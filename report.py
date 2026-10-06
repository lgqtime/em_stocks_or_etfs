"""决策报告生成器：把一次运行的全部落盘产物整理成一份可读报告。

    python report.py 2026-09-10 [2026-09-11 ...]

只读 runs/<日期>/ 下的产物，不调用任何模型。
"""

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from src import configs

ROOT = Path(__file__).resolve().parent
CST = dt.timezone(dt.timedelta(hours=8))
# 官方单价 元/百万token（高峰 / 空闲），见 api-docs.deepseek.com/zh-cn/quick_start/pricing
PRICE = {"flash": {"miss": (1.0, 2.0), "out": (4.0, 8.0), "hit": (0.02, 0.04)},
         "pro": {"miss": (4.5, 9.0), "out": (13.5, 27.0), "hit": (0.15, 0.30)}}
MODEL_OF = {"agent1": "flash", "agent2": "flash", "agent3": "pro", "agent4": "pro",
            "agent42": "pro", "agent5": "pro", "agent6": "pro", "agent62": "pro",
            "audit": "pro"}
M = 1_000_000

DROP_LABEL = {
    "f0_原料清洗": "第0关 原料清洗（黑名单/超长/空内容/会议预告/标题去重）",
    "f1_按类目剔除": "第1关 按类目剔除（整类剔除 + 公司合作须命中个股池）",
    "f2_行业归属清洗": "第2关 行业归属清洗（无快讯归属的行业 / 其他行业）",
}


def load(date, *parts, default=None):
    p = ROOT / "runs" / date / Path(*parts)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def cost_of(usage) -> tuple[float, float]:
    peak = slack = 0.0
    for tag, v in usage.get("by_tag", {}).items():
        agent = tag.split(":")[0].split("#")[0]
        m = MODEL_OF.get(agent, "pro")
        p = PRICE[m]
        # LLM 缓存命中（我们自己的内容缓存）不计费；这里按"全部对话缓存未命中"上界估
        peak += v["prompt"] / M * p["miss"][0] + v["completion"] / M * p["out"][0]
        slack += v["prompt"] / M * p["miss"][1] + v["completion"] / M * p["out"][1]
    return peak, slack


def report(date: str) -> str:
    meta = load(date, "meta.json", default={})
    dec = load(date, "analysis/decision.json", default={})
    a6 = load(date, "analysis/agent6_decision.json", default={})
    a62 = load(date, "analysis/agent62_decision.json")
    a5 = load(date, "analysis/agent5_selected.json", default={})
    a42 = load(date, "analysis/agent42_tiers.json", default=[])
    ranking = load(date, "filtered/f3_ranking.json", default=[])
    sel = load(date, "filtered/f3_selected.json", default={})
    news = load(date, "raw/news.json", default=[])
    quotes = load(date, "raw/quotes.json", default=[])

    by_code = {n["code"]: n for n in a42}
    top = meta.get("selected_industries", [])
    kept = set(a5.get("kept_codes", []))
    quotas = a5.get("quotas", {})
    usage = meta.get("usage", {})
    peak, slack = cost_of(usage)

    L = []
    A = L.append
    metaf = ROOT / "runs" / date / "meta.json"
    run_at = (dt.datetime.fromtimestamp(metaf.stat().st_mtime, CST)
              if metaf.exists() else dt.datetime.now(CST))
    A(f"# {date} 决策报告")
    A("")
    A(f"> 运行时间 {run_at:%Y-%m-%d %H:%M}（北京时间）　"
      f"耗时 {meta.get('elapsed_seconds', 0) / 60:.1f} 分钟　"
      f"版本 {meta.get('version', '-')}　"
      f"代码留档 {sorted(p.name for p in (ROOT / 'releases').glob('v*'))[-1] if (ROOT / 'releases').exists() else '-'}")
    A("")

    # ---------- 结论 ----------
    A("## 一、结论")
    A("")
    act = dec.get("action", "?")
    if act == "买入":
        kind = {"stock": "股票", "etf": "ETF"}.get(dec.get("kind"), dec.get("kind"))
        A(f"**{act}**　{kind}　`{dec.get('code')}` **{dec.get('name')}**　"
          f"行业：{dec.get('industry')}　档位依据：{'、'.join(dec.get('tier_basis') or []) or '—'}")
        A("")
        A(f"- 执行口径：**{date} 开盘买入，下一交易日开盘卖出**")
        A(f"- 审计回退轮次：{dec.get('audit_rounds', 0)}")
    else:
        A(f"**{act}**（空仓是正常结论）")
        A("")
    A("")
    A("### 决策理由")
    A("")
    A(dec.get("reason", "（无）"))
    A("")

    # ---------- 复盘收益 ----------
    ev = None
    if act == "买入" and dec.get("code"):
        try:
            from src.http import Client
            from src import market
            ev = market.evaluate(Client(), dec["code"], date)
        except Exception as e:  # noqa: BLE001
            ev = {"error": f"{type(e).__name__}: {e}"}
    if ev:
        A("### 事后复盘（T 日开盘买 → T+1 日开盘卖）")
        A("")
        if ev.get("error"):
            A(f"取价失败：{ev['error']}")
        else:
            A(f"| 项 | 值 |")
            A(f"|---|---|")
            A(f"| 买入价（{ev['entry_date']} 开盘） | {ev['entry_price']} |")
            A(f"| 卖出价（{ev['exit_date']} 开盘） | {ev['exit_price']} |")
            A(f"| **收益** | **{ev['return_pct']:+.3f}%** |")
        A("")

    # ---------- 第3关 ----------
    A("## 二、入选行业（第3关，纯代码排序）")
    A("")
    A("排序键：有无强档 → 强档数量 → 最高档位 → 弱档数量（越多越靠前）→ 既有顺序。"
      "**外围不参与本关**。")
    A("")
    A("| 名次 | 行业 | 强档数 | 最高档 | 弱档数 | 全部档位 | 入选 |")
    A("|---:|---|---:|---|---:|---|---|")
    for i, r in enumerate(ranking, 1):
        best = {1: "P1/C1/E1", 2: "P2/C2/E2", 3: "P3/C3/E3", 4: "P4/C4/E4"}.get(r["best_rank"], "—")
        mark = "✅" if r["industry"] in top else ""
        A(f"| {i} | {r['industry']} | {r['strong_n']} | {best} | {r['weak_n']} | "
          f"{'/'.join(r['tiers']) or '—'} | {mark} |")
    A("")
    A(f"外围消息 {sel.get('periphery_count', 0)} 条（不参与排序，按方向分桶后传下游）")
    A("")

    # ---------- 证据 ----------
    A("## 三、进入下游的证据（配额由代码强制，允许不满）")
    A("")
    if a5.get("_error") or (not kept):
        A(f"⚠️ Agent5 输出异常：{a5.get('_error') or '无保留条目'}")
        A("")
    for name in top:
        g = [c for c in kept if by_code.get(c, {}).get("industry") == name]
        lim = quotas.get("by_industry", {}).get(name, 5)
        A(f"### {name}（{len(g)}/{lim} 条）")
        A("")
        for c in sorted(g, key=lambda x: (0 if by_code[x]["tier"] in ("P1", "P2", "C1", "C2", "E1", "E2") else 1,
                                          by_code[x]["tier"], -by_code[x].get("ts", 0))):
            n = by_code[c]
            A(f"- **[{n['tier']}]** {n['title']}")
            A(f"  - 摘要：{n['summary']}")
            A(f"  - `{c}`　行业：{n['industry']}")
            if n.get("a42_why") and n["a42_why"] not in ("维持", ""):
                A(f"  - Agent42 调整：{n['a42_why']}")
        A("")
    per = [c for c in kept if by_code.get(c, {}).get("industry") == "外围"]
    A(f"### 外围消息（{len(per)}/{sum(quotas.get('periphery_buckets', {}).values()) or 8} 条，按方向分桶）")
    A("")
    buckets = {}
    for c in per:
        buckets.setdefault(by_code[c].get("direction") or "中性", []).append(c)
    for d, cs in buckets.items():
        A(f"**{d}**（{len(cs)} 条）")
        for c in cs:
            n = by_code[c]
            A(f"- {n['title']}")
            A(f"  - 摘要：{n['summary']}")
        A("")
    if a5.get("periphery_note"):
        A(f"外围整理结论：{a5['periphery_note']}")
        A("")

    # ---------- 外盘 ----------
    A("## 四、外盘指数（独立行情参数，不评级、不占配额）")
    A("")
    A("| 指标 | 类别 | 现价 | 涨跌 | 昨收 | 今开 | 时间 |")
    A("|---|---|---:|---:|---:|---:|---|")
    for q in quotes:
        pct = f"{q['pct']:+.2f}%" if q.get("pct") is not None else "—"
        A(f"| {q['name']} | {q['kind']} | {q.get('price')} | {pct} | "
          f"{q.get('prev_close')} | {q.get('open')} | {q.get('time') or '—'} |")
    A("")
    prov = meta.get("inputs", {}).get("quotes_provenance")
    if prov:
        A(f"> ⚠️ {prov}")
        A("")

    # ---------- 审计 ----------
    A("## 五、审计与回退")
    A("")
    # 只打本轮【真正跑过】的那一层。判据用产物时间戳，而不是 kind：
    #   · 股票层买入 → Agent62 根本没启动，旧轮次遗留的 agent62_decision.json 必须忽略
    #   · 股票层空仓 → 走 ETF 层，两层都要打
    def _mt(p):
        f = ROOT / "runs" / date / p
        return f.stat().st_mtime if f.exists() else 0

    ran62 = _mt("analysis/agent62_decision.json") >= _mt("analysis/agent6_decision.json") > 0
    layers = [("股票层 Agent6" + ("（判定空仓，已进入 ETF 层）" if ran62 else ""), a6)]
    if ran62:
        layers.append(("ETF 层 Agent62", a62))
    for label, blk in layers:
        if not blk:
            continue
        A(f"### {label}")
        A("")
        A(f"- 决策：**{blk.get('decision')}**"
          + (f"　标的：`{blk.get('code')}` {blk.get('name')}" if blk.get("decision") == "买入" else ""))
        if blk.get("industry"):
            A(f"- 依据行业：{blk['industry']}　档位：{'、'.join(blk.get('tier_basis') or []) or '—'}")
        A(f"- 审计轮次：{blk.get('rounds', 0)}")
        if blk.get("invalidation"):
            A(f"- 推翻条件：{blk['invalidation']}")
        A("")
        hist = blk.get("audit_history") or []
        if hist:
            A(f"**审计未认可记录（{len(hist)} 次）**")
            A("")
            for i, h in enumerate(hist, 1):
                A(f"{i}. `gap_type = {h.get('gap_type', '—')}`")
                A(f"   - 缺口：{h.get('gap_detail', '—')}")
                A(f"   - 改变结论的条件：{h.get('what_would_change_my_mind', '—')}")
                if h.get("periphery_view"):
                    A(f"   - 外围独立评估：{h['periphery_view']}")
                A("")
        av = blk.get("audit") or {}
        if av:
            A(f"**最终审计意见：{av.get('verdict', '—')}**")
            if av.get("periphery_view"):
                A(f"- 外围独立评估：{av['periphery_view']}")
            if av.get("what_would_change_my_mind"):
                A(f"- 改变结论的条件：{av['what_would_change_my_mind']}")
            A("")
        A("**Agent6/62 的详细理由**")
        A("")
        A(blk.get("reason", "（无）"))
        A("")

    # ---------- 过滤 ----------
    A("## 六、三关硬过滤（纯代码，可逐条审计）")
    A("")
    A("| 关卡 | 输入 | 保留 | 剔除 |")
    A("|---|---:|---:|---:|")
    for key, label in DROP_LABEL.items():
        st = meta.get("stages", {}).get(key, {})
        A(f"| {label} | {st.get('输入', '—')} | {st.get('保留', '—')} | {st.get('剔除', '—')} |")
    A("")
    reasons = Counter()
    for key in ("f0", "f1", "f2"):
        for d in load(date, "filtered", f"{key}_dropped.json", default=[]) or []:
            reasons[d.get("drop_reason", "?")] += 1
    if reasons:
        A("**剔除原因分布**")
        A("")
        A("| 原因 | 条数 |")
        A("|---|---:|")
        for k, v in reasons.most_common(15):
            A(f"| {k} | {v} |")
        A("")

    # ---------- 运行 ----------
    A("## 七、运行信息")
    A("")
    A("| 阶段 | 耗时(秒) | 计数 |")
    A("|---|---:|---|")
    for k, v in meta.get("stages", {}).items():
        sec = v.get("seconds", 0)
        detail = "　".join(f"{kk}={vv}" for kk, vv in v.items() if kk != "seconds")
        A(f"| {k} | {sec} | {detail} |")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| 墙钟总耗时 | {meta.get('elapsed_seconds', 0):.0f} 秒（{meta.get('elapsed_seconds', 0) / 60:.1f} 分钟） |")
    A(f"| LLM 调用 | {usage.get('calls', 0)} 次（内容缓存命中 {usage.get('cached', 0)}、失败 {usage.get('errors', 0)}） |")
    A(f"| 输入 token | {usage.get('prompt_tokens', 0):,} |")
    A(f"| 输出 token | {usage.get('completion_tokens', 0):,} |")
    A(f"| 估算成本 | 高峰 ¥{peak:.2f} / 空闲时段 ¥{slack:.2f} |")
    A(f"| 原始快讯 | {len(news)} 条 |")
    A("")
    A("**各模块输入来源**（memory=上游内存，local=本地固定路径，network=本次采集）")
    A("")
    A("| 模块 | 来源 |")
    A("|---|---|")
    for k, v in sorted(meta.get("inputs", {}).items()):
        A(f"| {k} | {v} |")
    A("")
    errs = meta.get("errors") or []
    notes = meta.get("notes") or []
    if errs or notes:
        A("**降级与失败留痕**")
        A("")
        for e in errs:
            A(f"- ⚠️ {e}")
        for n in notes:
            A(f"- ℹ️ {n}")
        A("")
    else:
        A("本轮无降级与失败。")
        A("")
    if a5.get("periphery_counts"):
        A(f"外围分桶实际条数：{a5['periphery_counts']}")
        A("")
    # 配额越界自检：报告里必须能一眼看出有没有超限
    over = []
    for name in top:
        n = sum(1 for c in kept if by_code.get(c, {}).get("industry") == name)
        lim = quotas.get("by_industry", {}).get(name, 5)
        if n > lim:
            over.append(f"{name} {n}>{lim}")
    pb = quotas.get("periphery_buckets", {})
    pc = a5.get("periphery_counts", {})
    for d, n in pc.items():
        if n > pb.get(d, 2):
            over.append(f"外围{d} {n}>{pb.get(d, 2)}")
    A(f"**配额越界自检：{'❌ ' + '；'.join(over) if over else '✅ 无越界'}**")
    A("")
    return "\n".join(L)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        # 之前这里静默回退到某个写死的日期 —— 会把"忘了给参数"变成
        # "悄悄重写了那一天的报告"，很难发现。改成必须是显式日期，
        # 或显式 --all（重生成 runs/ 下全部日期的报告）。
        if "--all" not in sys.argv:
            sys.exit("用法: python report.py <日期> [日期 ...]  或  python report.py --all\n"
                     "（不提供日期时不再回退到写死的默认值）")
        args = sorted(p.name for p in (ROOT / "runs").iterdir() if p.is_dir())
    for d in args:
        txt = report(d)
        out = ROOT / "runs" / d / "REPORT.md"
        out.write_text(txt, encoding="utf-8", newline="\n")
        print(f"已生成 {out.relative_to(ROOT)}（{len(txt)} 字符）")
