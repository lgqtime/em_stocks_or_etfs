"""对照：09-10 在【旧代码】与【新代码】两次运行下的差异归因。

旧代码那次跑完（14:36 前）后的产物已被覆盖，因此用两个来源还原：
- 本次新跑的产物：runs/2026-09-10/
- 旧跑的档位：LLM 内容缓存里 agent4/agent42 的旧结论（按批次的 prompt 哈希）
以及可直接对比的统计口径（meta.json 各阶段计数）。
"""

import json
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

from src import configs

rules = configs.load_tier_rules()
STRONG = set(rules["tier_sets"]["STRONG_TIERS"])

new = json.load(open("runs/2026-09-10/analysis/agent42_tiers.json", encoding="utf-8"))
print("=== 新跑（当前代码）09-10 的行业档位分布 ===")
by = {}
for n in new:
    by.setdefault(n["industry"], []).append((n["tier"], n["title"][:44]))
for ind, xs in sorted(by.items(), key=lambda kv: -sum(1 for t, _ in kv[1] if t in STRONG)):
    strong = [t for t, _ in xs if t in STRONG]
    mark = "★" if strong else " "
    print(f" {mark} {ind:<6} 强档{len(strong)}: {strong}  全部: {[t for t, _ in xs]}")

print()
print("=== 两次运行的直接可比口径（来自各自 meta.json）===")
new_meta = json.load(open("runs/2026-09-10/meta.json", encoding="utf-8"))
stages = new_meta["stages"]
print(f"  本次：f0 保留 {stages['f0_原料清洗']['保留']} → f1 保留 {stages['f1_按类目剔除']['保留']} "
      f"→ f2 保留 {stages['f2_行业归属清洗']['保留']} → 候选行业 {stages['f3_行业推举']['候选行业']}")
print(f"  本次入选：{stages['f3_行业推举']['入选名单']}")
print()
print("  上一轮（14:36 记录）：f0 保留 131 → f1 保留 60 → f2 保留 37 → 候选行业 8")
print("  上一轮入选：['电子', '交通运输', '医药生物', '银行', '农林牧渔']")
