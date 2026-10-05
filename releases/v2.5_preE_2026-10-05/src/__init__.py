"""多 Agent 情绪预测链路 V2（PROJECT_PLAN.md）。

对外只有两个功能：
- `predict(date)`  预测（src.pipeline.predict）—— 生产与回测共用这一条代码路径
- `backtest.run`   回测（src.backtest.run）
"""

from .pipeline import Decision, predict

__all__ = ["predict", "Decision"]
