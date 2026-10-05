"""外围夜盘的【历史】版本 —— 用于回测，口径与实时源一致。

为什么需要它：`quotes.py` 走新浪 `hq.sinajs.cn`，只有实时快照，
回测时所选日期拿不到当夜行情（曾导致回测里喂给 Agent 的是"运行日"的外盘）。

**口径（与实时源的 prev_close 语义对齐）**
取该指标在【T 日 09:00 之前已收出的最后一根日线】作为"当夜收盘"，
涨跌 = 当夜收盘 / 再前一根收盘 − 1。这样重建的量与生产运行时
`predict()` 看到的同一种量可比，避免"回测口径比生产更精细"而两者对不上。

⚠️ 已知简化：少掉 T 日亚洲早盘那一段（A50 / 日经 / 原油在 09:00 前已交易数小时）。
   要补那一段需要分钟线（腾讯 web3.ifzq 被本机网络阻断；新浪有分钟线但格式需再解析）。
   本模块只做日线口径，且不读取任何晚于 T 日的数据。

三个源（均已在 2026-09-10 验证可取到 2026-09 的历史）：
  · 新浪美股   stock.finance.sina.com.cn  US_MinKService.getDailyK
  · 新浪全球期货 stock2.finance.sina.com.cn GlobalFuturesService.getGlobalFuturesDailyKLine
  · 腾讯港股   web.ifzq.gtimg.cn/appstock/app/fqkline/get
"""

from __future__ import annotations

import json

from . import quotes as live

HEADERS_SINA = {"Referer": "https://finance.sina.com.cn/"}
SINA_US = ("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/x/"
           "US_MinKService.getDailyK")
SINA_FUT = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/x/"
            "GlobalFuturesService.getGlobalFuturesDailyKLine")
TX_HK = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
# 同一台服务器的备用入口：web. 前缀那个会被腾讯 WAF 拦（HTTP 501 挑战页），
# 不带前缀的入口实测可用。主入口失败时自动回落（实测 2026-10-05 主入口全程 501）。
TX_HK_ALT = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"

# key -> (源, 该源的代码)。与 quotes.SYMBOLS 的 key 一一对应。
SRC: dict[str, tuple[str, str]] = {
    "a50":    ("fut", "CHA50CFD"),
    "hsi":    ("hk", "hkHSI"),
    "dji":    ("us", ".DJI"),
    "ixic":   ("us", ".IXIC"),
    "spx":    ("us", ".INX"),
    "n225":   ("fut", "NK"),
    "gold":   ("fut", "GC"),
    "oil":    ("fut", "CL"),
    "brent":  ("fut", "OIL"),
    "silver": ("fut", "SI"),
}

# 各源能给的历史深度（起点），用于判断某日期是否落在可重建范围内
MIN_DATE = {"us": "2004-01-02", "fut": "2016-10-05", "hk": "2026-07-10"}

# ⚠️ 可靠性分级（实测发现，务必知情）：
#   · 美股三大指数（dji/ixic/spx）与恒生指数 = 现金指数，无换月问题，涨跌可直接用
#   · 期货类（a50/n225/gold/oil/brent/silver）= **连续合约**，换月处可能出现跳空，
#     该处的"日→日涨跌"会掺入换月价差而非真实行情。
#     实测线索：A50 在 2026-09-09 收 14665，而 2026-10-05 实时快照的昨收字段为 13810，
#     两者相差约 6%，非真实行情波动。
#   → 期货类的 pct 只作参考；要精确到 T 日 09:00 需分钟线（见文件头说明）。
RELIABILITY = {"dji": "high", "ixic": "high", "spx": "high", "hsi": "high",
               "a50": "roll_risk", "n225": "roll_risk", "gold": "roll_risk",
               "oil": "roll_risk", "brent": "roll_risk", "silver": "roll_risk"}


def _jsonp(text: str):
    """剥掉 jsonp 包装，取内部的 JSON 数组。"""
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < 0:
        raise RuntimeError(f"返回体不是 JSON 数组：{text[:120]}")
    return json.loads(text[i:j + 1])


def _bars(client, kind: str, code: str) -> list[dict]:
    """取该指标的日线（尽量长），统一成 [{date, open, close}]，按日期升序。"""
    if kind == "us":
        text = client.get(SINA_US, params={"symbol": code, "___qn": "n3"},
                          headers=HEADERS_SINA)
        rows = _jsonp(text)
        return [{"date": r["d"][:10], "open": _f(r.get("o")), "close": _f(r.get("c"))}
                for r in rows if r.get("d")]
    if kind == "fut":
        text = client.get(SINA_FUT, params={"symbol": code}, headers=HEADERS_SINA)
        rows = _jsonp(text)
        return [{"date": r["date"][:10], "open": _f(r.get("open")),
                 "close": _f(r.get("close"))} for r in rows if r.get("date")]
    if kind == "hk":
        last_err = None
        for url in (TX_HK, TX_HK_ALT):
            try:
                j = client.get_json(url, params={"param": f"{code},day,,,320,qfq"})
            except Exception as e:  # noqa: BLE001 - 逐入口回落
                last_err = f"{url.split('/')[2]}: {type(e).__name__}"
                continue
            node = (j.get("data") or {}).get(code) or {}
            rows = node.get("day") or node.get("qfqday") or []
            if rows:
                return [{"date": p[0], "open": _f(p[1]), "close": _f(p[2])} for p in rows]
            last_err = f"{url.split('/')[2]}: 无 day 数据"
        raise RuntimeError(f"港股日线取不到（{last_err}）")
    raise ValueError(f"未知源类型：{kind}")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def collect_quotes_hist(client, date: str) -> list[dict]:
    """重建 date 日 09:00 前应看到的外盘。返回结构与 quotes.collect_quotes() 同构。

    每个指标独立降级：某个源失败只让该指标 missing，不影响其余。
    """
    out = []
    for key, (name, code, kind) in live.SYMBOLS.items():
        src, src_code = SRC[key]
        rec = {"key": key, "name": name, "code": code, "kind": kind,
               "price": None, "prev_close": None, "open": None, "pct": None,
               "time": "", "source": f"{src}:{src_code}", "hist": True,
               "reliability": RELIABILITY.get(key, "unknown")}
        try:
            bars = _bars(client, src, src_code)
            # 只用【早于 date】的最后一根：T 日 09:00 时该日线尚未收出
            prior = [b for b in bars if b["date"] < date]
            if len(prior) < 2:
                rec["missing"] = True
                rec["note"] = (f"历史不足（{len(prior)} 根，源头 {MIN_DATE.get(src, '?')} 起）"
                               if prior else "该源无此指标的历史")
            else:
                last, prev = prior[-1], prior[-2]
                rec.update({
                    "price": last["close"], "prev_close": prev["close"], "open": last["open"],
                    "time": last["date"],
                    "pct": (round((last["close"] / prev["close"] - 1) * 100, 3)
                            if last["close"] and prev["close"] else None),
                })
        except Exception as e:  # noqa: BLE001 - 单指标失败不影响其余
            rec["missing"] = True
            rec["note"] = f"{type(e).__name__}: {str(e)[:80]}"
        out.append(rec)
    return out


def availability(date: str) -> dict:
    """该日期能重建到哪些指标（不联网，只按源的历史起点判断）。"""
    ok, bad = [], []
    for key, (src, _) in SRC.items():
        (ok if date >= MIN_DATE[src] else bad).append(key)
    return {"ok": ok, "insufficient_history": bad}
