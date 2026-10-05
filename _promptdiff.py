"""把当前所有提示词与 ce46200 原版逐字对比，确认回退了什么、保留了什么。"""

import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

from src.llm import prompts as P

orig = subprocess.run(["git", "show", "ce46200:src/llm/prompts.py"],
                      capture_output=True).stdout.decode("utf-8")


def extract(txt, name):
    """从源码里取出某个模板的原始文本（不含 format 渲染）。"""
    i = txt.find(f"{name} = ")
    if i < 0:
        return None
    j = txt.find('"""', i)
    k = txt.find('"""', j + 3)
    return txt[j + 3:k]


print("=== 源码级模板对比（当前文件 vs ce46200）===")
cur = open("src/llm/prompts.py", encoding="utf-8").read()
for name in ["AGENT1", "AGENT2", "AGENT3_TMPL", "AGENT4_TMPL", "AGENT42_TMPL",
             "AGENT5_TMPL", "AGENT6_TMPL", "AGENT62_TMPL", "AUDIT_TMPL", "RETRY_TMPL",
             "_TIER_BRIEF", "META_PRINCIPLES", "_DECIDE_COMMON"]:
    a, b = extract(cur, name), extract(orig, name)
    if a is None or b is None:
        print(f"  {name:16} 取不到（当前={a is not None} 原版={b is not None}）")
        continue
    same = a.strip() == b.strip()
    print(f"  {name:16} {'一致' if same else '★已改动'}"
          + ("" if same else f"   （当前 {len(a)} 字符 / 原版 {len(b)} 字符）"))
