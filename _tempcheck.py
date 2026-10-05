"""探针：确认 temperature=0 与 DeepSeek 的 thinking 模式兼容（仅 3 次调用）。"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from src.llm.client import LLM

llm = LLM(use_cache=False)
for agent in ("agent1", "audit", "agent6"):
    r = llm.call(agent, "只输出 JSON。", '回复 {"ok":true}', tag=f"temp:{agent}")
    print(f"  {agent:8} error={r.error}  text={(r.text or '')[:30]!r}  "
          f"tok={r.prompt_tokens}/{r.completion_tokens}")
