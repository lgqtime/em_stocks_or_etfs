"""按官方单价，用各次运行的真实 token 账算成本。"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

# 官方价格（元 / 百万 tokens）：空闲时段 / 高峰时段
# 高峰：北京时间周一至周五 9:00-12:00、14:00-18:00（不含法定节假日）
FLASH = {"hit": (0.02, 0.04), "miss": (1.0, 2.0), "out": (4.0, 8.0)}
PRO = {"hit": (0.15, 0.30), "miss": (4.5, 9.0), "out": (13.5, 27.0)}
M = 1_000_000

# 真实用量（来自 runs/<日期>/meta.json 的 usage）
AGENT_MODEL = {"agent1": "flash", "agent2": "flash", "agent3": "pro", "agent4": "pro",
               "agent42": "pro", "agent5": "pro", "agent6": "pro", "agent62": "pro",
               "audit": "pro"}


def price(agent, pin, pout, *, slack=False):
    """slack=False → 高峰价（索引 0）；slack=True → 空闲时段价（索引 1）。"""
    p = FLASH if AGENT_MODEL[agent] == "flash" else PRO
    i = 1 if slack else 0
    return pin / M * p["miss"][i] + pout / M * p["out"][i]


RUNS = {
    # 04-10：热跑（批量提取层全部命中 LLM 缓存，只重算了决策层）
    "09-10 热跑": {"wall": 805, "calls": 20, "cached": 12, "rows": [
        ("agent1", 13866, 3403), ("agent2", 6206, 2274), ("agent3", 3355, 4107),
        ("agent4", 5265, 18398), ("agent42", 4171, 30694), ("agent5", 2362, 1588),
        ("agent6", 8165, 23161), ("agent62", 8589, 19132), ("audit", 14882, 29648)]},
    # 09-11：全量冷跑（0 命中）
    "09-11 冷跑": {"wall": 905, "calls": 19, "cached": 0, "rows": [
        ("agent1", 18011, 5370), ("agent2", 7522, 2765), ("agent3", 3812, 5665),
        ("agent4", 5448, 20467), ("agent42", 4325, 19052), ("agent5", 2251, 7467),
        ("agent6", 11475, 25986), ("audit", 9924, 19264)]},
    # 最早那次冷跑（并行化之前，40 分钟）
    "首次冷跑 40min": {"wall": 2383, "calls": 45, "cached": 0, "rows": [
        ("agent1", 70271, 19428), ("agent2", 26506, 9055), ("agent3", 11477, 21204),
        ("agent4", 16535, 60917), ("agent42", 14186, 72009), ("agent5", 4815, 7714),
        ("agent6", 11103, 17141), ("audit", 26437, 20482), ("agent62", 18394, 43843)]},
}

print("=" * 74)
for name, r in RUNS.items():
    pin = sum(x[1] for x in r["rows"])
    pout = sum(x[2] for x in r["rows"])
    tot_peak = sum(price(a, i, o) for a, i, o in r["rows"])
    tot_slack = sum(price(a, i, o, slack=True) for a, i, o in r["rows"])
    print(f"\n【{name}】墙钟 {r['wall']}s（{r['wall'] / 60:.1f} 分钟）  "
          f"调用 {r['calls']} 次（缓存 {r['cached']}）")
    print(f"  输入 {pin:,} tok   输出 {pout:,} tok   合计 {pin + pout:,} tok")
    print(f"  成本：高峰 ¥{tot_peak:.3f}   空闲时段 ¥{tot_slack:.3f}")
    print("  按 Agent 拆分（高峰/空闲）:")
    for a, i, o in sorted(r["rows"], key=lambda x: -price(x[0], x[1], x[2])):
        pk, sl = price(a, i, o), price(a, i, o, slack=True)
        share = pk / tot_peak * 100
        print(f"    {a:8} {AGENT_MODEL[a]:5} in={i:>6,} out={o:>6,}  "
              f"¥{pk:.3f} / ¥{sl:.3f}   占比 {share:4.1f}%")

# 单次预测的两种口径
print("\n" + "=" * 74)
h = RUNS["09-10 热跑"]
hot_peak = sum(price(a, i, o) for a, i, o in h["rows"])
hot_slack = sum(price(a, i, o, slack=True) for a, i, o in h["rows"])
c = RUNS["09-11 冷跑"]
cold_peak = sum(price(a, i, o) for a, i, o in c["rows"])
cold_slack = sum(price(a, i, o, slack=True) for a, i, o in c["rows"])
f = RUNS["首次冷跑 40min"]
f_peak = sum(price(a, i, o) for a, i, o in f["rows"])
f_slack = sum(price(a, i, o, slack=True) for a, i, o in f["rows"])

print("\n" + "=" * 74)
print("单价口径：高峰 = 周一至周五 9:00-12:00 / 14:00-18:00；其余时段（含周末）为空闲，价格减半")
print("=" * 74)
hdr = f"{'情形':<26}{'墙钟':>9}{'高峰 ¥':>10}{'空闲 ¥':>10}"
print(hdr)
print("-" * 74)
rows = [
    ("① 缓存命中（批量层未变）", h["wall"], hot_peak, hot_slack),
    ("② 全量重算（语料变了/首次）", c["wall"], cold_peak, cold_slack),
    ("③ 首轮未并行化那次", f["wall"], f_peak, f_slack),
]
for name, w, pk, sl in rows:
    print(f"{name:<26}{w / 60:>6.1f} 分{pk:>10.2f}{sl:>10.2f}")

print("\n回测（每日都按 ② 全量重算）：")
print(f"{'天数':<8}{'总墙钟':>12}{'高峰 ¥':>10}{'空闲 ¥':>10}")
for n in (1, 5, 10, 22):
    print(f"{n:<8}{c['wall'] * n / 3600:>9.1f} 小时{cold_peak * n:>10.2f}{cold_slack * n:>10.2f}")

