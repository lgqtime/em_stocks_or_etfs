"""统一预测链路。

原则二：`predict(date)` 是唯一入口，回测只是"用历史日期调用同一个 predict"。
采集按 date 过滤：只取 date 当天 09:00 前发布的快讯，绝不读取晚于 date 的数据。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import time
from pathlib import Path

from . import configs
from .collect import news as news_mod
from .collect import overseas as overseas_mod
from .collect import quotes as quotes_mod
from .configs import (Item, Pool, ROOT, run_dir, write_json)
from .filter import hard
from .llm import prompts as P
from .llm.client import LLM
from .http import Client

# 下个模块的输入从哪来："memory" = 上游内存结果，"local" = 本地固定路径
SOURCE_LOG: dict[str, str] = {}

# 本轮运行遇到的问题。任何降级都要在这里留痕，不允许静默通过。
errors: list[str] = []
# 数据源固有限制等非本轮故障的说明。
notes: list[str] = []

# 档位强弱（与 config/tier_rules.json 的 tier_sets 一致，此处是代码侧常量）
STRONG_TIERS = {"P1", "P2", "C1", "C2", "E1", "E2"}
# 档位综合位次：同号时 P > C = E（政策优先）。与 hard.tier_rank 同一口径。
T_RANK = {f"{p}{i}": hard.tier_rank(f"{p}{i}") for p in "PCE" for i in (1, 2, 3, 4)}


def _quota_of(quota: dict, industry: str) -> int:
    return int(quota.get("by_industry", {}).get(industry, quota.get("default", 5)))


# --------------------------------------------------------------------------
# 输出契约
# --------------------------------------------------------------------------
@dataclasses.dataclass
class Decision:
    date: str
    action: str                       # "买入" | "空仓"
    kind: str = ""                    # "stock" | "etf" | ""
    code: str = ""
    name: str = ""
    industry: str = ""
    tier_basis: list[str] = dataclasses.field(default_factory=list)
    reason: str = ""
    audit_rounds: int = 0
    entry_price: float | None = None
    exit_price: float | None = None
    exit_date: str = ""
    return_pct: float | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------
# 上下文与落盘（原则一）
# --------------------------------------------------------------------------
class Ctx:
    def __init__(self, date: str, *, use_cache: bool = True, offline_ok: bool = False,
                 verbose: bool = False):
        self.date = date
        self.verbose = verbose
        self.stocks: Pool = configs.load_stocks()
        self.etfs: Pool = configs.load_etfs()
        self.blacklist = configs.load_blacklist()
        self.rules = configs.load_tier_rules()
        self.llm = LLM(use_cache=use_cache)
        self.http = Client()
        self.offline_ok = offline_ok
        self.t0 = time.time()
        self.meta: dict = {"date": date, "stages": {}, "inputs": {}}
        self.news: list[dict] = []
        self.quotes: list[dict] = []
        self.work: list[dict] = []
        self.periphery: list[dict] = []
        self.drops: dict[str, list[dict]] = {}
        self.industries: list[str] = []

    # --- 路径 -------------------------------------------------------------
    @property
    def dir(self) -> Path:
        return run_dir(self.date)

    def path(self, *parts: str) -> Path:
        p = self.dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def save(self, rel: str, obj) -> None:
        write_json(self.path(rel), obj)

    def load(self, rel: str):
        """原则一 ②：本地读取与内存传递走同一套加载器。"""
        return configs.read_local(self.date, rel)

    # --- 模块输入契据 ------------------------------------------------------
    def stage_input(self, module: str, upstream, loader):
        """run(ctx, settings, client, upstream=None) 的 upstream 语义。"""
        if upstream is not None:
            SOURCE_LOG[module] = "memory"
            return upstream
        SOURCE_LOG[module] = "local"
        try:
            return loader()
        except FileNotFoundError as e:
            raise RuntimeError(f"模块 {module} 既无上游内存结果，本地输入也不可用：{e}") from e

    def drop(self, stage: str, records: list[dict]) -> None:
        self.drops[stage] = records
        self.save(f"filtered/{stage}_dropped.json", [
            {k: v for k, v in r.items() if k in ("code", "showTime", "title", "category",
                                                 "industry", "drop_reason")}
            for r in records])

    def kept(self, stage: str, records: list[dict]) -> None:
        self.save(f"filtered/{stage}_kept.json",
                  [{k: v for k, v in r.items() if k in ("code", "title", "content",
                                                        "category", "industry", "summary",
                                                        "tier", "direction", "ts")}
                   for r in records])

    def mark(self, stage: str, **kw) -> None:
        self.meta["stages"][stage] = {"seconds": round(time.time() - self.t0, 1), **kw}
        if self.verbose:
            detail = " ".join(f"{k}={v}" for k, v in kw.items())
            print(f"  [{time.time() - self.t0:7.1f}s] {stage}  {detail}", flush=True)


def news_lines(items: list[dict], *, with_summary: bool = False) -> str:
    out = []
    for i, n in enumerate(items):
        head = f"[{i}] code={n['code']} {n.get('showTime') or _hhmm(n.get('ts'))}"
        if n.get("industry"):
            head += f" 行业={n['industry']}"
        if n.get("direction"):
            head += f" 方向={n['direction']}"
        if n.get("tier"):
            head += f" 档位={n['tier']}"
        out.append(f"{head}\n标题：{n.get('title', '')}")
        body = n.get("summary") or n.get("content") or ""
        if body and (with_summary or len(body) < 400):
            out.append(f"正文：{body[:600]}")
    return "\n".join(out)


def _hhmm(ts) -> str:
    """epoch → 'MM-DD HH:MM'。showTime 落盘时被过滤，用它兜底。"""
    import datetime as _dt
    if not ts:
        return ""
    return _dt.datetime.fromtimestamp(ts, news_mod.CST).strftime("%m-%d %H:%M")


def _index(res) -> dict:
    """把 LLM 结果按 code 建索引。"""
    try:
        data = res.json()
    except ValueError:
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    if items is None and isinstance(data, list):
        items = data
    return {str(x.get("code")): x for x in (items or []) if isinstance(x, dict)}


def _map(ctx: Ctx, agent: str, system: str, user_of, items: list, batch_size: int):
    """分批 + 并发调用；批次索引带进 tag，失败必须显式统计（【不得静默降级】）。"""
    batches = _batch(items, batch_size)
    results = ctx.llm.map(agent, system,
                          lambda b: user_of(b[1]),
                          [(i, b) for i, b in enumerate(batches)])
    # map 的 tag 由 LLM.map 用 enumerate 下标生成，这里补上阶段名前缀
    for i, r in enumerate(results):
        r.tag = f"{agent}:b{i}"
    failed = [r for r in results if r.error]
    if failed and len(failed) == len(results) and results:
        raise RuntimeError(
            f"{agent} 全部 {len(results)} 次调用失败，首个错误：{failed[0].error}")
    return results, len(failed)


def _short(res, n: int = 160) -> str:
    return (res.error or res.text or "")[:n]


def _call_each(ctx: Ctx, agent: str, groups: list, build):
    """对每组并发调用（组数少、单次耗时长，串行会拖垮 09:25 截止）。

    build(group) -> (system, user, tag)
    """
    if not groups:
        return []
    pairs = [build(g) for g in groups]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(ctx.llm.workers, len(pairs))) as ex:
        results = list(ex.map(
            lambda ip: ctx.llm.call(agent, ip[1][0], ip[1][1], tag=f"{ip[1][2]}#{ip[0]}"),
            list(enumerate(pairs))))
    for r in results:
        if r.error:
            errors.append(f"{agent} 批次失败：{_short(r)}")
    if results and all(r.error for r in results):
        raise RuntimeError(f"{agent} 全部 {len(results)} 批调用失败：{results[0].error}")
    return results


# --------------------------------------------------------------------------
# 采集
# --------------------------------------------------------------------------
def stage_collect(ctx: Ctx, *, refresh: bool = False) -> None:
    raw_news = ctx.path("raw/news.json")
    if raw_news.exists() and not refresh:
        ctx.news = json.loads(raw_news.read_text(encoding="utf-8"))
        src = "local"
    else:
        ctx.news = news_mod.collect_news(ctx.http, ctx.date)
        ctx.save("raw/news.json", ctx.news)
        src = "network"
    ctx.meta["inputs"]["news"] = src

    raw_q = ctx.path("raw/quotes.json")
    if raw_q.exists() and not refresh:
        ctx.quotes = json.loads(raw_q.read_text(encoding="utf-8"))
        qsrc = "local"
    elif ctx.date < dt.date.today().isoformat():
        # 回测：走历史源重建"T 日 09:00 前应看到的外盘"，而不是运行日的实时快照
        ctx.quotes = overseas_mod.collect_quotes_hist(ctx.http, ctx.date)
        ctx.save("raw/quotes.json", ctx.quotes)
        qsrc = "network:hist"
    else:
        # 生产：date 就是今天，实时快照即当夜行情
        ctx.quotes = quotes_mod.collect_quotes(ctx.http)
        ctx.save("raw/quotes.json", ctx.quotes)
        qsrc = "network:live"
    ctx.meta["inputs"]["quotes"] = qsrc

    missing = [q["name"] for q in ctx.quotes if q.get("missing")]
    roll = [q["name"] for q in ctx.quotes if q.get("reliability") == "roll_risk"]
    if qsrc == "network:hist":
        ctx.meta["inputs"]["quotes_provenance"] = (
            f"外盘为【历史重建】：取各指标在 {ctx.date} 09:00 前已收出的最后一根日线，"
            f"涨跌 = 该日收盘 / 前一日收盘 − 1（口径与实时源的昨收一致）。"
            + (f" ⚠️ 期货类为连续合约，换月处涨跌可能掺入价差：{'、'.join(roll)}。"
               if roll else "")
            + (f" ⚠️ 缺历史：{'、'.join(missing)}。" if missing else ""))
        if missing:
            notes.append(f"历史外盘有 {len(missing)} 项取不到：{'、'.join(missing)}")
        if roll:
            notes.append(f"历史外盘期货类存在换月价差风险：{'、'.join(roll)}")
    elif ctx.date != dt.date.today().isoformat():
        ctx.meta["inputs"]["quotes_provenance"] = (
            f"外盘指数为本地缓存（{len(ctx.quotes)} 项）——注意其原始采集时点")
    ctx.mark("collect", news=len(ctx.news), quotes=len(ctx.quotes),
             quotes_src=qsrc, quotes_missing=len(missing))


# --------------------------------------------------------------------------
# 各阶段
# --------------------------------------------------------------------------
def stage_f0(ctx: Ctx) -> list[dict]:
    kept, dropped = hard.hard_filter_0(ctx.news, ctx.blacklist)
    ctx.kept("f0", kept)
    ctx.drop("f0", dropped)
    ctx.mark("f0_原料清洗", 输入=len(ctx.news), 保留=len(kept), 剔除=len(dropped))
    return kept


def stage_agent1(ctx: Ctx, upstream) -> list[dict]:
    items = ctx.stage_input("agent1", upstream, lambda: ctx.load("filtered/f0_kept.json"))
    results, failed = _map(ctx, "agent1", P.agent1(),
                           lambda b: "分类下列快讯：\n" + news_lines(b), items, 60)
    idx: dict[str, dict] = {}
    for r in results:
        idx.update(_index(r))

    out, unknown = [], 0
    for n in items:
        rec = idx.get(n["code"])
        cat = (rec or {}).get("category", "")
        if cat not in hard.CATEGORIES:
            cat = "其他-杂/弱信号"
            unknown += 1
        out.append({**n, "category": cat, "cat_why": (rec or {}).get("why", "")})
    ctx.save("analysis/agent1_categories.json", out)
    ctx.save("analysis/agent1_categories.md", _md_agent1(out))
    if unknown:
        errors.append(f"agent1 有 {unknown}/{len(items)} 条未取到有效类目（已知失败批次 {failed}）")
    ctx.mark("agent1_分类", 输入=len(items), 输出=len(out), 无有效类目=unknown,
             批次=len(results), 失败批次=failed)
    return out


def stage_f1(ctx: Ctx, upstream) -> list[dict]:
    items = ctx.stage_input("f1", upstream,
                            lambda: ctx.load("analysis/agent1_categories.json"))
    kept, dropped = hard.hard_filter_1(items, list(ctx.stocks.by_name))
    ctx.kept("f1", kept)
    ctx.drop("f1", dropped)
    ctx.mark("f1_按类目剔除", 输入=len(items), 保留=len(kept), 剔除=len(dropped))
    return kept


def stage_agent2(ctx: Ctx, upstream) -> list[dict]:
    items = ctx.stage_input("agent2", upstream, lambda: ctx.load("filtered/f1_kept.json"))
    results, failed = _map(ctx, "agent2", P.AGENT2,
                           lambda b: "为下列快讯写摘要：\n" + news_lines(b, with_summary=True),
                           items, 60)
    idx: dict[str, dict] = {}
    for r in results:
        idx.update(_index(r))

    out, missing = [], 0
    for n in items:
        s = ((idx.get(n["code"]) or {}).get("summary") or "").strip()
        if not s:
            s = n.get("title", "")          # 【不得静默降级】：缺摘要回落标题并计数
            missing += 1
        out.append({**n, "summary": s})
    ctx.save("analysis/agent2_summaries.json", out)
    if missing:
        errors.append(f"agent2 有 {missing}/{len(items)} 条缺摘要，已回落标题（失败批次 {failed}）")
    ctx.mark("agent2_摘要", 输入=len(items), 缺摘要回落=missing, 失败批次=failed)
    return out


def stage_agent3(ctx: Ctx, upstream) -> list[dict]:
    items = ctx.stage_input("agent3", upstream,
                            lambda: ctx.load("analysis/agent2_summaries.json"))
    system = P.agent3(ctx.stocks.industry_map_text())
    results, failed = _map(ctx, "agent3", system,
                           lambda b: "为下列摘要指定唯一行业归属：\n"
                                     + "\n".join(f"[{i}] code={n['code']}\n摘要：{n['summary']}"
                                                 for i, n in enumerate(b)), items, 50)
    idx: dict[str, dict] = {}
    for r in results:
        idx.update(_index(r))

    valid = set(ctx.stocks.level1_names)
    out, other = [], 0
    for n in items:
        rec = idx.get(n["code"]) or {}
        ind = (rec.get("industry1") or "").strip()
        if ind not in valid and ind not in (hard.OTHER_INDUSTRY, hard.PERIPHERY_INDUSTRY):
            ind = hard.OTHER_INDUSTRY          # 归入池外行业一律落兜底桶，由第 2 关清洗
            other += 1
        out.append({**n, "industry": ind, "industry2": (rec.get("industry2") or "").strip(),
                    "ind_why": rec.get("why", "")})
    ctx.save("analysis/agent3_industries.json", out)
    ctx.save("analysis/agent3_industries.md", _md_agent3(out))
    if other:
        errors.append(f"agent3 有 {other}/{len(items)} 条未取到有效行业，已落「其他」"
                      f"（失败批次 {failed}）")
    ctx.mark("agent3_行业归属", 输入=len(items), 落到其他=other, 失败批次=failed)
    return out


def stage_f2(ctx: Ctx, upstream) -> list[dict]:
    items = ctx.stage_input("f2", upstream,
                            lambda: ctx.load("analysis/agent3_industries.json"))
    kept, dropped, industries = hard.hard_filter_2(items)
    ctx.kept("f2", kept)
    ctx.drop("f2", dropped)
    ctx.mark("f2_行业归属清洗", 输入=len(items), 保留=len(kept), 剔除=len(dropped),
             有消息行业=len(industries))
    ctx.industries = industries
    return kept


def stage_agent4(ctx: Ctx, upstream) -> list[dict]:
    items = ctx.stage_input("agent4", upstream, lambda: ctx.load("filtered/f2_kept.json"))
    ind = [n for n in items if n.get("industry") != hard.PERIPHERY_INDUSTRY]
    per = [n for n in items if n.get("industry") == hard.PERIPHERY_INDUSTRY]
    out: dict[str, dict] = {}

    groups = _batch(ind, 25)
    results = _call_each(ctx, "agent4", groups, lambda g: (
        P.agent4("、".join(sorted({n["industry"] for n in g}))),
        "为下列行业消息判级：\n" + "\n".join(
            f"[{i}] code={n['code']} 行业={n['industry']}\n标题：{n['title']}\n摘要：{n['summary']}"
            for i, n in enumerate(g)), "agent4:industry"))
    for r in results:
        out.update(_index(r))

    results = _call_each(ctx, "agent4", _batch(per, 15), lambda g: (
        P.agent4("外围"),
        "为下列外围消息标记唯一行业与方向（不评级）：\n" + "\n".join(
            f"[{i}] code={n['code']}\n标题：{n['title']}\n摘要：{n['summary']}"
            for i, n in enumerate(g)), "agent4:periphery"))
    for r in results:
        out.update(_index(r))

    return _apply_tiers(ctx, items, out, stage="agent4")


def stage_agent42(ctx: Ctx, upstream) -> list[dict]:
    items = ctx.stage_input("agent42", upstream, lambda: ctx.load("analysis/agent4_tiers.json"))
    out: dict[str, dict] = {}
    results = _call_each(ctx, "agent42", _batch(items, 25), lambda g: (
        P.agent42(),
        "复核下列判级：\n" + "\n".join(
            f"[{i}] code={n['code']} 行业={n['industry']} "
            f"档位={n.get('tier') or '（外围无档位）'} "
            f"方向={n.get('direction') or '（无）'}\n"
            f"标题：{n['title']}\n摘要：{n['summary']}"
            for i, n in enumerate(g)), "agent42"))
    for r in results:
        out.update(_index(r))

    kept, changed = [], 0
    for n in items:
        rec = out.get(n["code"]) or {}
        is_per = n.get("industry") == hard.PERIPHERY_INDUSTRY
        tier = n.get("tier", "")
        direction = n.get("direction", "")
        if is_per:
            d = (rec.get("direction") or "").strip()
            if d in P.DIRECTIONS:
                direction = d
        else:
            t = (rec.get("tier") or "").strip()
            if t in _ALL_TIERS:
                tier = t
        changed += int(bool(rec.get("changed")))
        kept.append({**n, "tier": tier, "direction": direction,
                     "ts": n.get("ts", 0),
                     "a42_why": rec.get("why", ""),
                     "a42_removed": False})        # 审查层不得移除任何消息
    ctx.save("analysis/agent42_tiers.json", kept)
    ctx.mark("agent42_档位审查", 输入=len(items), 输出=len(kept), 有调整=changed)
    return kept


_ALL_TIERS = [f"{p}{i}" for p in "PCE" for i in (1, 2, 3, 4)]


def _apply_tiers(ctx: Ctx, items: list[dict], idx: dict[str, dict], *, stage: str) -> list[dict]:
    out = []
    for n in items:
        rec = idx.get(n["code"]) or {}
        is_per = n.get("industry") == hard.PERIPHERY_INDUSTRY
        tier = (rec.get("tier") or "").strip()
        direction = (rec.get("direction") or "").strip()
        if is_per:
            tier = ""
            if direction not in P.DIRECTIONS:
                direction = "方向依存"          # 四值兜底，避免丢方向
        else:
            direction = ""
            if tier not in _ALL_TIERS:
                tier = "C3"                     # 判级失败回落弱档，不静默丢弃
        out.append({**n, "tier": tier, "direction": direction,
                    # ts 必须在业务字段里（落盘时保留 showTime 会被过滤，排序要用它）
                    "ts": n.get("ts", 0), "a4_why": rec.get("why", "")})
    ctx.save(f"analysis/{stage}_tiers.json", out)
    ctx.mark(f"{stage}_判级", 输入=len(items), 输出=len(out))
    return out


def stage_f3(ctx: Ctx, upstream) -> tuple[list[dict], list[str], list[dict]]:
    """第 3 关：推举 5 个行业。外围不参与本关排序，也不作为同分决胜键。"""
    items = ctx.stage_input("f3", upstream, lambda: ctx.load("analysis/agent42_tiers.json"))
    ind = [n for n in items if n.get("industry") != hard.PERIPHERY_INDUSTRY]
    per = [n for n in items if n.get("industry") == hard.PERIPHERY_INDUSTRY]
    order = [i for i in ctx.industries if any(n["industry"] == i for n in ind)]
    tiers = {i: [n["tier"] for n in ind if n["industry"] == i] for i in order}
    ranked = hard.rank_industries(tiers, order)
    top = [r["industry"] for r in ranked[:5]]
    ctx.save("filtered/f3_ranking.json", ranked)
    ctx.save("filtered/f3_selected.json", {"selected": top, "periphery_count": len(per)})
    ctx.mark("f3_行业推举", 候选行业=len(order), 入选=len(top), 入选名单=top)
    return ind, per, top


def _rank_key(n: dict) -> tuple:
    """档位排序键：强档优先 → 同档位内时间倒序（越新越靠前）。"""
    rank = T_RANK.get(n.get("tier", ""), 99)
    return (0 if n.get("tier") in STRONG_TIERS else 1, rank, -n.get("ts", 0))


def _cap(items: list[dict], limit: int) -> list[dict]:
    """按排序键取前 limit 条。允许不满，绝不为了凑数补低相关消息。"""
    return sorted(items, key=_rank_key)[:limit]


def stage_agent5(ctx: Ctx, upstream, top: list[str]) -> tuple[list[dict], str, dict]:
    """证据复审：Agent5 做相关性去重，【条数配额由代码强制】（不靠模型自觉）。

    - 所有行业（含外围）一律去重，强档不豁免。
    - 行业上限：电子 8，其余 5（config/tier_rules.json → summary_quota）。
    - 外围按方向严格分桶（利好≤3 / 利空≤3 / 中性≤2 / 方向依存≤2），允许不满。
    - 外盘指数（quotes）独立传递，不占外围消息配额。
    """
    a42 = ctx.stage_input("agent5", upstream, lambda: ctx.load("analysis/agent42_tiers.json"))
    ind = [n for n in a42 if n.get("industry") != hard.PERIPHERY_INDUSTRY
           and n.get("industry") in top]
    per = [n for n in a42 if n.get("industry") == hard.PERIPHERY_INDUSTRY]
    quota = ctx.rules["summary_quota"]
    buckets = ctx.rules["periphery"]["summary_buckets"]

    parts = ["【入选行业的全部消息】"]
    for name in top:
        group = [n for n in ind if n["industry"] == name]
        parts.append(f"\n行业：{name}（{len(group)} 条，上限 {_quota_of(quota, name)} 条）")
        for n in group:
            parts.append(f"- code={n['code']} 档位={n['tier']} {n['title']}")
            parts.append(f"  摘要：{n['summary']}")
    parts.append("\n【外围消息（不参与行业排名，按方向分桶去重）】")
    for n in per:
        parts.append(f"- code={n['code']} 方向={n['direction']} {n['title']}")
        parts.append(f"  摘要：{n['summary']}")

    res = ctx.llm.call("agent5", P.agent5(), "\n".join(parts), tag="agent5")
    try:
        data = res.json()
    except ValueError:
        data = {"industries": [], "periphery_note": "", "_error": res.error or "解析失败"}
        errors.append(f"agent5 输出无法解析，退化为纯代码去重：{_short(res)}")

    by_code = {n["code"]: n for n in ind + per}

    # Agent5 的去重结论：只采信它对"应保留哪些"的筛选，条数仍由代码裁剪
    survivor: set[str] = set()
    for blk in data.get("industries", []):
        for c in blk.get("keep", []) or blk.get("kept", []):
            if str(c) in by_code:
                survivor.add(str(c))
    for c in data.get("periphery_keep", []) or []:
        if str(c) in by_code:
            survivor.add(str(c))
    if not survivor:                      # 解析失败/漏答 → 全量进入代码裁剪，不丢证据
        survivor = set(by_code)

    # 保险：若 Agent5 把某条强档去掉了、而它判定的"重复保留项"是弱档，
    # 则改留强档那条 —— 相关性去重不该把更强的证据换成更弱的。
    for name in top:
        group = [n for n in ind if n["industry"] == name]
        for n in group:
            if n["tier"] not in STRONG_TIERS or n["code"] in survivor:
                continue
            dropped = next((d for blk in data.get("industries", [])
                            if blk.get("industry") == name
                            for d in (blk.get("dropped") or [])
                            if str(d.get("code")) == n["code"]), None)
            dup = str((dropped or {}).get("dup_of") or "")
            if dup and dup in by_code and by_code[dup].get("tier") not in STRONG_TIERS:
                survivor.discard(dup)
                survivor.add(n["code"])

    # 代码裁剪：行业按各自上限，外围按方向桶上限
    final: set[str] = set()
    per_by_dir: dict[str, list[dict]] = {}
    for n in per:
        per_by_dir.setdefault(n.get("direction") or "中性", []).append(n)
    for direction, group in per_by_dir.items():
        limit = buckets.get(direction, buckets.get("中性", 2))
        final.update(n["code"] for n in _cap(group, limit))
    for name in top:
        group = [n for n in ind if n["industry"] == name and n["code"] in survivor]
        final.update(n["code"] for n in _cap(group, _quota_of(quota, name)))

    selected = [by_code[c] for c in sorted(final) if c in by_code]
    ctx.save("analysis/agent5_selected.json", {
        "industries": data.get("industries", []),
        "periphery_note": data.get("periphery_note", ""),
        "kept_codes": sorted(final),
        "quotas": {"by_industry": {n: _quota_of(quota, n) for n in top},
                   "periphery_buckets": buckets},
        "periphery_counts": {d: sum(1 for x in g if x["code"] in final)
                             for d, g in per_by_dir.items()},
    })
    ctx.save("analysis/agent5_selected.md", _md_agent5(top, by_code, final, data, quota, buckets))
    ctx.mark("agent5_证据复审", 入选行业=top, 上游条数=len(ind) + len(per), 精选条数=len(final),
             按行业={n: sum(1 for x in selected if x.get("industry") == n) for n in top},
             外围按方向={d: sum(1 for x in selected if x.get("industry") == hard.PERIPHERY_INDUSTRY
                              and (x.get("direction") or "中性") == d)
                     for d in buckets})
    return selected, data.get("periphery_note", ""), data


def _ctx_parts(ctx: Ctx, selected: list[dict], top: list[str], per_note: str):
    """给决策/审计的两块上下文，严格分开：

    · 外盘指数：独立的行情参数，原样传下游，不占外围消息配额、不参与行业排序
    · 外围消息：Agent5 按方向分桶去重后的原文
    """
    evidence = _evidence_text(selected, top)
    per = [n for n in selected if n.get("industry") == hard.PERIPHERY_INDUSTRY]
    periphery = (f"【外盘指数（独立行情参数，非消息、不评级、不参与合成计数）】\n"
                 f"{quotes_mod.quotes_text(ctx.quotes)}\n\n"
                 f"【外围消息（已按方向分桶去重，共 {len(per)} 条）】\n"
                 + (news_lines(per, with_summary=True) if per else "（无）")
                 + f"\n外围整理结论：{per_note}")
    return evidence, periphery


def stage_agent6(ctx: Ctx, upstream, top: list[str], per: list[dict],
                 per_note: str) -> dict:
    selected = ctx.stage_input("agent6", upstream,
                               lambda: ctx.load("analysis/agent5_selected.json"))
    pool = ctx.stocks
    cand = pool.candidates_text(top)
    evidence, periphery = _ctx_parts(ctx, selected, top, per_note)
    return _decide(ctx, "agent6", P.agent6(cand), pool, evidence, periphery, top)


def stage_agent62(ctx: Ctx, upstream, top: list[str], per: list[dict], per_note: str) -> dict:
    selected = ctx.stage_input("agent62", upstream,
                               lambda: ctx.load("analysis/agent5_selected.json"))
    pool = ctx.etfs
    cand = pool.candidates_text(top)
    if not cand.strip():
        return {"decision": "空仓", "reason": "所选行业在 ETF 池中无候选标的", "code": "",
                "name": "", "industry": "", "tier_basis": [], "rounds": 0}
    evidence, periphery = _ctx_parts(ctx, selected, top, per_note)
    return _decide(ctx, "agent62", P.agent62(cand), pool, evidence, periphery, top)


def _evidence_text(selected: list[dict], top: list[str]) -> str:
    parts = []
    for name in top:
        group = [n for n in selected if n.get("industry") == name]
        parts.append(f"\n行业：{name}（{len(group)} 条精选）")
        for n in group:
            parts.append(f"- [{n['tier']}] {n['title']}")
            parts.append(f"  摘要：{n['summary']}（code={n['code']}）")
    return "\n".join(parts)


def _decide(ctx: Ctx, agent: str, system: str, pool: Pool,
            evidence: str, periphery: str, top: list[str]) -> dict:
    """决策 + 审计回退（最多 3 轮）。3 轮仍不认可 → 空仓。"""
    schema = (f"【精选证据】\n{evidence}\n\n【外围（外盘指数 + 外围消息原文）】\n{periphery}\n\n"
              f"入选的 5 个行业：{'、'.join(top)}")
    history: list[str] = []
    rejected: list[dict] = []
    prev = None
    for rnd in range(1, 4):
        if rnd == 1:
            res = ctx.llm.call(agent, system, schema, tag=f"{agent}:r{rnd}")
        else:
            res = ctx.llm.call(agent, system,
                               schema + "\n\n" + P.retry(
                                   rejected[-1].get("gap_type", ""),
                                   rejected[-1].get("gap_detail", ""),
                                   rejected[-1].get("what_would_change_my_mind", ""),
                                   json.dumps(prev, ensure_ascii=False))
                               + "\n注意：本次必须换一个论证角度，不得与原方案雷同。",
                               tag=f"{agent}:r{rnd}")
        try:
            plan = res.json()
        except ValueError:
            plan = {"decision": "空仓", "reason": f"决策输出无法解析：{res.error or '格式错误'}"}

        # 代码侧硬约束：标的必须能在池中按代码或名称找到
        if plan.get("decision") == "买入":
            item = pool.find(plan.get("code") or "") or pool.find(plan.get("name") or "")
            if item is None:
                plan = {**plan, "decision": "空仓",
                        "reason": f"所推举标的（{plan.get('code')} {plan.get('name')}）不在候选池内 → 空仓",
                        "_invalid": True}
            else:
                plan = {**plan, "code": item.code, "name": item.name}

        aud = ctx.llm.call("audit", P.audit(P.gap_enum(), "、".join(history) or "无"),
            f"{schema}\n\n【待审方案】\n{json.dumps(plan, ensure_ascii=False, indent=1)}",
            tag=f"audit:r{rnd}")
        try:
            verdict = aud.json()
        except ValueError:
            verdict = {"verdict": "不认可", "gap_type": "证据缺失",
                       "gap_detail": f"审计输出无法解析：{aud.error or '格式错误'}",
                       "what_would_change_my_mind": "重试", "periphery_view": ""}

        if verdict.get("verdict") == "认可":
            return {**plan, "rounds": rnd, "audit": verdict, "audit_history": rejected}
        gap = verdict.get("gap_type") or "证据缺失"
        if gap in history:                       # 保险：每轮 gap_type 必须不同
            verdict = {**verdict, "gap_type": gap,
                       "_strike": "本轮 gap_type 与历史同类 → 直接空仓"}
            rejected.append(verdict)
            return {"decision": "空仓", "reason": "审计保险触发：连续同类否定理由 → 空仓",
                    "rounds": rnd, "audit": verdict, "audit_history": rejected,
                    "code": "", "name": "", "industry": "", "tier_basis": []}
        history.append(gap)
        rejected.append(verdict)
        prev = plan
    return {"decision": "空仓", "reason": "3 轮审计均不认可 → 空仓",
            "rounds": 3, "audit": rejected[-1] if rejected else {}, "audit_history": rejected,
            "code": "", "name": "", "industry": "", "tier_basis": []}


# --------------------------------------------------------------------------
# 主入口：唯一预测接口（原则二）
# --------------------------------------------------------------------------
def predict(date: str, *, refresh: bool = False, use_cache: bool = True,
            verbose: bool = False) -> Decision:
    errors.clear()
    notes.clear()
    SOURCE_LOG.clear()
    ctx = Ctx(date, use_cache=use_cache, verbose=verbose)
    t0 = time.time()
    if verbose:
        print(f"[predict] {date} cache={use_cache} refresh={refresh}", flush=True)

    stage_collect(ctx, refresh=refresh)

    f0 = stage_f0(ctx)
    a1 = stage_agent1(ctx, f0)
    f1 = stage_f1(ctx, a1)
    a2 = stage_agent2(ctx, f1)
    a3 = stage_agent3(ctx, a2)
    f2 = stage_f2(ctx, a3)
    a4 = stage_agent4(ctx, f2)
    a42 = stage_agent42(ctx, a4)
    ind, per, top = stage_f3(ctx, a42)
    selected, per_note, _ = stage_agent5(ctx, None, top)

    if not top:
        decision = Decision(date=date, action="空仓", reason="当日无任何行业有快讯归属 → 空仓")
        ctx.save("analysis/agent6_decision.json", decision.to_dict())
    else:
        stock = stage_agent6(ctx, selected, top, per, per_note)
        ctx.save("analysis/agent6_decision.json", stock)
        if stock.get("decision") == "买入":
            decision = Decision(date=date, action="买入", kind="stock",
                                code=stock.get("code", ""), name=stock.get("name", ""),
                                industry=stock.get("industry", ""),
                                tier_basis=stock.get("tier_basis", []),
                                reason=stock.get("reason", ""),
                                audit_rounds=stock.get("rounds", 0))
        else:
            etf = stage_agent62(ctx, selected, top, per, per_note)
            ctx.save("analysis/agent62_decision.json", etf)
            decision = Decision(
                date=date,
                action="买入" if etf.get("decision") == "买入" else "空仓",
                kind="etf" if etf.get("decision") == "买入" else "",
                code=etf.get("code", ""), name=etf.get("name", ""),
                industry=etf.get("industry", ""), tier_basis=etf.get("tier_basis", []),
                reason=f"[股票层空仓：{stock.get('reason', '')}] ETF层：{etf.get('reason', '')}",
                audit_rounds=etf.get("rounds", 0))

    ctx.meta["inputs"].update(SOURCE_LOG)
    ctx.meta["version"] = "v2.0"
    ctx.meta["usage"] = ctx.llm.usage()
    ctx.meta["elapsed_seconds"] = round(time.time() - t0, 1)
    ctx.meta["selected_industries"] = top
    ctx.meta["errors"] = errors
    ctx.meta["notes"] = notes
    ctx.save("meta.json", ctx.meta)
    ctx.save("analysis/decision.json", decision.to_dict())
    if errors:
        print(f"[warn] 本轮有 {len(errors)} 处降级/失败，详见 meta.json:errors")
        for e in errors[:8]:
            print(f"       - {e}")
    for n in notes:
        print(f"[note] {n}")
    return decision


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _batch(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)] or []


def _md_agent1(items: list[dict]) -> str:
    lines = ["# Agent1 分类结果", ""]
    for n in items:
        lines.append(f"- `{n['code']}` **{n['category']}** ｜ {n['title']}")
    return "\n".join(lines)


def _md_agent3(items: list[dict]) -> str:
    lines = ["# Agent3 行业归属", ""]
    for n in items:
        lines.append(f"- `{n['code']}` **{n['industry']}**"
                     f"{'/' + n['industry2'] if n.get('industry2') else ''} ｜ {n['title']}")
    return "\n".join(lines)


def _md_agent5(top, by_code, kept_codes, data, quota=None, buckets=None) -> str:
    quota = quota or {"default": 5, "by_industry": {}}
    buckets = buckets or {}
    lines = ["# Agent5 证据精选（条数由代码强制）", ""]
    for name in top:
        group = [c for c in kept_codes
                 if by_code[c]["industry"] == name]
        lines.append(f"## {name}（{len(group)}/{_quota_of(quota, name)} 条）")
        for c in sorted(group, key=lambda x: _rank_key(by_code[x])):
            n = by_code[c]
            lines.append(f"- [{n['tier']}] {n['title']}")
        lines.append("")
    per = [c for c in kept_codes if by_code[c]["industry"] == hard.PERIPHERY_INDUSTRY]
    if per or buckets:
        lines.append(f"## 外围（{len(per)}/{sum(buckets.values())} 条，按方向分桶）")
        for c in sorted(per, key=lambda x: _rank_key(by_code[x])):
            n = by_code[c]
            lines.append(f"- [{n.get('direction') or '中性'}] {n['title']}")
        lines.append("")
    lines.append(f"外围结论：{data.get('periphery_note', '')}")
    return "\n".join(lines)
