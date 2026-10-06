"""把每个 Agent 实际发出的提示词渲染出来（含 system + 一条真实 user 样例）。

用法：`python showprompts.py > PROMPTS.md`
注意 stdout 必须设置 newline="\n" —— 否则 Windows 会把换行翻译成 CRLF，
而 .gitattributes 规定 *.md 用 LF，结果是每次生成都产生整文件 diff。
"""

import sys

# newline="\n" 是必须的（见上）；encoding 固定 utf-8 以匹配仓库约定。
sys.stdout.reconfigure(encoding="utf-8", newline="\n")

from src import configs
from src.llm import prompts as P
from src.llm.client import MODELS

stocks, etfs = configs.load_stocks(), configs.load_etfs()

# 取自 2026-09-10 那轮的真实条目
N1 = {"code": "202609103870575524", "showTime": "2026-09-10 06:12:00",
      "title": "三部门：开展2026年度享受增值税加计抵减政策的工业母机企业清单制定工作",
      "content": "工信部、财政部、税务总局联合发布通知，开展2026年度享受增值税加计抵减政策的工业母机企业清单制定工作。"}
N2 = {"code": "202609093868712056", "showTime": "2026-09-09 21:33:00",
      "title": "业内人士：云端AI拉动需求 成熟制程芯片供需紧张或持续至2028年",
      "content": "业内消息称，云端AI需求拉动，成熟制程芯片供需紧张状况可能持续至2028年。"}
N3 = {"code": "202609103869674459", "showTime": "2026-09-10 08:20:00",
      "title": "美联储主席：通胀仍有黏性 不排除进一步加息",
      "content": "美联储主席表示，通胀仍有黏性，若数据不支持，将不排除进一步加息。"}
WORK = []
for n, cat, ind, tier, direction in (
        (N1, "国家政策", "机械设备", "P1", ""),
        (N2, "涨价与供给", "电子", "C3", ""),
        (N3, "外围权威机构/国家发言", "外围", "", "利空")):
    WORK.append({**n, "category": cat, "summary": n["title"], "industry": ind,
                 "tier": tier, "direction": direction})

TOPN = ["机械设备", "电子", "汽车", "社会服务", "医药生物"]
IND = [w for w in WORK if w["industry"] != "外围"]
PER = [w for w in WORK if w["industry"] == "外围"]

AGENTS = [
    ("Agent1 分类", "agent1", P.agent1(),
     "分类下列快讯：\n" + "\n".join(
         f"[{i}] code={n['code']} {n['showTime']}\n标题：{n['title']}\n正文：{n['content']}"
         for i, n in enumerate([N1, N2, N3]))),
    ("Agent2 摘要", "agent2", P.AGENT2,
     "为下列快讯写摘要：\n" + "\n".join(
         f"[{i}] code={n['code']} {n['showTime']}\n标题：{n['title']}\n正文：{n['content']}"
         for i, n in enumerate([N1, N2, N3]))),
    ("Agent3 行业归属", "agent3", P.agent3(stocks.industry_map_text()),
     "为下列摘要指定唯一行业归属：\n" + "\n".join(
         f"[{i}] code={n['code']}\n摘要：{n['summary']}" for i, n in enumerate(WORK))),
    ("Agent4 判级", "agent4", P.agent4("机械设备、电子"),
     "为下列行业消息判级：\n" + "\n".join(
         f"[{i}] code={n['code']} 行业={n['industry']}\n标题：{n['title']}\n摘要：{n['summary']}"
         for i, n in enumerate(IND))),
    ("Agent4 外围方向", "agent4", P.agent4("外围"),
     "为下列外围消息标记唯一行业与方向（不评级）：\n" + "\n".join(
         f"[{i}] code={n['code']}\n标题：{n['title']}\n摘要：{n['summary']}"
         for i, n in enumerate(PER))),
    ("Agent42 档位审查", "agent42", P.agent42(),
     "复核下列判级：\n" + "\n".join(
         f"[{i}] code={n['code']} 行业={n['industry']} "
         f"档位={n.get('tier') or '（外围无档位）'} 方向={n.get('direction') or '（无）'}\n"
         f"标题：{n['title']}\n摘要：{n['summary']}" for i, n in enumerate(WORK))),
    ("第3关后 Agent5 证据复审", "agent5", P.agent5(),
     "\n".join(f"- code={n['code']} 档位={n['tier']} {n['title']}\n  摘要：{n['summary']}"
               for n in WORK)),
    ("第4关前 Agent52 消息→个股归属", "agent52",
     P.agent52("电子", stocks.candidates_text(["电子"])),
     "为下列精选摘要指出它指向哪只候选股票：\n"
     + "\n".join(f"[{i}] code={n['code']} [{n['tier']}] {n['title']}\n    摘要：{n['summary']}"
                 for i, n in enumerate(WORK))),
    ("Agent6 选股", "agent6", P.agent6(stocks.candidates_text(TOPN)),
     "【精选证据】\n（此处为 Agent5 的精选结果 + 外围原文，略）\n\n"
     f"入选的 5 个行业：{'、'.join(TOPN)}"),
    ("Agent62 选 ETF", "agent62", P.agent62(etfs.candidates_text(["电子"])),
     "【精选证据】\n（此处为 Agent5 的精选结果 + 外围原文，略）\n\n"
     f"入选的 5 个行业：{'、'.join(TOPN)}"),
    ("审计", "audit", P.audit(P.gap_enum(), "无"),
     "【精选证据】…\n【外围…】…\n【待审方案】\n"
     '{"decision": "买入", "code": "002008", "name": "大族激光", "industry": "机械设备", …}'),
]

def render() -> str:
    """渲染成 Markdown 文本。"""
    out = []
    for name, key, system, user in AGENTS:
        model, thinking, effort = MODELS[key]
        out.append("#" * 78)
        out.append(f"# {name}")
        out.append(f"# 模型={model}  thinking={thinking}  effort={effort or '—'}"
                   f"  system {len(system)} 字 / user 样例 {len(user)} 字")
        out.append("#" * 78)
        out.append("---------- SYSTEM ----------")
        out.append(system)
        out.append("---------- USER（样例） ----------")
        out.append(user)
    return "\n\n".join(out) + "\n"


if __name__ == "__main__":
    # 【默认写文件，而不是打到 stdout】原因：PowerShell 的 `>` 重定向会由 PowerShell
    # 自己以 UTF-16LE 写文件（含 BOM），Python 侧怎么设置都没用 —— 结果 PROMPTS.md
    # 变成二进制、每次生成都整文件 diff。所以这里直接写文件，杜绝那个坑。
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="渲染各 Agent 的提示词")
    ap.add_argument("--out", default="PROMPTS.md", help="输出文件（默认 PROMPTS.md）")
    ap.add_argument("--stdout", action="store_true",
                    help="打到标准输出（仅在确定终端能正确处理 UTF-8 时用）")
    a = ap.parse_args()
    text = render()
    if a.stdout:
        sys.stdout.write(text)
    else:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"已生成 {a.out}（{len(text)} 字符，UTF-8 / LF）")
