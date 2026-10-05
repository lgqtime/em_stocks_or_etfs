# em_stocks_or_etfs —— 多 Agent 情绪预测链路 V2

依据 [`PROJECT_PLAN.md`](PROJECT_PLAN.md) 落地。**两个功能：预测、回测**（回测复用同一条
预测代码路径，没有第二套实现）。

```
T 日 09:00 运行，用「快讯 + 外围夜盘」预测该买哪只标的
持有 1 个交易日：T 日开盘买、T+1 日开盘卖。允许空仓，空仓是正常结论。
```

## 用法

```bash
python -m src predict                    # 最新交易日（生产运行）
python -m src predict 2026-09-10         # 指定日（回测运行，同一代码路径）
python -m src backtest 2026-09-08 2026-09-10
python -m src evaluate 2026-09-10        # 用已落盘的决策复盘单日收益

python -m src.release v2.0 "说明"        # 原则三：版本留档 + SHA256 清单
python repeat.py 2026-09-10 3            # 同一输入反复跑，量出模型随机的摆幅
python selftest.py                       # 三关硬过滤 / 第3关排序 / 提示词渲染 / 行情口径自检
```

依赖：`requests`、`openai`（当前环境已装）。密钥放 `.env` 的 `DEEPSEEK_API_KEY`。

## 链路

```
采集(东财7x24 A窗+B窗 / 新浪外围10指标)
  → 第0关 原料清洗 ── Agent1 分类 ── 第1关 类目剔除
  → Agent2 摘要 ── Agent3 唯一行业归属 ── 第2关 行业清洗
  → Agent4 判级 ── Agent42 档位审查 ── 第3关 推举5行业（纯代码）
  → Agent5 证据精选 ── Agent6 选股 ── 审计（不认可则回退≤3轮）
  → 股票层最终空仓时才启用 Agent62(ETF) ── 审计（≤3轮）
```

三关硬过滤、第3关排序、标的合法性校验全部是**纯代码**（`src/filter/hard.py`）。

## 目录

```
config/       stocks.json / etfs.json / audit_blacklist.json / tier_rules.json
src/          configs.py 基础文件加载 · http.py 自适应限速 · market.py 日线与日历
  collect/    news.py 东财快讯 · quotes.py 新浪夜盘
  filter/     hard.py 三关硬过滤 + 第3关排序
  llm/        client.py 模型分档与缓存 · prompts.py 各 Agent 提示词
  pipeline.py predict() 唯一入口
  backtest.py 回测 · cli.py 命令行 · release.py 版本留档
runs/<日期>/  raw/ filtered/ analysis/ meta.json     ← 原则一
.cache/llm/   内容寻址的 LLM 缓存（批量提取类 agent1–5 才缓存；决策类
              agent6/62 与审计不缓存，避免把"第一次碰巧算出的结论"冻死）
releases/     版本留档                                      ← 原则三
```

`meta.json` 记录每个模块的输入来源（`memory` / `local` / `network`）、各阶段计数、
token 用量，以及本轮所有降级与失败（`errors`）——**不允许静默降级**。

## 已知限制（实测）

| 项 | 说明 |
|---|---|
| **外盘指数无法对齐历史** | 新浪 `hq.sinajs.cn` 只有实时快照，没有夜盘历史接口。回测时外盘指数是"运行时点"的值，不是目标日当夜的。行业消息与外围消息都按目标日严格过滤，只有这一项对不上，已在 `meta.json.inputs.quotes_provenance` 标注。 |
| **东财 push2his 取不到行情** | 该域名族在当前网络下 ProxyError，故日线改用腾讯 `web.ifzq.gtimg.cn`（个股/ETF/指数均返回前复权日线）。 |
| **历史补采有 40,000 条上限陷阱** | 必须用 `sortEnd` 微秒锚点分段抓，不能靠加大 `maxPages`。 |
| **单次全链路耗时** | 09-10 实测冷跑约 40 分钟、45 次调用、47 万 token（agent4/agent42 占大头）；并行化后热跑约 7 分钟。仍可能超方案 §11 的 09:25 截止，生产前需继续压缩。 |
| **决策层结果会摆动** | 同一份 09-10 输入跑 4 次：2 次买 002156 通富微电（−1.11%）、1 次买 159667 工业母机ETF（−2.60%）、1 次空仓。批量提取层（agent1–5）已确定性缓存，摆动全部来自 agent6/62 + 审计。**不要用单次结果比较版本**（方案 §10.1 第 5 条），用 `repeat.py` 取均值。 |
