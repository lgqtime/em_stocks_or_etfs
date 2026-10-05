"""用已落盘的 09-10 / 09-11 产物，算出新配额下"精选条数"会怎么变（不跑预测）。"""

import json
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

from src import configs, pipeline

rules = configs.load_tier_rules()
q = rules["summary_quota"]
buckets = rules["periphery"]["summary_buckets"]


def show(date, top):
    a42 = json.load(open(f"runs/{date}/analysis/agent42_tiers.json", encoding="utf-8"))
    ind = [n for n in a42 if n.get("industry") != "外围" and n.get("industry") in top]
    per = [n for n in a42 if n.get("industry") == "外围"]
    print(f"\n=== {date} ===")
    tot = 0
    for name in top:
        g = [n for n in ind if n["industry"] == name]
        cap = pipeline._cap(g, pipeline._quota_of(q, name))
        tot += len(cap)
        tiers = Counter(x["tier"] for x in g)
        print(f"  {name:<6} 上游 {len(g):2d} 条 {dict(tiers)}  →  裁剪后 {len(cap)}/{pipeline._quota_of(q, name)}")
    by_dir = {}
    for n in per:
        by_dir.setdefault(n.get("direction") or "中性", []).append(n)
    for d, g in sorted(by_dir.items()):
        cap = pipeline._cap(g, buckets.get(d, 2))
        tot += len(cap)
        print(f"  外围[{d}] 上游 {len(g):2d} 条  →  裁剪后 {len(cap)}/{buckets.get(d, 2)}")
    print(f"  合计进入下游：{tot} 条（旧口径：所有行业全部保留、外围全部保留 = {len(ind) + len(per)} 条）")


show("2026-09-10", ["电子", "交通运输", "医药生物", "银行", "农林牧渔"])
show("2026-09-11", ["农林牧渔", "房地产", "电子", "计算机", "非银金融"])
