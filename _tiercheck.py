"""核验：判档能力是否完好 —— 只是不再由提示词替模型判定"哪档算强/弱"。"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from src.llm import prompts as P

a4 = P.agent4("电子、银行")
a42 = P.agent42()

print("=== Agent4 现在还用不用判档？===")
can_judge = [
    "P1 强制性", "C1 池内公司", "E1 主体在池外", "E2 主体在池外",
    "E3 主体在池外", "E4 纯宏观", '"tier": "<P1..E4',
]
for kw in can_judge:
    print(f"  {kw:26} {kw in a4}")

print("\n=== 现在【没有】的东西（本次删除的）===")
removed = ["强档 = ", "弱档 = ", "E3 是弱档", "单条即可支撑开仓", "不足以开仓"]
for kw in removed:
    print(f"  {kw:22} {kw in a4}")

print("\n=== Agent4 输出契约 ===")
for line in a4.splitlines()[-4:]:
    print("  ", line)

print("\n=== Agent42 是否仍可调档 ===")
for kw in ["可以调整档位", "升档 / 降档", "不得修改行业标记", "不得移除任何消息"]:
    print(f"  {kw:22} {kw in a42}")

print("\n=== Agent4 提示词里的 P/C/E 四档定义（节选，证明判档依据仍在）===")
for line in a4.splitlines():
    s = line.strip()
    if s.startswith(("P1", "P2", "P3", "P4", "C1", "C2", "C3", "C4", "E1", "E2", "E3", "E4")):
        print("  ", line)
