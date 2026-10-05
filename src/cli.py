"""命令行入口。

  python -m src predict                  # 最新交易日（生产运行）
  python -m src predict 2026-09-10       # 指定日（回测运行，同一条代码路径）
  python -m src backtest 2026-09-01 2026-09-10
  python -m src evaluate 2026-09-10      # 复用 runs/<date>/analysis/decision.json 复盘
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from . import market
from .configs import ROOT, run_dir


def cmd_predict(args) -> int:
    from .pipeline import predict
    date = args.date or dt.date.today().isoformat()
    print(f"[predict] date={date} refresh={args.refresh} cache={not args.no_cache}")
    dec = predict(date, refresh=args.refresh, use_cache=not args.no_cache, verbose=True)
    print(json.dumps(dec.to_dict(), ensure_ascii=False, indent=2))
    if dec.action == "买入":
        ev = market.evaluate(_client(), dec.code, date, kind="stock")
        print("\n[复盘口径] T 日开盘买 / T+1 日开盘卖：", json.dumps(ev, ensure_ascii=False))
    print(f"\n产物目录：{run_dir(date)}")
    return 0


def cmd_backtest(args) -> int:
    from .backtest import run
    out = run(args.beg, args.end, refresh=args.refresh, use_cache=not args.no_cache,
              reuse=args.reuse)
    print(f"\n结果落盘：{run_dir(f'backtest_{args.beg}_{args.end}') / 'result.json'}")
    return 0


def cmd_evaluate(args) -> int:
    from . import market
    from .configs import read_local
    dec = read_local(args.date, "analysis/decision.json")
    print(json.dumps(dec, ensure_ascii=False, indent=2))
    if dec.get("action") == "买入" and dec.get("code"):
        ev = market.evaluate(_client(), dec["code"], args.date, kind="stock")
        print("\n[复盘口径] T 日开盘买 / T+1 日开盘卖：", json.dumps(ev, ensure_ascii=False))
    return 0


def _client():
    from .http import Client
    return Client()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="src", description="多 Agent 情绪预测链路 V2")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("predict", help="运行预测（date 省略 = 今天/最新交易日）")
    p.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD")
    p.add_argument("--refresh", action="store_true", help="忽略本地 raw 缓存，重新采集")
    p.add_argument("--no-cache", action="store_true", help="忽略 LLM 缓存")
    p.set_defaults(fn=cmd_predict)

    b = sub.add_parser("backtest", help="区间回测")
    b.add_argument("beg")
    b.add_argument("end")
    b.add_argument("--refresh", action="store_true")
    b.add_argument("--no-cache", action="store_true")
    b.add_argument("--reuse", action="store_true",
                   help="复用 runs/<日期>/analysis/decision.json，不重跑预测（只重算收益）")
    b.set_defaults(fn=cmd_backtest)

    e = sub.add_parser("evaluate", help="用已落盘的决策复盘单日收益")
    e.add_argument("date")
    e.set_defaults(fn=cmd_evaluate)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
