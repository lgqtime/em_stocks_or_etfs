"""数据源二：外围夜盘（新浪 hq.sinajs.cn）。

⚠️ hf_ / gb_ / hk 三类的字段下标完全不同，不能共用一个下标表。
"""

from __future__ import annotations

URL = "https://hq.sinajs.cn/list={codes}"
HEADERS = {"Referer": "https://finance.sina.com.cn"}

# key -> (显示名, 代码, 类别)
SYMBOLS: dict[str, tuple[str, str, str]] = {
    "a50":    ("富时中国A50期货", "hf_CHA50CFD", "期货"),
    "hsi":    ("恒生指数",       "rt_hkHSI",    "港股"),
    "dji":    ("道琼斯",         "gb_$dji",     "美股"),
    "ixic":   ("纳斯达克",       "gb_ixic",     "美股"),
    "spx":    ("标普500",        "gb_$inx",     "美股"),
    "n225":   ("日经225",        "hf_NK",       "期货"),
    "gold":   ("纽约黄金",       "hf_GC",       "期货"),
    "oil":    ("纽约原油",       "hf_CL",       "期货"),
    "brent":  ("布伦特原油",     "hf_OIL",      "期货"),
    "silver": ("纽约白银",       "hf_SI",       "期货"),
}


def _f(parts: list[str], i: int) -> float | None:
    try:
        v = parts[i].strip()
        return float(v) if v not in ("", "-", "--") else None
    except (IndexError, ValueError):
        return None


def parse_line(line: str) -> tuple[str, list[str]] | None:
    """`var hq_str_<code>="a,b,c";` → (code, [字段])"""
    if "hq_str_" not in line or "=" not in line:
        return None
    head, _, tail = line.partition("=")
    code = head.split("hq_str_", 1)[1].strip()
    return code, tail.strip().strip(";").strip('"').split(",")


def parse_quote(key: str, parts: list[str]) -> dict:
    """三类下标各走各的。返回 {price, prev_close, open, pct, time}。"""
    name, code, kind = SYMBOLS[key]
    if code.startswith("hf_"):
        return {"price": _f(parts, 0), "prev_close": _f(parts, 7),
                "open": _f(parts, 8), "pct": None, "time": parts[6] if len(parts) > 6 else ""}
    if code.startswith("gb_"):
        return {"price": _f(parts, 1), "prev_close": _f(parts, 7),
                "open": _f(parts, 8), "pct": _f(parts, 2),
                "time": parts[3] if len(parts) > 3 else ""}
    if code.startswith("rt_hk"):
        return {"price": _f(parts, 6), "prev_close": _f(parts, 3),
                "open": _f(parts, 2), "pct": _f(parts, 8),
                "time": parts[18] if len(parts) > 18 else ""}
    raise ValueError(f"未知代码族：{code}")


def collect_quotes(client) -> list[dict]:
    """采集 10 个外围指标。逐个代码都验证过有数据。"""
    codes = [c for _, c, _ in SYMBOLS.values()]
    text = client.get(URL.format(codes=",".join(codes)), headers=HEADERS, encoding="gbk")
    by_code = {}
    for line in text.strip().splitlines():
        parsed = parse_line(line)
        if parsed:
            by_code[parsed[0]] = parsed[1]

    out = []
    for key, (name, code, kind) in SYMBOLS.items():
        parts = by_code.get(code)
        if not parts:
            out.append({"key": key, "name": name, "code": code, "kind": kind,
                        "price": None, "prev_close": None, "open": None,
                        "pct": None, "time": "", "missing": True})
            continue
        q = parse_quote(key, parts)
        if q["pct"] is None and q["price"] and q["prev_close"]:
            q["pct"] = round((q["price"] / q["prev_close"] - 1) * 100, 3)
        out.append({"key": key, "name": name, "code": code, "kind": kind, **q})
    return out


def quotes_text(quotes: list[dict]) -> str:
    """给提示词用的紧凑文本。"""
    lines = []
    for q in quotes:
        if q.get("missing"):
            lines.append(f"- {q['name']}（{q['kind']}）：无数据")
            continue
        pct = f"{q['pct']:+.2f}%" if q.get("pct") is not None else "—"
        lines.append(f"- {q['name']}（{q['kind']}）：现价 {q['price']}，涨跌 {pct}，"
                     f"昨收 {q['prev_close']}，今开 {q['open']}")
    return "\n".join(lines)
