"""不调 LLM 的功能测试：用假 LLM 验 Agent5 的配额裁剪是否严格。

覆盖：
- 电子上限 8、其余 5
- 外围按方向分桶 3/3/2 且允许不满
- 强档不豁免去重、但裁剪时优先保留强档
- 允许不满（不足上限不补）
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from src import configs, pipeline
from src.llm.client import Result

TOP = ["电子", "银行", "汽车", "机械设备", "医药生物"]


def mk(code, industry, tier, ts, direction=""):
    return {"code": code, "title": f"标题{code}", "summary": f"摘要{code}",
            "industry": industry, "tier": tier, "direction": direction,
            "ts": ts, "category": "国家政策"}


def build():
    recs = []
    # 电子：12 条（含强档），应裁到 8
    for i in range(12):
        recs.append(mk(f"E{i:02d}", "电子", "C1" if i < 3 else "E3", 100 + i))
    # 银行：8 条，应裁到 5
    for i in range(8):
        recs.append(mk(f"B{i:02d}", "银行", "P4", 200 + i))
    # 汽车：2 条（不满 5，应保留 2）
    for i in range(2):
        recs.append(mk(f"A{i:02d}", "汽车", "C3", 300 + i))
    # 外围：利好 6 / 利空 5 / 中性 4 → 应裁到 3/3/2
    for i in range(6):
        recs.append(mk(f"P{i:02d}", "外围", "", 400 + i, "利好"))
    for i in range(5):
        recs.append(mk(f"N{i:02d}", "外围", "", 500 + i, "利空"))
    for i in range(4):
        recs.append(mk(f"Z{i:02d}", "外围", "", 600 + i, "中性"))
    return recs


class FakeLLM:
    """假装去重：保留除每行业第一条以外的全部，制造"上游过剩"以检验裁剪。"""
    workers = 1

    def __init__(self):
        self.calls = []

    def call(self, agent, system, user, *, tag=""):
        import json
        recs = build()
        ind = {}
        for r in recs:
            if r["industry"] != "外围":
                ind.setdefault(r["industry"], []).append(r["code"])
        # 只在大桶里丢一条（模拟去重）；小桶不动，用于验证"允许不满"
        keep = {"industries": [{"industry": k,
                                "keep": (v[1:] if len(v) > 5 else v),
                                "dropped": [], "note": ""} for k, v in ind.items()],
                "periphery_keep": [r["code"] for r in recs if r["industry"] == "外围"],
                "periphery_note": "外围整理"}
        self.calls.append(tag)
        return Result(text=json.dumps(keep, ensure_ascii=False), reasoning="",
                      prompt_tokens=0, completion_tokens=0, seconds=0.0, tag=tag)

    def usage(self):
        return {}


class FakeCtx:
    def __init__(self):
        self.rules = configs.load_tier_rules()
        self.stocks = configs.load_stocks()
        self.quotes = []
        self.llm = FakeLLM()
        self.date = "2026-09-10"
        self.saved = {}
        self.meta = {"stages": {}}
        self.industries = TOP

    def stage_input(self, module, upstream, loader):
        return build()

    def save(self, rel, obj):
        self.saved[rel] = obj

    def mark(self, stage, **kw):
        self.meta["stages"][stage] = kw

    def load(self, rel):
        return self.saved.get(rel, [])


ctx = FakeCtx()
selected, per_note, data = pipeline.stage_agent5(ctx, None, TOP)

by_ind = {}
by_dir = {}
for r in selected:
    if r["industry"] == "外围":
        by_dir[r["direction"]] = by_dir.get(r["direction"], 0) + 1
    else:
        by_ind[r["industry"]] = by_ind.get(r["industry"], 0) + 1

print("按行业入选:", by_ind)
print("外围按方向入选:", by_dir)
print("保存的配额:", ctx.saved["analysis/agent5_selected.json"]["quotas"])
print("外围实际计数:", ctx.saved["analysis/agent5_selected.json"]["periphery_counts"])
print()

assert by_ind.get("电子") == 8, f"电子应 8 条，实际 {by_ind.get('电子')}"
assert by_ind.get("银行") == 5, f"银行应 5 条，实际 {by_ind.get('银行')}"
assert by_ind.get("汽车") == 2, f"汽车不满应保留 2 条，实际 {by_ind.get('汽车')}"
assert "机械设备" not in by_ind, "无消息行业不应凭空出现"
assert by_dir.get("利好") == 3, by_dir
assert by_dir.get("利空") == 3, by_dir
assert by_dir.get("中性") == 2, by_dir

# 裁剪优先级：同一行业里强档必须排在弱档前面进入配额
e_codes = {r["code"] for r in selected if r["industry"] == "电子"}
assert {"E01", "E02"} <= e_codes, f"强档被误裁：{sorted(e_codes)}"
assert e_codes == {"E01", "E02", "E06", "E07", "E08", "E09", "E10", "E11"}, sorted(e_codes)
# 强档不豁免去重：fake 把 E00（C1）去掉了，不得被强行补回（旧代码会补）
assert "E00" not in e_codes, "强档不应豁免去重被强行补回"
# 弱档 E06..E11 与强档 E01/E02 同处 8 条配额内，但排序时强档在前
ranked = sorted([r for r in selected if r["industry"] == "电子"],
                key=pipeline._rank_key)
assert [r["tier"] for r in ranked][:2] == ["C1", "C1"], [r["tier"] for r in ranked]

print("OK Agent5 配额裁剪全部通过（未调用任何真实 LLM）")
