"""同一份输入反复跑同一日，看模型随机的摆动幅度。

方案 §10.1 第 5 条：单次运行的结果不能用来比较版本。
本脚本就是把它变成可执行的一条命令。

    python repeat.py 2026-09-10 3

⚠️ 日期必须显式给出 —— 之前有写死的默认日期，会把"忘了给参数"变成
   "悄悄反复跑某一天并覆盖它的产物"，这类静默回退很难发现。
"""

import json
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

from src import market
from src.http import Client
from src.pipeline import predict

if len(sys.argv) < 2:
    sys.exit("用法: python repeat.py <日期> [次数=3]\n"
             "（日期必须显式给出，不再回退到写死的默认值）")
date = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
client = Client()

rows = []
for i in range(n):
    dec = predict(date, use_cache=True)
    ret = None
    if dec.action == "买入":
        ev = market.evaluate(client, dec.code, date)
        ret = ev.get("return_pct")
    rows.append({"run": i + 1, "action": dec.action, "code": dec.code,
                 "name": dec.name, "industry": dec.industry,
                 "audit_rounds": dec.audit_rounds, "return_pct": ret})
    print(f"  第{i + 1}次: {dec.action} {dec.code} {dec.name} 收益={ret}", flush=True)

traded = [r for r in rows if r["action"] == "买入"]
print("\n" + "=" * 52)
print(f"日期 {date}  运行 {n} 次")
print("决策分布 :", dict(Counter(r["action"] for r in rows)))
print("标的分布 :", dict(Counter(f"{r['code']} {r['name']}" for r in traded)) or "全空仓")
if traded:
    rets = [r["return_pct"] for r in traded if r["return_pct"] is not None]
    if rets:
        print(f"已开仓 {len(rets)} 次：收益 {rets}  均值 {sum(rets) / len(rets):.3f}%  "
              f"极差 {max(rets) - min(rets):.3f}pp")
        print(f"含空仓的日均收益：{sum(rets) / n:.3f}%（空仓按 0 计）")
json.dump(rows, open(f"runs/repeat_{date}.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"\n明细落盘：runs/repeat_{date}.json")
