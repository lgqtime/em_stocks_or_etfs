"""LLM 调用层：模型分档（§4.4）+ 内容寻址缓存。

缓存的意义是原则一 ③ 的落地：同一份输入可反复跑，把「输入变化」与「模型随机」分开。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

from ..configs import ROOT, load_env

BASE_URL = "https://api.deepseek.com"
CACHE = ROOT / ".cache" / "llm"

# Agent 名 → (模型, thinking, effort)。依据 PROJECT_PLAN.md §4.4 分档表。
MODELS: dict[str, tuple[str, str, str | None]] = {
    "agent1":  ("deepseek-flash",  "disabled", None),
    "agent2":  ("deepseek-flash",  "disabled", None),
    "agent3":  ("deepseek-v4-pro", "enabled",  "low"),
    "agent4":  ("deepseek-v4-pro", "enabled",  "high"),
    "agent42": ("deepseek-v4-pro", "enabled",  "high"),
    "agent5":  ("deepseek-v4-pro", "enabled",  "low"),
    "agent6":  ("deepseek-v4-pro", "enabled",  "max"),
    "agent62": ("deepseek-v4-pro", "enabled",  "max"),
    "audit":   ("deepseek-v4-pro", "enabled",  "max"),
}

_JSON = re.compile(r"\{.*\}", re.S)

# 不缓存的 Agent：决策与审计单次结果本就带模型随机，
# 缓存会把"第一次碰巧算出的那个结论"冻结成唯一答案（方案 §10.1 第 5 条：
# 单次运行的结果不能用来比较版本）。批量提取类（agent1/2/3/4/42/5）才缓存。
NO_CACHE = {"agent6", "agent62", "audit"}

# 固定 temperature=0 的 Agent：把采样随机性从决策路径上掐掉。
# 实测（09-10 跑 4 次得 3 个不同标的、审计对同一条证据两次结论相反）表明
# 决策层的摆动主要来自采样。注意：temperature=0 **不等于**确定性输出，
# 只消除采样这一个来源；审计判据本身的弹性要靠 tier_rules 的硬判据约束。
GREEDY = {"agent6", "agent62", "audit"}


@dataclasses.dataclass
class Result:
    text: str
    reasoning: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    cached: bool = False
    error: str | None = None
    tag: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def json(self) -> dict:
        """容错解析：容忍 ```json 围栏与前后说明文字。"""
        if self.error:
            raise ValueError(f"LLM 调用失败：{self.error}")
        t = self.text.strip()
        if t.startswith("```"):
            t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            m = _JSON.search(t)
            if not m:
                raise ValueError(f"无法解析 JSON：{t[:300]}") from None
            return json.loads(m.group(0))


class LLM:
    def __init__(self, use_cache: bool = True, workers: int = 6):
        env = load_env()
        key = env.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY（见 .env）")
        self.client = OpenAI(api_key=key, base_url=BASE_URL, timeout=600, max_retries=3)
        self.use_cache = use_cache
        self.workers = workers
        self.calls: list[Result] = []
        self._lock = threading.Lock()

    # --- 单次调用 -----------------------------------------------------------
    def call(self, agent: str, system: str, user: str, *, tag: str = "") -> Result:
        model, thinking, effort = MODELS[agent]
        cacheable = self.use_cache and agent not in NO_CACHE
        ck = hashlib.sha256(
            f"{model}|{thinking}|{effort}|{system}|{user}".encode()).hexdigest()[:32]
        cpath = CACHE / f"{ck}.json"
        if cacheable and cpath.exists():
            d = json.loads(cpath.read_text(encoding="utf-8"))
            r = Result(text=d["text"], reasoning=d.get("reasoning", ""),
                       prompt_tokens=d.get("prompt_tokens", 0),
                       completion_tokens=d.get("completion_tokens", 0),
                       seconds=0.0, cached=True, tag=tag)
            with self._lock:
                self.calls.append(r)
            return r

        body = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            # thinking 是 DeepSeek 的扩展参数，openai SDK 不认识，
            # 必须走 extra_body 透传（否则 create() 直接 TypeError）。
            "extra_body": {"thinking": {"type": thinking}},
        }
        if agent in GREEDY:
            body["temperature"] = 0
        if effort:
            body["reasoning_effort"] = effort

        import time
        t0 = time.time()
        try:
            data = self.client.chat.completions.create(**body)
            msg = data.choices[0].message
            u = data.usage
            r = Result(text=msg.content or "",
                       reasoning=getattr(msg, "reasoning_content", "") or "",
                       prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
                       completion_tokens=getattr(u, "completion_tokens", 0) or 0,
                       seconds=round(time.time() - t0, 1), tag=tag)
        except Exception as e:  # noqa: BLE001 - 单条失败不拖垮整轮
            r = Result(text="", reasoning="", prompt_tokens=0, completion_tokens=0,
                       seconds=round(time.time() - t0, 1),
                       error=f"{type(e).__name__}: {e}", tag=tag)

        with self._lock:
            self.calls.append(r)
            if cacheable and not r.error:
                CACHE.mkdir(parents=True, exist_ok=True)
                cpath.write_text(json.dumps({
                    "text": r.text, "reasoning": r.reasoning,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                }, ensure_ascii=False), encoding="utf-8")
        return r

    # --- 并发批量 -----------------------------------------------------------
    def map(self, agent: str, system: str, user_of, items: list) -> list[Result]:
        """对 items 并发调用同一提示词模板，返回与输入同序的结果。"""
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            return list(ex.map(
                lambda p: self.call(agent, system, user_of(p[1]), tag=str(p[0])),
                list(enumerate(items))))

    # --- 用量 ---------------------------------------------------------------
    def usage(self) -> dict:
        live = [c for c in self.calls if not c.cached]
        by_agent: dict[str, dict] = {}
        for c in self.calls:
            a = c.tag.split(":")[0] or "other"
            d = by_agent.setdefault(a, {"calls": 0, "cached": 0, "prompt": 0, "completion": 0})
            d["calls"] += 1
            d["cached"] += int(c.cached)
            d["prompt"] += c.prompt_tokens
            d["completion"] += c.completion_tokens
        return {
            "calls": len(self.calls),
            "cached": sum(1 for c in self.calls if c.cached),
            "errors": sum(1 for c in self.calls if c.error),
            "prompt_tokens": sum(c.prompt_tokens for c in live),
            "completion_tokens": sum(c.completion_tokens for c in live),
            "seconds": round(sum(c.seconds for c in live), 1),
            "by_tag": by_agent,
        }


def strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    return t


__all__ = ["LLM", "Result", "MODELS", "strip_fences"]
