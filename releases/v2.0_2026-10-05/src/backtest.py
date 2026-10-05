"""回测：用历史日期调用同一个 predict（原则二：回测不得有独立的代码路径）。

评估口径：T 日开盘买、T+1 日开盘卖 → open-to-open 收益。
"""

from __future__ import annotations

import datetime as dt

from . import market
from . import pipeline
from .configs import read_local, write_json, run_dir
from .http import Client
from .pipeline import Decision, predict

BENCH = "000001"          # 上证指数，作为同期基准
BENCH_KIND = "index"


def _dates(beg: str, end: str, client: Client) -> list[str]:
    """区间内的交易日（按上证指数日历）。"""
    return [d for d in market.trading_days(client, beg, end)]


def run(beg: str, end: str, *, refresh: bool = False, use_cache: bool = True,
        reuse: bool = False, verbose: bool = True) -> dict:
    client = Client()
    days = _dates(beg, end, client)
    if not days:
        raise SystemExit(f"{beg} ~ {end} 区间内没有交易日")

    # 基准要多取一段：最后一天的出场是"它的下一个交易日"
    last = (dt.date.fromisoformat(days[-1]) + dt.timedelta(days=40)).isoformat()
    bench = market.opens(client, BENCH, days[0], last, BENCH_KIND)

    rows = []
    for d in days:
        if verbose:
            print(f"\n=== {d} ===", flush=True)
        if reuse:
            dec = _from_disk(d)
            if verbose:
                print(f"  [reuse] 复用 runs/{d}/analysis/decision.json", flush=True)
        else:
            dec = predict(d, refresh=refresh, use_cache=use_cache, verbose=verbose)
        rows.append(_evaluate(client, dec, bench, d))
        if verbose:
            r = rows[-1]
            print(f"  → {dec.action} {dec.kind} {dec.code} {dec.name} "
                  f"收益={r.get('return_pct')} 基准={r.get('bench_pct')}", flush=True)

    stats = summarize(rows)
    out = {"range": [beg, end], "days": len(days), "rows": rows, "stats": stats}
    write_json(run_dir(f"backtest_{beg}_{end}") / "result.json", out)
    if verbose:
        print("\n" + "=" * 58)
        print(render(stats, rows))
    return out


def _from_disk(date: str) -> Decision:
    d = read_local(date, "analysis/decision.json")
    return Decision(**{k: v for k, v in d.items()
                       if k in Decision.__dataclass_fields__})


def _evaluate(client: Client, dec: Decision, bench: dict[str, float], date: str) -> dict:
    row = dec.to_dict()
    if dec.action == "买入" and dec.code:
        ev = market.evaluate(client, dec.code, date, kind="stock")
        row.update({k: v for k, v in ev.items() if k != "error"})
        if ev.get("error"):
            row["eval_error"] = ev["error"]
    row["warnings"] = len(pipeline.errors)
    nxt = next((x for x in sorted(bench) if x > date), None)
    if nxt and date in bench:
        row["bench_pct"] = round((bench[nxt] / bench[date] - 1) * 100, 3)
        if row["action"] == "买入":
            row.setdefault("exit_date", nxt)
    return row


def summarize(rows: list[dict]) -> dict:
    trades = [r for r in rows if r["action"] == "买入" and r.get("return_pct") is not None]
    rets = [r["return_pct"] for r in trades]
    wins = [x for x in rets if x > 0]
    curve, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        curve *= 1 + r / 100
        peak = max(peak, curve)
        mdd = min(mdd, curve / peak - 1)
    bench = [r["bench_pct"] for r in rows if r.get("bench_pct") is not None]
    return {
        "交易日数": len(rows),
        "开仓次数": len(rets),
        "空仓次数": len(rows) - len(rets),
        "胜率%": round(len(wins) / len(rets) * 100, 2) if rets else None,
        "平均收益%": round(sum(rets) / len(rets), 3) if rets else None,
        "累计收益%": round((curve - 1) * 100, 3) if rets else 0.0,
        "最大单笔回撤%": round(min(rets), 3) if rets else None,
        "最大净值回撤%": round(mdd * 100, 3) if rets else None,
        "基准累计%": round(sum(bench), 3) if bench else None,
        "平均审计轮次": round(sum(r.get("audit_rounds", 0) for r in rows) / len(rows), 2),
    }


def render(stats: dict, rows: list[dict]) -> str:
    def num(v, width=8):
        return f"{v:>{width}.3f}" if isinstance(v, (int, float)) else f"{'—':>{width}}"

    lines = [f"{'日期':<11}{'决策':<6}{'标的':<18}{'收益%':>8}{'基准%':>8}  行业"]
    for r in rows:
        name = f"{r['code']} {r['name']}" if r["action"] == "买入" else "—"
        lines.append(f"{r['date']:<11}{r['action']:<6}{name:<18}"
                     f"{num(r.get('return_pct'))}{num(r.get('bench_pct'))}  {r.get('industry', '')}")
    lines.append("-" * 58)
    for k, v in stats.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)
