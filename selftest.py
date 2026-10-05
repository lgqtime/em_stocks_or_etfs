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
assert [r["best_tier"] for r in hard.rank_industries(tiers, order)][0] == "P1", \
    "同强档数时应取最高档位"

# 同号同级的政策优先序（用户定案 A）：
#   P1 > C1 = E1 > P2 > C2 = E2 > P3 = C3 = E3 > P4 = C4 = E4
# 政策优先只在前两档成立；第 3、4 档是弱档，与同号 C/E 并列。
assert hard.tier_rank("P1") < hard.tier_rank("C1") == hard.tier_rank("E1"), "P1 应优先于 C1=E1"
assert hard.tier_rank("P2") < hard.tier_rank("C2") == hard.tier_rank("E2"), "P2 应优先于 C2=E2"
assert hard.tier_rank("P3") == hard.tier_rank("C3") == hard.tier_rank("E3"), \
    "第3档：P3 与 C3/E3 并列（A 方案在 P3 处破例）"
assert hard.tier_rank("P4") == hard.tier_rank("C4") == hard.tier_rank("E4"), \
    "第4档：P4 与 C4/E4 并列"
# 号小优先于号大（跨字母亦然：P2 优先于 C1 之外的一切第3档）
assert hard.tier_rank("P1") < hard.tier_rank("P2") < hard.tier_rank("P3") < hard.tier_rank("P4")
assert hard.tier_rank("E1") < hard.tier_rank("C2"), "E1（强档）应优先于 C2"
# 完整序列必须与用户指定的一致
_EXPECT = ["P1", "C1", "E1", "P2", "C2", "E2", "P3", "C3", "E3", "P4", "C4", "E4"]
assert sorted(_EXPECT, key=lambda t: (_EXPECT.index(t), hard.tier_rank(t))) == _EXPECT
_ordered = sorted(_EXPECT, key=lambda t: (hard.tier_rank(t), _EXPECT.index(t)))
assert _ordered == _EXPECT, f"优先序与规格不符：{_ordered}"
# 三个行业各持同号强档 → 持 P1/P2 的排前，持 C1/E1 的并列
_t = {"甲": ["E1"], "乙": ["P1"], "丙": ["C1"]}
_r = [r["industry"] for r in hard.rank_industries(_t, ["甲", "乙", "丙"])]
assert _r[0] == "乙", f"持 P1 的应排第一，实际 {_r}"
assert set(_r) == {"甲", "乙", "丙"}
# pipeline 的档位位次必须与 hard 同源，否则 Agent5 配额与第3关会口径不一致
from src import pipeline as _PL
assert _PL.T_RANK == {t: hard.tier_rank(t) for t in _PL.T_RANK}, "T_RANK 与 tier_rank 不一致"

# --- 提示词渲染 --------------------------------------------------------------
assert "1. 国家政策" in P.agent1() and "14. 其他-杂/弱信号" in P.agent1()
# 提示词里的 JSON 示例必须是【合法 JSON】，不能带转义用的双花括号。
for nm, txt in (("agent1", P.agent1()), ("agent2", P.AGENT2), ("agent3", P.agent3("X")),
                ("agent4", P.agent4("X")), ("agent42", P.agent42()), ("agent5", P.agent5()),
                ("agent6", P.agent6("- X(000001)")), ("audit", P.audit(P.gap_enum(), "无"))):
    for line in txt.splitlines():
        assert not line.strip().startswith("{{"), f"{nm} 提示词残留双花括号：{line[:60]}"
        assert "{{" not in line, f"{nm} 提示词残留双花括号：{line[:60]}"
assert "电子：半导体" in P.agent3("电子：半导体")
a4 = P.agent4("电子")
assert "利好 / 利空 / 中性 / 方向依存" in a4 and "行业清单：电子" in a4
assert '"items"' in a4 and "{{" not in a4, "花括号转义泄漏"
assert "- X(000001)" in P.agent6("- X(000001)")
assert "无强档支撑" in P.gap_enum()
assert "已定价" in P.retry("已定价", "d", "m", "p")

# --- ① 审计硬判据：否决必须点名缺哪一项数据，禁用弹性措辞 --------------------
_aud = P.audit(P.gap_enum(), "无")
assert "硬判据" in _aud and "无效否决" in _aud, "审计提示词缺硬判据"
assert "缺 <具名数据项>" in _aud, "审计提示词缺 gap_detail 模板"
assert "属(a)" in _aud or "属 a" in _aud, "审计未要求区分『消息本就没有』与『方案未引用』"
for _bad in ("证据不足", "证据不够充分", "感觉不够强", "再想想"):
    assert _bad in _aud, f"审计提示词未列出禁用措辞：{_bad}"
assert "宁可放行" in _aud, "审计缺『说不出具体缺什么就不得否决』的自检"
_he = configs.load_tier_rules()["audit_protocol"]["hard_evidence"]
assert len(_he["gap_type_requirements"]) == 8, "8 种 gap_type 都应加附加要求"
assert _he["gap_detail"]["examples_forbidden"], "禁用示例不能为空"

# --- 历史外盘（回测用）：不得读取晚于 T 日的数据 ------------------------------
from src.collect import overseas as _ov

assert set(_ov.SRC) == set(configs.load_tier_rules() and
                           __import__("src.collect.quotes", fromlist=["SYMBOLS"]).SYMBOLS), \
    "历史源必须覆盖实时源的 10 个指标"
assert _ov.MIN_DATE["us"] == "2004-01-02" and _ov.MIN_DATE["fut"] == "2016-10-05"
assert _ov.RELIABILITY["dji"] == "high" and _ov.RELIABILITY["hsi"] == "high", \
    "现金指数应标 high"
assert _ov.RELIABILITY["a50"] == "roll_risk", "期货连续合约必须标出换月风险"
from src.http import Client as _C
_q = _ov.collect_quotes_hist(_C(), "2026-09-10")
assert len(_q) == 10, _q
assert not [x for x in _q if x.get("missing")], f"历史外盘有缺失：{[x['name'] for x in _q if x.get('missing')]}"
assert all(x["time"] < "2026-09-10" for x in _q), \
    f"历史外盘不得用到 T 日及以后的数据：{[x['time'] for x in _q]}"
_live = __import__("src.collect.quotes", fromlist=["collect_quotes"]).collect_quotes(_C())
assert set(_q[0]) >= set(_live[0]), "历史源必须与实时源输出同构（下游才能不改）"

# --- ③ 决策层固定 temperature=0 ---------------------------------------------
from src.llm.client import GREEDY, NO_CACHE
assert GREEDY == NO_CACHE == {"agent6", "agent62", "audit"}, (GREEDY, NO_CACHE)
assert "agent4" not in GREEDY and "agent1" not in GREEDY, "批量提取层不应固定 temperature"

# --- E 档口径（B：一步可定位）与零未来信息约束 --------------------------------
assert "不需要任何额外推理步骤" in a4, "E1 的『一步可定位』判据缺失"
assert "不要求点名池内公司" in a4, "E1 未说明可不点名"
# 档位强弱归类【故意不写进提示词】：让模型自己按证据强度判断能否支撑开仓。
# （原版就没有；实测 09-11 靠单条 E3 开仓并过审计 +8.718%。）
assert "强档 = " not in a4 and "弱档 = " not in a4, "提示词不应出现强弱档归类表"
assert "E3 是弱档" not in a4, "不应在提示词里替模型判定 E3 的强弱"
assert "E3 是弱档" not in P.agent42(), "Agent42 不应把 E3 当错误去纠正"
# C/E 边界：C 类只适用于主体在池内的消息；资本运作按【动作主体】三方分流
assert "只适用于主体本身就在池内的消息" in a4, "Agent4 未声明 C 类的主体边界"
assert "走 P 类" in a4, "Agent4 缺『政府出资是政策不是公司行为』的分流"
assert "只适用于主体就在池内" in P.agent42(), "Agent42 未复核 C/E 边界"

# P 与 C/E 互斥且【先判 P】：政策主体是政府，不因受益方在池外而降成 E
assert "判定顺序" in a4 and "最先判 P" in a4, "Agent4 未声明先判 P 的顺序"
assert "P 类不受主体位置影响" in a4, "Agent4 未声明 P 不受主体位置影响"
assert "只有【不是 P】时" in a4, "Agent4 未说明非 P 才分支到 C/E"
assert "不得因为受益方在池外就把一条政策降成 E" in P.agent42(), "Agent42 未复核 P 分支"
_rules_p = configs.load_tier_rules()["judging_rules"]
for _k in ("_order", "_p_ignores_subject_position", "_capital_operation_triage"):
    assert _k in _rules_p, f"tier_rules 的 judging_rules 缺 {_k}"
_cp = [x for x in configs.load_tier_rules()["code_prejudge"]["recommended"]
       if x["kind"].startswith("融资资本运作")]
# 该段【不进提示词】（已断言），只是给"代码预判"留的规格；真正的分流顺序在提示词里。
assert _cp, "code_prejudge 应保留融资资本运作的规格"
assert not [x for x in _cp if x["kind"] == "融资资本运作"], \
    "融资资本运作应已按动作主体拆分为三条分流"
assert "零未来信息约束" in a4 and "发布时间【之后】才知道的任何信息" in a4, "Agent4 缺零未来信息约束"
assert "零未来信息" in P.agent42(), "Agent42 缺零未来信息约束"
assert "零未来信息" in P.agent6("- X(000001)"), "Agent6 缺零未来信息约束"
assert "上限由程序强制" in P.agent5() and "允许不满" in P.agent5()
assert "至多 5 条" not in P.agent5(), "Agent5 仍写死 5 条配额"

# --- 精选配额与排序（条数由代码强制，不靠模型自觉）--------------------------
from src import pipeline as PL
rules = configs.load_tier_rules()
q = rules["summary_quota"]
assert q["by_industry"].get("电子") == 8 and q["default"] == 5, q
assert PL._quota_of(q, "电子") == 8 and PL._quota_of(q, "银行") == 5
buckets = rules["periphery"]["summary_buckets"]
assert (buckets["利好"], buckets["利空"], buckets["中性"]) == (3, 3, 2), buckets
assert "e_tier_rules" in rules and "no_future_leak" in rules
ti = [{"code": "a", "tier": "E3", "ts": 9}, {"code": "b", "tier": "E1", "ts": 1},
      {"code": "c", "tier": "P4", "ts": 5}, {"code": "d", "tier": "C1", "ts": 2}]
assert [x["code"] for x in sorted(ti, key=PL._rank_key)] == ["d", "b", "a", "c"], \
    "排序键应为 强档优先 → 档位号 → 时间倒序"
assert [x["code"] for x in PL._cap(ti, 2)] == ["d", "b"]
assert PL._cap(ti, 0) == [] and len(PL._cap(ti, 99)) == 4

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

# --- 采集时间窗（A 窗 = T-1 18:00–23:00，B 窗 = T 05:00–09:00）---------------
# 补这条断言的原因：曾因 _windows 的偏移算反，使 609 条 09:00 之后的快讯
# 漏进 09-10 那轮预测（未来数据泄露），且两个窗口整体错位。
from src.collect import news as news_mod

w = news_mod._windows("2026-09-10")
assert [(x.strftime("%m-%d %H:%M"), y.strftime("%m-%d %H:%M")) for x, y in w] == \
       [("09-09 18:00", "09-09 23:00"), ("09-10 05:00", "09-10 09:00")], w
assert all(x.utcoffset().total_seconds() == 8 * 3600 for pair in w for x in pair), \
    "窗口必须固定 +08:00，否则本机时区非 UTC+8 时整体平移"
assert news_mod._to_epoch("2026-09-10 09:00:00") == max(y.timestamp() for _, y in w)
assert news_mod._to_epoch("2026-09-09 18:00:00") == min(x.timestamp() for x, _ in w)

print("OK 全部自检通过")
