# v2.7（2026-10-05）

补历史外盘数据源（方案A：日线口径，与实时源 prev_close 语义对齐）。新增 src/collect/overseas.py：新浪美股 getDailyK（2004起）+ 新浪全球期货 getGlobalFuturesDailyKLine（2016起）+ 腾讯 hkHSI，10/10 指标可重建。pipeline 按日期自动路由：回测(<今天)走历史源、生产(今天)走实时快照，meta 记录 quotes 来源(network:hist/live)与口径。输出结构与实时源同构，下游无需改。可靠性分级：美股三大指数与恒指为现金指数(high)，6项期货为连续合约(roll_risk，换月价差风险)。审计硬判据：gap_detail 必须点名缺哪项数据、禁用弹性措辞、8种 gap_type 各加附加要求。决策层(agent6/62/审计)固定 temperature=0。release.py 补入 report.py/showprompts.py（之前漏了导致留档不可复现）。
