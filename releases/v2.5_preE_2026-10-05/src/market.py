"""行情与交易日历：历史日线 OHLC。

用途有两处：
- 回测评估：T 日开盘买、T+1 日开盘卖 → open-to-open 收益
- 交易日历：判断指定日期是否交易日、下一交易日是谁

**两个源串联**（源优先级：**腾讯 → 新浪**，先到的可用源即采用）：

| 顺序 | 源 | 实测 |
|---|---|---|
| 1 | 腾讯 `web.ifzq.gtimg.cn` | 首选（更稳、支持区间查询、前复权）。缺点是有反爬，被判定后**整个域名对所有标的连续 501**（JS 挑战页），当天内不恢复 |
| 2 | 新浪 `money.finance.sina.com.cn` | 备用（目前全池 107 只逐个验证可用，数值与腾讯完全一致）。只能"最近 N 根"，按区间自行裁剪 |

> 未采用东财 push2his：该域名族在当前网络下直接 ProxyError。
> 两个源都是"平时好用、会突然整体不可用"，所以**不能只挂一个**。
"""

from __future__ import annotations

import datetime as dt
import json
import re

from .http import Client

TX_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "CN_MarketData.getKLineData")
SH_INDEX = "sh000001"          # 上证指数，用作交易日历


def symbol(code: str, kind: str = "stock") -> str:
    """6 位代码 → 带交易所前缀的 symbol（sh/sz）。已带前缀的原样返回。"""
    if code[:2] in ("sh", "sz", "bj"):
        return code
    if kind == "index":
        # 上证指数 000xxx/999xxx 在沪市；深证成指 399xxx 在深市
        return ("sz" if code.startswith("399") else "sh") + code
    # 个股/ETF：5/6/9 开头在沪市，其余在深市
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


# --- 源一：腾讯 -------------------------------------------------------------
def _tencent(client: Client, sym: str, beg: str, end: str) -> list[dict]:
    j = client.get_json(TX_KLINE, params={"param": f"{sym},day,{beg},{end},800,qfq"})
    node = (j.get("data") or {}).get(sym) or {}
    rows = node.get("qfqday") or node.get("day") or []
    out = [{"date": p[0], "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]),
            "volume": float(p[5]) if len(p) > 5 and p[5] else 0.0} for p in rows]
    return out


# --- 源二：新浪 -------------------------------------------------------------
def _sina(client: Client, sym: str, beg: str, end: str) -> list[dict]:
    """新浪只给"最近 N 根"，取够跨度后按 [beg, end] 裁剪。"""
    span = (dt.date.fromisoformat(end) - dt.date.fromisoformat(beg)).days
    datalen = min(1023, max(40, int(span * 0.75) + 20))
    text = client.get(SINA_KLINE, params={"symbol": sym, "scale": "240",
                                          "ma": "no", "datalen": str(datalen)},
                      headers={"Referer": "https://finance.sina.com.cn/"})
    if not text.strip().startswith("["):
        raise RuntimeError(f"新浪返回非数组：{text[:120]}")
    rows = json.loads(re.sub(r"(\w+):", r'"\1":', text))
    out = []
    for p in rows:
        d = p["day"][:10]
        if beg <= d <= end:
            out.append({"date": d, "open": float(p["open"]), "close": float(p["close"]),
                        "high": float(p["high"]), "low": float(p["low"]),
                        "volume": float(p.get("volume") or 0)})
    return out


SOURCES = (("腾讯", _tencent), ("新浪", _sina))


def daily(client: Client, code: str, beg: str, end: str, kind: str = "stock") -> list[dict]:
    """拉取 [beg, end] 区间内的日线，按日期升序。

    空响应必须报错——否则"取不到行情"会静默变成 0 条数据，回测结果无声失真。
    """
    sym = symbol(code, kind)
    errs = []
    for name, fn in SOURCES:
        try:
            rows = fn(client, sym, beg, end)
            if rows:
                return sorted(rows, key=lambda r: r["date"])
            errs.append(f"{name}返回空")
        except Exception as e:  # noqa: BLE001 - 逐源降级，最后统一报错
            errs.append(f"{name}: {type(e).__name__}: {str(e)[:80]}")
    raise RuntimeError(f"取不到 {code} 的日线（{beg}~{end}）：" + " | ".join(errs))


def opens(client: Client, code: str, beg: str, end: str, kind: str = "stock") -> dict[str, float]:
    """{日期: 开盘价}。前复权，保证跨日可比。"""
    return {r["date"]: r["open"] for r in daily(client, code, beg, end, kind)}


def trading_days(client: Client, beg: str, end: str) -> list[str]:
    """以 上证指数 的日线为准的交易日列表。"""
    return [r["date"] for r in daily(client, SH_INDEX, beg, end, kind="index")]


def _span(date: str, ahead_days: int = 40) -> tuple[str, str]:
    d = dt.date.fromisoformat(date)
    return date, (d + dt.timedelta(days=ahead_days)).isoformat()


def next_trading_day(client: Client, date: str, *, ahead_days: int = 40) -> str | None:
    """date 之后的下一个交易日（区间从 date 当天开始取，节假日自动跳过）。"""
    beg, end = _span(date, ahead_days)
    return next((x for x in trading_days(client, beg, end) if x > date), None)


def evaluate(client: Client, code: str, date: str, kind: str = "stock") -> dict:
    """T 日开盘买、T+1 日开盘卖。返回 {entry_date, entry_price, exit_date, exit_price, return_pct}。"""
    beg, end = _span(date)
    o = opens(client, code, beg, end, kind)
    if date not in o:
        return {"error": f"{date} 没有 {code} 的开盘价（非交易日或代码无行情）"}
    nxt = next((x for x in sorted(o) if x > date), None)
    if nxt is None:
        return {"error": f"{date} 之后没有可用的交易日行情（{code}）"}
    entry, exit_ = o[date], o[nxt]
    return {"entry_date": date, "entry_price": entry, "exit_date": nxt,
            "exit_price": exit_, "return_pct": round((exit_ / entry - 1) * 100, 3)}
