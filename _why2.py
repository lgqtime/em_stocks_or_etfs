"""从 LLM 内容缓存里还原上一轮 09-10 的 agent1 分类，定位 f1 差量。"""

import hashlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from src import configs
from src.filter import hard
from src.llm import prompts as P
from src.pipeline import _batch, news_lines
from src.llm.client import CACHE, MODELS

raw = json.load(open("runs/2026-09-10/raw/news.json", encoding="utf-8"))
kept0 = [n for n in hard.hard_filter_0(raw, configs.load_blacklist())[0]]
batches = _batch(kept0, 60)
print(f"f0 保留 {len(kept0)} → {len(batches)} 批")

model, thinking, effort = MODELS["agent1"]
system = P.agent1()

for i, b in enumerate(batches):
    user = "分类下列快讯：\n" + news_lines(b)
    # 新旧 user 文本只差新闻集合，这里枚举当次真实哈希
    ck = hashlib.sha256(f"{model}|{thinking}|{effort}|{system}|{user}".encode()).hexdigest()[:32]
    p = CACHE / f"{ck}.json"
    print(f"  批{i}: {len(b):2d} 条  缓存{'命中' if p.exists() else '未命中'}")

print()
print("→ 逐条比对新旧 agent1 分类结论（旧结论在缓存里，新结论用当次对话哈希取不到时说明是新算的）")
new_cls = {x["code"]: x["category"] for x in
           json.load(open("runs/2026-09-10/analysis/agent1_categories.json", encoding="utf-8"))}

# 找出所有缓存里 agent1 的结论，与本次比对
found_old = {}
for f in CACHE.glob("*.json"):
    pass  # 无法反推 prompt，只能比较本次
print(f"  本次分类结论 {len(new_cls)} 条")
cnt = {}
for v in new_cls.values():
    cnt[v] = cnt.get(v, 0) + 1
print("  本次类目分布:", dict(sorted(cnt.items(), key=lambda kv: -kv[1])))

names = list(configs.load_stocks().by_name)
k1, d1 = hard.hard_filter_1(
    [{"code": c, "title": "", "category": v} for c, v in new_cls.items()], names)
