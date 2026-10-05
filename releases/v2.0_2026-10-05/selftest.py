"""最小可运行自检：三关硬过滤 + 第3关排序 + 提示词渲染 + 行情口径。

    python _selftest.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from src import configs
from src.filter import hard
from src.llm import prompts as P

# --- 第0关 ------------------------------------------------------------------
bl = configs.load_blacklist()
news = [
    {"code": "a", "title": "正常快讯：某公司中标10亿元订单", "content": "正文"},
    {"code": "b", "title": "券商研报看好某板块", "content": "正文"},          # 黑名单词
    {"code": "c", "title": "短", "content": "长" * 900},                      # 超长
    {"code": "d", "title": "", "content": ""},                                # 空
    {"code": "e", "title": "第20届半导体博览会将于10月20日举办", "content": ""},  # 会议预告
    {"code": "f", "title": "某公司在制造业大会上宣布量产新一代芯片", "content": ""},  # 实质内容，保留
    {"code": "g", "title": "正常快讯：某公司中标10亿元订单。", "content": "正文"},   # 归一化后与 a 相同
]
kept, dropped = hard.hard_filter_0(news, bl)
assert [k["code"] for k in kept] == ["a", "f"], [k["code"] for k in kept]
reasons = {d["code"]: d["drop_reason"].split("[")[0] for d in dropped}
assert reasons["b"].startswith("黑名单词"), reasons
assert reasons["c"].startswith("超长"), reasons
assert reasons["d"] == "空内容", reasons
assert reasons["e"] == "会议日程预告", reasons
assert reasons["g"] == "标题去重", reasons

# --- 第1关 ------------------------------------------------------------------
names = ["比亚迪", "宁德时代"]
k1, d1 = hard.hard_filter_1([
    {"code": "1", "title": "比亚迪签下大单", "category": "公司合作"},
    {"code": "2", "title": "两家小公司签约", "category": "公司合作"},
    {"code": "3", "title": "某地发生爆炸", "category": "外围突发事件"},
    {"code": "4", "title": "工信部印发规划", "category": "国家政策"},
    {"code": "5", "title": "某地补贴", "category": "地区政策"},
], names)
assert [x["code"] for x in k1] == ["1", "4", "5"], k1
assert {x["code"]: x["drop_reason"].split("[")[0] for x in d1}["3"] == "整类剔除"

# --- 第2关 ------------------------------------------------------------------
k2, d2, inds = hard.hard_filter_2([
    {"code": "1", "industry": "电子"}, {"code": "2", "industry": "其他"},
    {"code": "3", "industry": ""}, {"code": "4", "industry": "外围"},
])
assert [x["code"] for x in k2] == ["1", "4"], k2
assert inds == ["电子"], inds

# --- 第3关排序：有无强档 → 强档数 → 最高档位 → 弱档数 -----------------------
tiers = {"甲": ["P4", "P4", "P4", "P4"], "乙": ["C3"], "丙": ["C1"], "丁": ["P1", "E1"],
         "戊": ["C2", "C2"]}
order = ["甲", "乙", "丙", "丁", "戊"]
ranked = [r["industry"] for r in hard.rank_industries(tiers, order)]
assert ranked == ["丁", "戊", "丙", "甲", "乙"], ranked   # 强档优先；同强档数比最高档；无强档比弱档数

# --- 提示词渲染 --------------------------------------------------------------
assert "1. 国家政策" in P.agent1() and "14. 其他-杂/弱信号" in P.agent1()
assert "电子：半导体" in P.agent3("电子：半导体")
a4 = P.agent4("电子")
assert "利好 / 利空 / 中性 / 方向依存" in a4 and "行业清单：电子" in a4
assert '"items"' in a4 and "{{" not in a4, "花括号转义泄漏"
assert "P1 / P2 / C1 / C2 / E1 / E2" in P.agent5()
assert "- X(000001)" in P.agent6("- X(000001)")
assert "无强档支撑" in P.gap_enum()
assert "已定价" in P.retry("已定价", "d", "m", "p")

# --- 基础文件 ---------------------------------------------------------------
s, e = configs.load_stocks(), configs.load_etfs()
assert s.count == 59 and len(s.level1_names) == 28, (s.count, len(s.level1_names))
assert e.count == 48 and len(e.level1_names) == 21, (e.count, len(e.level1_names))
assert s.find("002594").name == "比亚迪"
assert s.find("比亚迪").code == "002594"
assert s.find("999999") is None
assert "电子：半导体" in s.industry_map_text()
assert "- 通富微电(002156)" in s.candidates_text(["电子"])

# --- 行情口径 ---------------------------------------------------------------
from src import market
from src.http import Client
c = Client()
ev = market.evaluate(c, "002594", "2026-09-10")
assert ev["entry_date"] == "2026-09-10" and ev["exit_date"] == "2026-09-11", ev
assert abs(ev["return_pct"] - (83.17 / 85.11 - 1) * 100) < 0.01, ev
assert "error" in market.evaluate(c, "002594", "2026-09-12")      # 周六
assert market.next_trading_day(c, "2026-09-10") == "2026-09-11"

print("OK 全部自检通过")
