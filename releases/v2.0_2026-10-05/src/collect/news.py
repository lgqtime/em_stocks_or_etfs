"""数据源一：东财 7x24 快讯。

两条必须知道的实现要点：
1. `sortEnd` 是可直接跳转的微秒时间锚点，不是页码 —— 因此可以跳页补采历史数据。
2. `summary` 就是完整正文，默认不抓文章页。
"""

from __future__ import annotations

import datetime as dt

URL = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"
ARTICLE = "https://finance.eastmoney.com/a/{code}.html"
PAGE_SIZE = 200

# 采集时间窗：A = T-1 18:00–23:00，B = T 05:00–09:00
WINDOWS = ((0, 18 * 60, 23 * 60), (1, 5 * 60, 9 * 60))
MAX_PAGES = 60          # 单次最多 12,000 条；历史补采须分段，不能靠加大它


def _ts(text: str) -> int:
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp())


def _windows(date: str):
    """A 窗 = T-1 18:00–23:00，B 窗 = T 05:00–09:00（均为 naive 本地时间）。"""
    d = dt.datetime.combine(dt.date.fromisoformat(date), dt.time.min)
    return [(d - dt.timedelta(days=off, minutes=-start),
             d - dt.timedelta(days=off, minutes=-end))
            for off, start, end in WINDOWS]


def _to_epoch(text: str) -> float:
    return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


def collect_news(client, date: str, *, max_pages: int = MAX_PAGES) -> list[dict]:
    """采集 date 日 09:00 前发布的快讯（A 窗 + B 窗）。

    从 date 09:00 的锚点向后翻页，翻到早于 A 窗开头即停 —— 绝不读取晚于 date 09:00 的数据。
    """
    wins = _windows(date)
    lo = min(w[0] for w in wins).timestamp()
    hi = max(w[1] for w in wins).timestamp()
    anchor = int(max(w[1] for w in wins).timestamp() * 1_000_000)

    out: dict[str, dict] = {}
    seen: set[str] = set()
    for page in range(max_pages):
        try:
            j = client.get_json(URL, params={
                "client": "web", "biz": "web_724", "fastColumn": "102",
                "sortEnd": str(anchor), "pageSize": str(PAGE_SIZE),
            })
        except RuntimeError:
            if out:
                break            # 已有数据可用，不因单页失败丢掉整轮采集
            raise
        data = j.get("data") or {}
        lst = data.get("fastNewsList") or []
        if not lst:
            break

        oldest = None
        for it in lst:
            code = it.get("code")
            show = it.get("showTime") or ""
            if not show:
                continue
            ep = _to_epoch(show)
            oldest = ep if oldest is None else min(oldest, ep)
            if code in seen or not (lo <= ep <= hi):
                continue
            seen.add(code)
            out[code] = {
                "code": code,
                "showTime": show,
                "ts": ep,
                "title": (it.get("title") or "").strip(),
                "content": (it.get("summary") or it.get("title") or "").strip(),
                "url": ARTICLE.format(code=code),
            }

        # 下一页锚点：优先用接口回传的 sortEnd（微秒），否则用本页最老的 realSort
        nxt = data.get("sortEnd")
        if not nxt:
            nxt = min(int(x["realSort"]) for x in lst if x.get("realSort"))
        anchor = int(nxt)
        if oldest is not None and oldest < lo:
            break
        if anchor / 1_000_000 < lo:
            break

    return sorted(out.values(), key=lambda x: x["ts"])


__all__ = ["collect_news", "URL", "MAX_PAGES"]
