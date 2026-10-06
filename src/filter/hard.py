"""三道硬过滤：纯代码、零 LLM，判定完全确定、可逐条审计。

第 0 关 原料清洗 → Agent1 → 第 1 关 按类目剔除 → Agent2 → Agent3 → 第 2 关 行业归属清洗
"""

from __future__ import annotations

import re

MAX_CHARS = 800          # 标题 + 正文 > 800 字 → 剔除
TITLE_SIM = 0.95         # 归一化后完全相同，或字符集相似度 ≥ 0.95

# 会议日程预告：「将于 X 月 X 日举办/开幕」这类无实质内容的未来会议预告。
# 判据是"有无实质内容"，不是"有无会议词"——大量实质内容借会议场合披露，必须保留。
_PREVIEW = re.compile(r"将于.{0,24}?(?:举办|举行|召开|开幕|启动|发布|亮相|召开)")
_PREVIEW_MONTH = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]")
# 已发生 / 有实质内容的信号——命中即不按预告剔除
_SUBSTANCE = re.compile(
    r"(?:宣布|签署|签订|中标|获批|批准|印发|施行|实施|生效|量产|下线|投产|扩建|"
    r"出口|进口|涨价|提价|降价|上调|下调|收购|合并|订单|合同|金额|亿元|万元|"
    r"补贴|配额|关税|税率|禁|停产|限产|退出|突破|研发成功|通过|获得)")
_MEETING_WORDS = re.compile(r"(?:大会|论坛|峰会|会议|博览会|展会|年会|研讨会)")

CATEGORIES = [
    "国家政策", "国家单位发言", "外围突发事件", "外围重大事件",
    "外围权威机构/国家发言", "地区政策", "地区发言", "公司合作",
    "公司发布产品/上线模型", "涨价与供给", "国内外龙头发言",
    "世界自然环境", "其他-强信号", "其他-杂/弱信号",
]

# 第 1 关：整类剔除
DROPPED_CATEGORIES = {
    "外围突发事件", "外围重大事件", "地区发言", "其他-强信号",
    "公司发布产品/上线模型", "其他-杂/弱信号", "世界自然环境",
}

OTHER_INDUSTRY = "其他"
PERIPHERY_INDUSTRY = "外围"

_NORM = re.compile(r"[\s\W_]+", re.UNICODE)


def norm_title(title: str) -> str:
    return _NORM.sub("", title or "").lower()


def similar(a: str, b: str) -> float:
    """字符集相似度（Jaccard）。空集返回 0。"""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _is_meeting_preview(title: str, content: str) -> bool:
    """未来会议预告，且无实质内容。"""
    head = (title or "")[:60]
    if not _MEETING_WORDS.search(head):
        return False
    text = title or content or ""
    if not _PREVIEW.search(text):
        return False
    if _PREVIEW_MONTH.search(text):
        return True                      # 「将于 10 月 20 日举办」——未来时点，无内容
    return not _SUBSTANCE.search(text)   # 有实质动词则保留


_MONTH_RE = re.compile(r"(\d{1,2})\s*月")


def past_date_reason(content: str, trade_date) -> str | None:
    """快讯【正文】里是否出现"早于 T-1 的日期"。

    用户定案（T = 交易日）：
      · 正文含「M月」，M < T月                 → 删
      · 正文含「T月D日」，D < T日 − 1          → 删
        （即允许 T-1 那一天；T-2 及更早一律删）

    命中即删，用来剔除"对已发生事情的回顾/总结"—— 那类消息对明日开盘价没有预测力。
    ⚠️ 「M月」这条【不看年份】：用户明确要求如此。
      因此 `2027年3月30日起推出`（未来事件）与 `2025年1月以来的新高`（历史对比基准）
      也会命中被删 —— 这是已知的取舍，记录下来备查。
    """
    if not content:
        return None
    for m in _MONTH_RE.finditer(content):
        mm = int(m.group(1))
        if 1 <= mm <= 12 and mm < trade_date.month:
            return f"正文含过去月份[{mm}月<{trade_date.month}月]"
    cut = trade_date.day - 1
    if cut >= 1:
        for m in re.finditer(rf"{trade_date.month}\s*月\s*(\d{{1,2}})\s*日", content):
            dd = int(m.group(1))
            if dd < cut:
                return f"正文含过去日期[{trade_date.month}月{dd}日<{trade_date.month}月{cut}日]"
    return None


def hard_filter_0(news: list[dict], blacklist: list[str],
                  trade_date=None) -> tuple[list[dict], list[dict]]:
    """原料清洗。返回 (保留, 剔除记录)。

    trade_date 给出时，额外执行「正文含过去日期 → 删」（见 past_date_reason）。
    """
    words = [w.lower() for w in blacklist if w]
    kept, dropped, seen = [], [], []
    for n in news:
        title = n.get("title", "")
        content = n.get("content", "")
        blob = f"{title}\n{content}"
        reason = None

        if not title.strip() and not content.strip():
            reason = "空内容"
        else:
            low = blob.lower()
            hit = next((w for w in words if w in low), None)
            if hit:
                reason = f"黑名单词[{hit}]"
            elif len(title) + len(content) > MAX_CHARS:
                reason = f"超长[{len(title) + len(content)}字]"
            elif _is_meeting_preview(title, content):
                reason = "会议日程预告"
            else:
                # 只看【正文】（用户指定），不看标题
                d = past_date_reason(content, trade_date) if trade_date else None
                if d:
                    reason = d
                else:
                    nt = norm_title(title)
                    if nt and any(nt == s or similar(nt, s) >= TITLE_SIM for s in seen):
                        reason = "标题去重"
                    else:
                        seen.append(nt)

        (dropped if reason else kept).append({**n, "drop_reason": reason})
    return kept, dropped


def hard_filter_1(news: list[dict], stock_names: list[str]) -> tuple[list[dict], list[dict]]:
    """按类目剔除；「公司合作」须标题命中个股池中任一股票名的前两字。"""
    prefixes = {s[:2] for s in stock_names if len(s) >= 2}
    kept, dropped = [], []
    for n in news:
        cat = n.get("category", "")
        reason = None
        if cat in DROPPED_CATEGORIES:
            reason = f"整类剔除[{cat}]"
        elif cat == "公司合作":
            title = n.get("title", "")
            if not any(p in title for p in prefixes):
                reason = "公司合作未命中个股池"
        elif cat not in CATEGORIES:
            reason = f"未知类目[{cat or '空'}]"
        (dropped if reason else kept).append({**n, "drop_reason": reason})
    return kept, dropped


def hard_filter_2(news: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """行业归属清洗：剔除「没有快讯归属的行业」与「其他行业」。

    返回 (保留, 剔除记录, 当日有消息的行业名列表)。
    """
    kept, dropped = [], []
    for n in news:
        ind = (n.get("industry") or "").strip()
        reason = None
        if not ind or ind == OTHER_INDUSTRY:
            reason = "无法归入个股池行业"
        (dropped if reason else kept).append({**n, "drop_reason": reason})
    industries = sorted({n["industry"] for n in kept if n["industry"] != PERIPHERY_INDUSTRY})
    return kept, dropped, industries


# --- 第 3 关：行业推举排序（纯代码筛选器，不是生产者）-----------------------
# 同号同级时的档位优先序（越小越优先）：
#   P1 > C1 = E1 > P2 > C2 = E2 > P3 = C3 = E3 > P4 = C4 = E4
# 设计含义：政策优先只在【第 1、2 档】成立 —— P1/P2 抬到同号 C/E 之上；
# 第 3、4 档（都是弱档）则与同号 C/E 并列，即"弱档层面，政策文件的边际信息量
# 不比公司/事件的实质动作更大"（P3 再量化也只是规划，不等于订单）。
# ⚠️ 因为 P3/P4 处破例，这个序【不是算术式】，必须显式写表；改序请直接改这张表。
TIER_PRIORITY = {
    "P1": 0, "C1": 1, "E1": 1,
    "P2": 2, "C2": 3, "E2": 3,
    "P3": 4, "C3": 4, "E3": 4,
    "P4": 5, "C4": 5, "E4": 5,
}


def tier_rank(t: str) -> int:
    """档位的综合位次（越小越优先）。未知档位排最后。"""
    return TIER_PRIORITY.get(t, 99)


def rank_industries(tiers: dict[str, list[str]], order: list[str]) -> list[dict]:
    """排序键：有无强档 → 强档数量 → 最高档位（见 TIER_PRIORITY）→ 弱档数量 → 既有顺序。"""
    strong_set = {"P1", "P2", "C1", "C2", "E1", "E2"}
    rows = []
    for idx, name in enumerate(order):
        ts = tiers.get(name, [])
        strong = [t for t in ts if t in strong_set]
        weak = [t for t in ts if t not in strong_set]
        best = min(strong, key=tier_rank) if strong else None
        rows.append({
            "industry": name,
            "has_strong": 1 if strong else 0,
            "strong_n": len(strong),
            "best_tier": best,
            "best_rank": tier_rank(best) if best else 99,
            "weak_n": len(weak),
            "tiers": ts,
            "_idx": idx,
        })
    rows.sort(key=lambda r: (-r["has_strong"], -r["strong_n"], r["best_rank"],
                             -r["weak_n"], r["_idx"]))
    return rows
