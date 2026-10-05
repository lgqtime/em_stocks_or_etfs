"""核验：C 段与 E 段是否已逐字回退到原版（ce46200），以及我加的内容是否清除。"""

import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

from src.llm import prompts as P

orig = subprocess.run(["git", "show", "ce46200:src/llm/prompts.py"],
                      capture_output=True).stdout.decode("utf-8")
new4 = P.agent4("X")
new42 = P.agent42()
FENCE = chr(34) * 3


def block(txt, start, end):
    i = txt.find(start)
    if i < 0:
        return "(未找到 " + start + ")"
    j = txt.find(end, i)
    return txt[i:j if j > 0 else i + 400].strip()


pairs = [
    ("C 段", "C 企业类", "E 其他事件类"),
    ("E 段", "E 其他事件类", FENCE),
]
for name, s, e in pairs:
    a, b = block(new4, s, e), block(orig, s, e)
    print(f"=== {name} 与原版逐字一致: {a == b}")
    if a != b:
        print("--- 新版 ---")
        print(a)
        print("--- 原版 ---")
        print(b)

print()
print("=== 我加的内容是否已清除 ===")
mine = ["只适用于主体", "不得判 C4", "一步可定位", "不需要任何额外推理步骤",
        "不要求点名池内公司", "E3 是弱档", "大行获注资", "受益关系能不能一步定位",
        "强档 = ", "弱档 = "]
for kw in mine:
    print(f"  {kw:24} agent4={kw in new4!s:5} agent42={kw in new42!s:5}")

print()
print("=== 保留项（应仍在）===")
keep = ["零未来信息约束", "P1 强制性", "E1 主体在池外", "E4 纯宏观",
        '"tier": "<P1..E4', "可以调整档位", "不得移除任何消息"]
for kw in keep:
    print(f"  {kw:24} agent4={kw in new4!s:5} agent42={kw in new42!s:5}")

print()
print("=== 花括号自查 ===")
for n, s in (("agent4", new4), ("agent42", new42)):
    print(f"  {n}: 双括号残留={'{{' in s or '}}' in s}")
