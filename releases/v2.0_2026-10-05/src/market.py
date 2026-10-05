"""行情与交易日历：历史日线 OHLC（腾讯 web.ifzq.gtimg.cn）。

用途有两处：
- 回测评估：T 日开盘买、T+1 日开盘卖 → open-to-open 收益
- 交易日历：判断指定日期是否交易日、下一交易日是谁

> 为什么不用东财 push2his：实测该域名族在当前网络下会 ProxyError（push2 同样），
> 而腾讯接口对个股/ETF/指数三者都稳定返回前复权日线。
"""

from __future__ import annotations

import datetime as dt

from .http import Client

KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SH_INDEX = "sh000001"          # 上证指数，用作交易日历


def symbol(code: str, kind: str = "stock") -> str:
    """6 位代码 → 腾讯 symbol（sh/sz 前缀）。已带前缀的原样返回。"""
    if code[:2] in ("sh", "sz", "bj"):
        return code
    if kind == "index":
        # 上证指数 000xxx / 999xxx 在沪市；深证成指 399xxx 在深市
        return ("sz" if code.startswith("399") else "sh") + code
    # 个股/ETF：5/6/9 开头在沪市，其余在深市
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def daily(client: Client, code: str, beg: str, end: str, kind: str = "stock") -> list[dict]:
    """拉取 [beg, end] 区间内的前复权日线。

    返回 [{date, open, close, high, low, volume}]，按日期升序。
    空响应必须报错——否则"取不到行情"会静默变成 0 条数据，回测结果无声失真。
    """
    sym = symbol(code, kind)
    param = f"{sym},day,{beg},{end},800,qfq"
    err = None
    for _ in range(3):
        j = client.get_json(KLINE, params={"param": param})
        node = (j.get("data") or {}).get(sym)
        if node is None:
            err = f"接口未返回 {sym} 节点：{str(j)[:200]}"
            continue
        rows = node.get("qfqday") or node.get("day") or []
        if rows:
            return sorted(({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                            "high": float(p[3]), "low": float(p[4]),
                            "volume": float(p[5]) if len(p) > 5 and p[5] else 0.0}
                           for p in rows), key=lambda r: r["date"])
        err = f"{sym} 在 {beg}~{end} 无日线（参数 {param}）"
    raise RuntimeError(f"取不到 {code} 的日线：{err}")


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
