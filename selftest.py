"""最小可运行自检：三关硬过滤 + 第3关排序 + 提示词渲染 + 行情口径。

    python -m src selftest   （或直接 python selftest.py）
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from src import configs
from src.filter import hard
from src.llm import prompts as P


def _read_or_empty(rel: str) -> str:
    """读项目内文本文件；缺失返回空串（让断言自然失败并给出可读信息）。"""
    p = Path(rel)
    return p.read_text(encoding="utf-8") if p.exists() else ""


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
assert GREEDY == NO_CACHE == {"agent52", "agent6", "agent62", "audit"}, (GREEDY, NO_CACHE)
assert "agent4" not in GREEDY and "agent1" not in GREEDY, "批量提取层不应固定 temperature"

# --- E 档四步流水线（合并了"已兑现降档"与"渠道判据"两节）------------------
assert "四步流水线" in a4, "Agent4 缺 E 档四步流水线"
for _kw in ("第 1 步", "第 2 步", "第 3 步", "第 4 步", "别处一步就能看到或猜到",
            "外国产业事件", "大项目落地", "价格上涨", "预测或表态", "行情陈述"):
    assert _kw in a4, f"Agent4 四步流水线缺：{_kw}"
assert "涨价函" in a4, "未区分『企业发涨价函』与『价格行情』"
assert "E1 是 E 的上限" in a4, "未限定作用域（不得越出 E 档）"
assert "已兑现降档" not in a4, "旧的『已兑现降档』节未合并掉"
_ep = configs.load_tier_rules()["judging_rules"]["E"]["e_tier_pipeline"]
for _k in ("step1_nature", "step2_time", "step3_channel", "step4_settle"):
    assert _k in _ep, f"e_tier_pipeline 缺 {_k}"
assert _ep["step3_channel"]["a_foreign"]["delta"] == "+2"
assert _ep["step3_channel"]["b_project_landed"]["delta"] == "-2"
assert _ep["step3_channel"]["c_price_up"]["delta"] == "-2"
assert "already_realized_downgrade" not in configs.load_tier_rules()["judging_rules"]["E"]
assert "channel_test" not in configs.load_tier_rules()["judging_rules"]["E"]

# --- 渠道调整【不叠加】：只动一格（用户定案）---------------------------------
assert "不叠加" in a4, "Agent4 未写明『不叠加』"
for _kw in ("只动一格", "最多只移动一格", "总分", "互相抵为 0"):
    assert _kw in a4, f"Agent4 的结算规则缺：{_kw}"
assert "不要在此处单独降档" in a4, "第2步未改为『只提供依据、不单独结算』（会重复扣减）"
assert "step4_no_stacking" in _ep, "配置缺 step4_no_stacking"
assert "只移动一格" in _ep["step4_settle"], "配置未写明只动一格"
assert len(_ep["step4_no_stacking"]["examples"]) == 4
# 用配置声明的例子验证语义：总分取符号，只动一格
def _settle(_init: str, _deltas: list) -> str:
    _total = sum(_deltas)
    _n = int(_init[1]) + (0 if _total == 0 else (-1 if _total > 0 else +1))
    return f"E{max(1, min(4, _n))}"

assert _settle("E3", [2]) == "E2", "外国事件单独适用应升 1 格"
assert _settle("E2", [-2]) == "E3", "涨价单独适用应降 1 格"
assert _settle("E3", [2, -2]) == "E3", "外国+落地应互相抵为 0（不动）"
assert _settle("E3", [2, 2]) == "E2", "两个 +2 仍只升 1 格（不叠加）"
assert _settle("E1", [2]) == "E1", "E1 是上限，不再上抬"
assert _settle("E4", [-2]) == "E4", "E4 是下限，不再下降"
# Agent1 要把"已兑现动作动词"归入该类
assert "已完成的动作动词" in P.agent1() and "动工" in P.agent1(), "Agent1 缺已完成动词判据"
assert "行情播报" not in P.agent1(), "Agent1 引用了不存在的类目名「行情播报」"
from src.filter.hard import CATEGORIES as _CATS
for _c in ("涨价与供给", "公司发布产品/上线模型"):
    assert any(_c in x for x in _CATS), f"Agent1 引用了不存在的类目 {_c}"
# Agent6 不得自行降档
_a6 = P.agent6("- X(000001)")
assert "档位标记是硬事实" in _a6 and "不得自行降档" in _a6, "Agent6 缺『不得自行降档』约束"
# 审计回退三选项，空仓排第一
_rt = P.retry("g", "d", "m", "p")
assert "(1) **空仓**" in _rt and "(2) 坚持原标的" in _rt and "(3) 更换标的" in _rt, "回退未给三个选择"

# --- E 档口径（B：一步可定位）与零未来信息约束 --------------------------------
assert "不需要任何额外推理步骤" in a4, "E1 的『一步可定位』判据缺失"
assert "不要求点名池内公司" in a4, "E1 未说明可不点名"
# 档位强弱归类【故意不写进提示词】：让模型自己按证据强度判断能否支撑开仓。
# （原版就没有；实测 09-11 靠单条 E3 开仓并过审计 +8.718%。）
assert "强档 = " not in a4 and "弱档 = " not in a4, "提示词不应出现强弱档归类表"
assert "E3 是弱档" not in a4, "不应在提示词里替模型判定 E3 的强弱"
assert "E3 是弱档" not in P.agent42(), "Agent42 不应把 E3 当错误去纠正"

# --- 弱档合成门槛 = 五条（不是三条）------------------------------------------
# 实测依据：门槛为三条时，「弱档合成」开的 ETF 仓两次全亏（−4.007% / −5.168%），
# 而强档开仓 4 次 3 正 1 负。用户定案：提高到五条。
_a6_combo = P.agent6("- X")
# 只有真正参与开仓判断的提示词需要写明门槛（Agent4 判级 / Agent6 选股 / Agent62 选 ETF）
# Agent5 是去重与配额层、审计是挑缺口，都不做开仓判断，不要求出现该数字。
for _txt, _name in ((a4, "Agent4"), (_a6_combo, "Agent6"), (P.agent62("- X"), "Agent62"),
                    (P.agent42(), "Agent42")):
    assert "五条" in _txt, f"{_name} 未写明弱档合成门槛为五条"
for _bad in ("三条以上弱档", "不足三条", "第三条弱档", "2 条行业弱档"):
    assert _bad not in _a6_combo, f"Agent6 仍残留旧门槛表述：{_bad}"
    assert _bad not in a4, f"Agent4 仍残留旧门槛表述：{_bad}"
assert "≥5 条" in _read_or_empty("src/pipeline.py"), "证据文本未写明 ≥5 条"
_rules_txt = _read_or_empty("config/tier_rules.json")
assert "五条弱档须来自不同独立事件" in _rules_txt, "配置的元原则 3 未同步为五条"
assert "五条以上（须独立事件且方向一致）可合成开仓" in _rules_txt, "配置 tier_sets 说明未同步"

# --- P 档只针对国内：外国/港澳台 政府与监管机构不走 P -------------------------
# 实测反例：英国 7.08 亿英镑六代机研发、香港金管局贴现窗口 2000 万港元，都曾被判 P1。
for _kw in ("只针对【国内】政策", "中国大陆的政府/监管机构", "香港金管局", "一律不走 P 类"):
    assert _kw in a4, f"Agent4 缺『P 只针对国内』的规则：{_kw}"
assert "只适用于中国大陆的政府/监管机构" in P.agent42(), "Agent42 未同步 P 的国内限定"
_pd = configs.load_tier_rules()["judging_rules"].get("p_domestic_only")
assert _pd and len(_pd["excluded_subjects"]) == 2, "配置缺 p_domestic_only"
# P 档的定义本身不得被改动（用户定案：P 判据保持原样，本条只加适用范围）
assert "P1 强制性、已生效或明确生效日、直接改变经营约束（配额/关税/税率/强制标准/禁限令；" in a4, \
    "P1 档定义被改动了（应保持原样，只加适用范围）"

# --- 地方财政补贴判 P1（用户定案）--------------------------------------------
# 原因：P1 与 P4 在"地方补贴"上原本无定论，同一类消息一轮判 P1、一轮判 P4，
# 行业排名从第 2 掉到第 13。定案：有金额/对象的地方补贴即 P1。
for _kw in ("地方补贴也算 P1", "不因\"它是地方政策\"就一概降 P4", "只有泛泛鼓励"):
    assert _kw in a4, f"Agent4 缺『地方补贴判 P1』规则：{_kw}"
assert "\"是地方政策\"本身不等于 P4" in a4, "P4 段未说明地方政策不等于 P4"
_ps = configs.load_tier_rules()["judging_rules"].get("p1_local_subsidy")
assert _ps and "判 P1" in _ps["P1_when"], "配置缺 p1_local_subsidy"

# --- 泄露检查：提示词与配置不得出现回测期真实案例 -----------------------------
from src.llm import prompts as _P  # noqa: E402

_LEAK = ["小米", "卢伟冰", "村田", "风华高科", "瑞萨", "SK海力士", "摩根大通",
         "兆易创新", "普冉股份", "浪潮信息", "广联达", "阳光电源", "天赐材料",
         "金风科技", "特斯拉", "马斯克", "Terafab", "Alphabet", "OpenAI", "英伟达",
         "容百科技", "滨江集团", "红旗连锁", "登海种业", "海大集团", "地铁设计",
         "云南白药", "凯莱英", "宁波银行", "广发证券", "湛江", "惠州", "3000亿",
         "3600亿", "2000万港元", "7.08亿", "525户", "600万", "5.984", "8.718",
         "MLCC", "电子布", "乙二醇", "多晶硅", "eSIM", "具身智能", "8000亿",
         "存款保险", "贴现窗口", "内幕交易", "第七批", "集采"]
# 注：不再把「特别国债 / 专项债 / 补贴细则」这类【通用政策工具名】列为泄露词 ——
# 它们是 P1 定义本身需要的抽象举例，与具体回测案例无关。
_pblob = "\n".join([a4, _P.AGENT2, _P.agent1(), _P.agent3("X"), _P.agent42(), _P.agent5(),
                    _P.agent52("电子", "- X"), _P.agent6("- X"), _P.agent62("- X"),
                    _P.audit(_P.gap_enum(), "无"), _P.retry("g", "d", "m", "p")])
_lh = [w for w in _LEAK if w in _pblob]
assert not _lh, f"提示词出现回测期真实案例：{_lh}"
_cblob = _read_or_empty("config/tier_rules.json")
_ch = [w for w in _LEAK if w in _cblob]
assert not _ch, f"配置出现回测期真实案例（应移入 docs/KNOWN_ISSUES.md）：{_ch}"

# --- 外国产业事件 +2 仅限【已宣布的具体动作】--------------------------------
for _kw in ("必须是已宣布的具体动作", "很快就会宣布", "高管/机构的观点表态",
            "把消息里的动词换成肯定的过去/完成时"):
    assert _kw in a4, f"Agent4 的 (a) 限定缺：{_kw}"
_af = configs.load_tier_rules()["judging_rules"]["E"]["e_tier_pipeline"][
    "step3_channel"]["a_foreign"]["must_be_announced_action"]
assert len(_af["not_applicable"]) == 3, _af
assert "KNOWN_ISSUES.md" in _af["why"], "(a) 限定的实测证据未指向台账文件"
assert "仅限已宣布的具体动作" in P.agent42(), "Agent42 未同步 (a) 的限定"

# --- P 档保护：已生效/已落地不是减分项 ---------------------------------------
# 用户定案：P 是持续性约束（已生效是 P1 成立条件）；C 是离散事件（已兑现才降档）。
# 实测反例：09-10 财政部注资 P1 被审计以"已落地、缺乏增量催化"否决 → 空仓。
# 该保护作用于【否决 P 档的那一层】（决策 Agent6/62 + 审计 + Agent42 复核），
# 不在 Agent4 —— Agent4 是判级层，它只判档、不否决。
_a6p = P.agent6("- X")
for _kw in ('P 档的"已生效/已落地"不是减分项', "缺乏未定价的增量催化",
            "量级不足以驱动标的", "主体不是国内政府"):
    assert _kw in _a6p, f"决策规则缺 P 档保护：{_kw}"
    assert _kw in P.agent62("- X"), f"Agent62 缺 P 档保护：{_kw}"
_a_txt = P.audit(P.gap_enum(), "无")
assert "P 档专用约束" in _a_txt, "审计缺 P 档专用约束"
assert "用 gap_type = 已定价 否决它" in _a_txt, "审计缺『不得用已定价否决 P』"
assert "主体不在池 否决它" in _a_txt, "审计缺『不得用主体不在池否决 P』"
assert "量级不足以驱动标的" in _a_txt, "审计未保留『量级不足』这一正当否决理由"
assert "P 档已生效不是" in _a_txt, "审计 gap_type 说明未标注 P 档豁免"
assert "P 档复核要点" in P.agent42(), "Agent42 缺 P 档复核要点"
assert "已生效/已落地" in P.retry("g", "d", "m", "p"), "回退提示词缺 P 档保护"
_pp = configs.load_tier_rules()["judging_rules"].get("p_tier_protection")
assert _pp and len(_pp["allowed_rejection_reasons"]) == 3, "配置缺 p_tier_protection"
assert "缺乏未定价的增量催化" in _pp["not_a_reason_to_reject"], "配置未列出被禁用的理由"
_gt = configs.load_tier_rules()["audit_protocol"]["gap_types"]
assert "不得用于 P 档" in _gt["已定价"] and "不得用于 P 档" in _gt["主体不在池"], \
    "gap_types 未标注 P 档豁免"
# --- P 档锁：Agent4 判出的 P 档，下游任何人不得改动 ---------------------------
# 用户定案：Agent42 及后续只能改 E 档与 C 档；P 档锁定。
# 用代码硬拦（提示词约束在本项目已被绕过多次），故这里直接单测该函数。
from src.pipeline import lock_p_tier as _lock

# 下游试图改 P1 → 一律驳回，保持 P1
assert _lock("P1", "E2") == ("P1", True), "P1 被下游改成 E2 应驳回"
assert _lock("P1", "C4") == ("P1", True), "P1 被下游改成 C4 应驳回"
assert _lock("P1", "P3") == ("P1", True), "P1 被下游改成 P3 也应驳回（P 档内部同样锁）"
assert _lock("P2", "P4") == ("P2", True), "P2 → P4 应驳回"
assert _lock("P3", "E1") == ("P3", True), "P3 被下游升成 E1 应驳回"
# 下游不动 P → 放行
assert _lock("P1", "P1") == ("P1", False), "P 档未被改动应放行"
# C / E 档可以改
assert _lock("C1", "E2") == ("E2", False), "C1 → E2 应放行（C/E 可改）"
assert _lock("E1", "E3") == ("E3", False), "E1 → E3 应放行"
assert _lock("C3", "C1") == ("C1", False), "C3 → C1 应放行"
assert _lock("E3", "E1") == ("E1", False), "E3 → E1 应放行"
# 空档位不被误锁
assert _lock("", "E2") == ("E2", False), "原档为空不应锁"
# 提示词也要声明这条（避免模型白费额度去改 P）
_a42p = P.agent42()
assert "不得改动 P 档" in _a42p, "Agent42 未声明不得改 P 档"
assert "E 档与 C 档" in _a42p, "Agent42 未声明可改的范围是 E/C"
assert "P 档锁定" in P.retry("g", "d", "m", "p"), "回退提示词未声明 P 档锁定"
assert "lock_p_tier" in _read_or_empty("src/pipeline.py"), "pipeline 缺 P 档锁实现"

# --- PROMPTS.md 必须是 UTF-8/LF（不是 UTF-16）--------------------------------
# 教训：用 PowerShell 的 `>` 重定向会让 PowerShell 以 UTF-16LE 写文件（含 BOM），
# 结果 PROMPTS.md 变成二进制、每次生成整文件 diff。改为让 showprompts.py 自己写文件。
_pm = Path("PROMPTS.md")
if _pm.exists():
    _pb = _pm.read_bytes()
    assert b"\x00" not in _pb, "PROMPTS.md 含 NUL —— 被 UTF-16 写坏了"
    assert not _pb.startswith((b"\xff\xfe", b"\xfe\xff")), "PROMPTS.md 含 UTF-16 BOM"
    assert b"\r" not in _pb, "PROMPTS.md 含 CR —— 应为 LF"
    _pb.decode("utf-8")
    assert _pb.decode("utf-8").count("---------- SYSTEM ----------") == 11, \
        "PROMPTS.md 未覆盖全部 11 个 agent"
assert 'newline="\\n"' in _read_or_empty("showprompts.py"), \
    "showprompts.py 应显式以 newline='\\n' 写文件"

# --- P1 闸门：股票层被否且理由涉及 P1 → 插入一次 ETF（共用那 3 轮）------------
# 用户设计：股票层 r=1..3；若审计否决理由涉及 P1，插入 Agent62(ETF) 一次并【消耗一轮】；
# ETF 也被否 → 回 Agent6 用剩余轮数。阶段2（原 ETF 层）不走这个钩子，维持原样。
import json as _json  # noqa: E402

from src import configs as _cfg  # noqa: E402


class _FakeRes:
    def __init__(self, obj):
        self._obj = obj
        self.error = None
        self.text = _json.dumps(obj, ensure_ascii=False)

    def json(self):
        return self._obj


class _FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def call(self, agent, system, user, tag=None):
        self.calls.append(f"{agent}@{tag}")
        for i, (wa, wt, obj) in enumerate(self.script):
            if wa == agent and wt == tag:
                self.script.pop(i)
                return _FakeRes(obj)
        return _FakeRes({"decision": "空仓", "reason": "脚本用尽"})


class _FakeCtx:
    def __init__(self, llm):
        self.llm = llm


_A6 = {"decision": "买入", "code": "000636", "name": "风华高科",
       "industry": "电子", "tier_basis": ["P1"], "reason": "P1 政策"}
_A62 = {"decision": "买入", "code": "159997", "name": "电子ETF天弘",
        "industry": "电子", "tier_basis": ["P1"], "reason": "ETF 承接"}
_OK = {"verdict": "认可", "gap_type": "", "gap_detail": "", "what_would_change_my_mind": ""}
_P1NO = {"verdict": "不认可", "gap_type": "档位误判", "gap_detail": "P1 对该标的重大性不足",
         "what_would_change_my_mind": "补量级证据"}
_ENO = {"verdict": "不认可", "gap_type": "证据缺失", "gap_detail": "缺 ETF 成分股权重",
        "what_would_change_my_mind": "补权重"}
_noP1 = {"verdict": "不认可", "gap_type": "传导链过长", "gap_detail": "缺供应商数据",
         "what_would_change_my_mind": "补"}
_TOP2 = ["电子", "银行"]
_stocks, _etfs = _cfg.load_stocks(), _cfg.load_etfs()


def _hook(industry: str):
    """hook 现在收行业参数 —— 只回应被否股票所在行业。"""
    _hook.seen.append(industry)
    return ("A62SYS", _etfs, "ETFEV", "ETFPER", None)


_hook.seen = []


def _run(script):
    _hook.seen = []
    _llm = _FakeLLM(script)
    _out = _pipeline._decide(_FakeCtx(_llm), "agent6", "A6SYS", _stocks,
                             "EV", "PER", _TOP2, etf_hook=_hook)
    return _out, _llm.calls


from src import pipeline as _pipeline  # noqa: E402

# 场景 A：股票被否(涉P1) → 用【同行业】ETF 回应 → 认可
# 新规则：插入【不消耗股票轮次】，所以 ETF 那一审用 r1 的 tag。
_a, _calls = _run([("agent6", "agent6:r1", _A6), ("audit", "audit:r1", _P1NO),
                   ("agent62", "agent62:p1insert1", _A62), ("audit", "audit:r1", _OK)])
assert _a["decision"] == "买入" and _a["code"] == "159997", _a
assert _a["rounds"] == 1, f"插入不消耗股票轮次，rounds 应为 1，实际 {_a['rounds']}"
assert _calls == ["agent6@agent6:r1", "audit@audit:r1",
                  "agent62@agent62:p1insert1", "audit@audit:r1"], _calls
assert len(_a["p1_etf_inserts"]) == 1
assert _hook.seen == ["电子"], f"hook 应只收到被否股票所在行业，实际 {_hook.seen}"

# 场景 B：ETF 也被否 → 回股票层走【正常流程】（r2，不是 r3）
_b, _calls = _run([("agent6", "agent6:r1", _A6), ("audit", "audit:r1", _P1NO),
                   ("agent62", "agent62:p1insert1", _A62), ("audit", "audit:r1", _ENO),
                   ("agent6", "agent6:r2", _A6), ("audit", "audit:r2", _OK)])
assert _b["decision"] == "买入" and _b["code"] == "000636", _b
assert _b["rounds"] == 2, f"插入不消耗轮次，回到股票层应是 r2，实际 rounds={_b['rounds']}"
assert "agent6@agent6:r2" in _calls, f"应回股票层走正常流程 r2：{_calls}"
# 只插一次：即使后面还有 P1 被否，也不再插
assert sum(1 for c in _calls if "agent62" in c) == 1, f"只应插一次：{_calls}"

# 场景 B2：P1 被否 → 插 ETF（被否）→ 股票 r2 又因 P1 被否 → 【不得再插】
# 注意：三轮的 gap_type 必须互不相同，否则会先触发「审计保险」（同类理由→直接空仓）。
_b2, _calls2 = _run([("agent6", "agent6:r1", _A6), ("audit", "audit:r1", _P1NO),
                     ("agent62", "agent62:p1insert1", _A62), ("audit", "audit:r1", _ENO),
                     ("agent6", "agent6:r2", _A6),
                     ("audit", "audit:r2", {"verdict": "不认可", "gap_type": "传导链过长",
                                            "gap_detail": "链路多一步", "what_would_change_my_mind": "补"}),
                     ("agent6", "agent6:r3", _A6), ("audit", "audit:r3", _OK)])
assert _b2["decision"] == "买入", _b2
assert sum(1 for c in _calls2 if "agent62" in c) == 1, \
    f"P1 闸门只插一次，第二次被否不得再插：{_calls2}"
assert _b2["rounds"] == 3, _b2["rounds"]

# 场景 C：与 P1 无关的否决 → 不插入 ETF
_c, _calls = _run([("agent6", "agent6:r1", {**_A6, "tier_basis": ["E1"]}),
                   ("audit", "audit:r1", _noP1),
                   ("agent6", "agent6:r2", {"decision": "空仓", "reason": "补不了"}),
                   ("audit", "audit:r2", _OK)])
assert not any("agent62" in c for c in _calls), f"不应插入 ETF：{_calls}"

# 场景 D：3 轮耗尽 → 空仓
_d, _calls = _run([("agent6", "agent6:r1", {**_A6, "tier_basis": ["E1"]}),
                   ("audit", "audit:r1", _noP1),
                   ("agent6", "agent6:r2", {**_A6, "tier_basis": ["E1"]}),
                   ("audit", "audit:r2", {"verdict": "不认可", "gap_type": "证据缺失",
                                          "gap_detail": "a", "what_would_change_my_mind": "b"}),
                   ("agent6", "agent6:r3", {**_A6, "tier_basis": ["E1"]}),
                   ("audit", "audit:r3", {"verdict": "不认可", "gap_type": "方向相反",
                                          "gap_detail": "c", "what_would_change_my_mind": "d"})])
assert _d["decision"] == "空仓" and _d["rounds"] == 3, _d
# 插入上限必须是 1（用户定案：只插一次）
assert _pipeline._MAX_ETF_INSERTS == 1, f"插入上限应为 1，实际 {_pipeline._MAX_ETF_INSERTS}"

# _gap_touches_p1 判据：只认 P1，不认 P4
assert _pipeline._gap_touches_p1(_P1NO, {}) is True
assert _pipeline._gap_touches_p1(_ENO, {"tier_basis": ["P1"]}) is True
assert _pipeline._gap_touches_p1(_noP1, {"tier_basis": ["E1"]}) is False
assert _pipeline._gap_touches_p1(
    {"gap_type": "量级不足", "gap_detail": "金额太小"}, {"tier_basis": ["P4"]}) is False, \
    "P4 不应触发 ETF 插入（闸门只认 P1）"
# --- A1 定案：机会优先级不得推翻等级序 + 试的顺序 -----------------------------
# 实测反例（07-28）：房地产持 P1、非银金融持 E2，模型援引"机会优先级"选了 E2，
# P1 从未被先试一次。定案 A1：等级序优先，外围只在同等级内决胜。
for _kw in ("但外围不能推翻等级序", "只在【等级真正相同时】才做次级排序",
            "不得】出现\"P1 被 E2 顶掉\"", "试的顺序（硬约束，必须遵守）",
            "不得跳过 1 档"):
    assert _kw in _a6p, f"Agent6/62 缺 A1 规则：{_kw}"
assert "先看档位再挑标的" in P.agent6("- X"), "Agent6 缺试的顺序提示"
assert "先看档位再挑标的" in P.agent62("- X"), "Agent62 缺试的顺序提示"
assert "上一个更高档候选为什么被否" in P.agent6("- X"), "Agent6 缺降级须说明理由的要求"
_tr = configs.load_tier_rules()
assert "trial_order" in _tr and "must_not_skip" in _tr["trial_order"], "配置缺 trial_order"
assert "opportunity_priority" in _tr, "配置缺 opportunity_priority"
assert "P1 被 E2 顶掉" in _tr["opportunity_priority"]["forbidden"]
# 方案文档必须同步（避免下次漂移）
_plan = _read_or_empty("PROJECT_PLAN.md")
for _kw in ("外围也不能推翻等级序（定案 A1）", "「试的顺序」（Agent6 / Agent62 的硬约束）",
            "多个行业都有强档，但等级不同"):
    assert _kw in _plan, f"PROJECT_PLAN 未同步 A1：{_kw}"
# 等级序本身不得被改动（第 3 键）
assert _tr["industry_ranking"]["order"] == \
    "P1 > C1 = E1 > P2 > C2 = E2 > P3 = C3 = E3 > P4 = C4 = E4", \
    "等级序被改动了"
# P1 闸门的审计历史与股票层【共用】（用户定案 2）
import inspect as _ins  # noqa: E402
_dsrc = _ins.getsource(_pipeline._decide)
assert "history)" in _dsrc, "插入 ETF 的审计未传共用 history"
assert "独立" not in _dsrc.split("_audit(ctx, etf_plan")[1][:60], \
    "插入 ETF 的审计历史应共用，不应独立"
import inspect as _inspect  # noqa: E402
assert "etf_hook" not in _inspect.getsource(_pipeline.stage_agent62), \
    "阶段2（stage_agent62）不应带 P1 插入钩子"
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
# Agent4 不得回显行业（行业由上游 f2 唯一确定；回显等于暗示它可以改）
_a4_contract = a4[a4.rfind("只输出 JSON"):]
assert '"industry"' not in _a4_contract, "Agent4 契约不得回显 industry（会被误认为可改写）"
assert '"tier"' in _a4_contract and '"direction"' in _a4_contract

# --- 提示词不得出现回测期真实案例的具体名称（未来数据泄露）------------------
_LEAK_WORDS = ("小米", "卢伟冰", "村田", "风华高科", "瑞萨", "SK海力士", "摩根大通",
               "兆易创新", "普冉股份", "浪潮信息", "广联达", "阳光电源", "天赐材料",
               "金风科技", "京东", "阿里", "华为", "特斯拉", "强生", "英伟达",
               "容百科技", "滨江集团", "红旗连锁", "登海种业", "海大集团", "地铁设计",
               "宁波银行", "广发证券", "MLCC", "电子布", "乙二醇", "多晶硅", "eSIM",
               "RoboBase", "具身智能", "8000亿", "特别国债", "存款保险", "贴现窗口")
_prompt_blob = "\n".join([a4, P.AGENT2, P.agent1(), P.agent3("X"), P.agent42(), P.agent5(),
                          P.agent52("电子", "- X"), P.agent6("- X"), P.agent62("- X"),
                          P.audit(P.gap_enum(), "无"), P.retry("g", "d", "m", "p")])
_leaked = [w for w in _LEAK_WORDS if w in _prompt_blob]
assert not _leaked, f"提示词里出现回测期真实案例名称（应改为抽象举例）：{_leaked}"

# --- 跨 agent 的硬约束必须一致（否则施工方被禁止、审计方却能做）-------------
_audit_txt, _g6, _g62, _retry = (P.audit(P.gap_enum(), "无"), P.agent6("- X"),
                                 P.agent62("- X"), P.retry("g", "d", "m", "p"))
for _name, _txt in (("agent6", _g6), ("agent62", _g62), ("retry", _retry), ("audit", _audit_txt)):
    assert "不能单独否决一条强档" in _txt or "外围不能单独否决强档" in _txt, \
        f"{_name} 缺『外围不能单独否决强档』约束"
# 回退轮次必须复述档位硬事实与零未来信息（否则回退时就丢了保护）
for _kw in ("档位标记是硬事实", "零未来信息约束", "空仓是正常结论"):
    assert _kw in _retry, f"retry 未复述硬约束：{_kw}"
# E 档四步流水线的调整值必须以 _TIER_BRIEF 为准，且与配置一致
_ep = configs.load_tier_rules()["judging_rules"]["E"]["e_tier_pipeline"]
assert _ep["step3_channel"]["a_foreign"]["delta"] == "+2"
assert _ep["step3_channel"]["b_project_landed"]["delta"] == "-2"
assert _ep["step3_channel"]["c_price_up"]["delta"] == "-2"
assert "+2" in a4 and "−2" in a4, "Agent4 未写出渠道调整的加减值"
# (d) 必须限定「只针对池内公司」，否则外国龙头停产会被误降档（实测回归）
_d = _ep["step3_channel"]["d_other_channels"]
assert "池内公司" in _d["scope"], "(d) 未限定只针对池内公司"
assert any("外国主体的公告" in x for x in _d["not_this_clause"]), "(d) 缺『外国公告不适用』的排除"
for _kw in ("外国主体的公告【不是", "行情陈述不等于动作", "气象/自然灾害"):
    assert _kw in a4, f"Agent4 的 (d) 缺排除项：{_kw}"
assert "外国龙头的公告不是" in P.agent42(), "Agent42 未同步 (d) 的误用提示"
# 已定价清单只在一处定义（不得重复枚举）
assert a4.count("发行或上市获批") + a4.count("发行获批") <= 2, "已定价清单被重复枚举"
assert "后果已经发生" in a4 and "后果发生了没有" in a4, "已定价的唯一判据未写明"

# --- 判级规则两份必须同步：_TIER_BRIEF（模型看到的）与 tier_rules.json（代码用的）
_rules = configs.load_tier_rules()
assert "e_tier_pipeline" in _rules["judging_rules"]["E"], "配置缺 E 档四步流水线"
assert "judging_rules" in _rules and "industry_ranking" in _rules, "配置结构不完整"
# P1 约束：金额明确不等于约束已生效，"蓄势待发/将投放"不得判 P1
assert "计划中" in a4 and "蓄势待发" in a4, "Agent4 缺『计划中不算 P1』的约束"
_p1 = configs.load_tier_rules()["judging_rules"]["P"]["P1"]
assert any("计划中" in x for x in _p1["exclusions"]), "P1 exclusions 未列出『计划中』"
# （Agent3 的『禁止硬塞』约束已被撤回：用户改为在下游用 Agent52 + 第4关解决）
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
