"""采集用 HTTP 客户端：自适应限速 + 重试。

东财会丢弃高频请求（RemoteDisconnected），固定间隔不够用，失败后必须主动退让。
"""

from __future__ import annotations

import time

import requests

RETRYABLE = {429, 500, 502, 503, 504}

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class Client:
    def __init__(self, interval_ms: int = 600, max_interval_ms: int = 8000,
                 timeout: int = 25, retries: int = 4):
        self.base_interval = interval_ms / 1000
        self.interval = self.base_interval
        self.max_interval = max_interval_ms / 1000
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0
        self.seq = 0
        self.session = requests.Session()

    # --- 限速 ---------------------------------------------------------------
    def _wait(self):
        gap = time.time() - self._last
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self._last = time.time()

    def _slower(self):
        self.interval = min(self.interval * 1.5, self.max_interval)

    def _faster(self):
        self.interval = max(self.interval * 0.8, self.base_interval)

    # --- 请求 ---------------------------------------------------------------
    def get(self, url: str, *, params=None, headers=None, encoding=None,
            inject_trace: bool = False) -> str:
        """inject_trace 只给东财用——其他源把它当非法参数（腾讯直接 param error）。"""
        h = dict(UA)
        h.update(headers or {})
        last_err = None
        for attempt in range(self.retries + 1):
            self._wait()
            self.seq += 1
            if params is not None and inject_trace:
                params = dict(params)
                params.setdefault("req_trace", str(int(time.time() * 1000)))
            try:
                r = self.session.get(url, params=params, headers=h, timeout=self.timeout)
                if r.status_code in RETRYABLE:
                    ra = r.headers.get("Retry-After")
                    self._slower()
                    sleep_s = float(ra) if (ra or "").replace(".", "", 1).isdigit() else self.interval
                    last_err = f"HTTP {r.status_code}"
                    if attempt < self.retries:
                        time.sleep(min(sleep_s, 30))
                        continue
                    raise RuntimeError(f"{last_err} after {self.retries} retries: {url}")
                r.raise_for_status()
                if encoding:
                    r.encoding = encoding
                self._faster()
                return r.text
            except Exception as e:  # noqa: BLE001 - 采集层统一兜底重试
                last_err = f"{type(e).__name__}: {e}"
                self._slower()
                if attempt >= self.retries:
                    break
                time.sleep(self.interval)
        raise RuntimeError(f"采集失败（{self.retries} 次重试后）：{url} → {last_err}")

    def get_json(self, url: str, *, params=None, headers=None, inject_trace: bool = False):
        import json
        return json.loads(self.get(url, params=params, headers=headers,
                                   inject_trace=inject_trace))
