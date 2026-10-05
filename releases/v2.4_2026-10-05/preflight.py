"""数据源体检：把四个外部依赖逐个打通一遍，并逐标的验证行情可取。

    python preflight.py [日期]

逐标的检查会暴露"整池里某几只在行情接口上取不到"这类只在回测时才炸的问题。
"""

import datetime as dt
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from src import configs, market
from src.collect import news as news_mod
from src.collect import quotes as quotes_mod
from src.http import Client
from src.llm.client import LLM

DATE = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
c = Client()
ok, bad = [], []


def check(name, fn):
    t0 = time.time()
    try:
        detail = fn()
        ok.append(name)
        print(f"  [OK]   {name:<28} {time.time() - t0:5.1f}s  {detail}")
    except Exception as e:  # noqa: BLE001 - 体检脚本要报告全部失败项
        bad.append((name, f"{type(e).__name__}: {e}"))
        print(f"  [FAIL] {name:<28} {time.time() - t0:5.1f}s  {type(e).__name__}: {str(e)[:120]}")


print(f"=== 数据源体检  target={DATE}  now={dt.datetime.now():%Y-%m-%d %H:%M} ===\n")

print("【1】东财 7x24 快讯")
check("近端采集（今天 09:00 锚点）", lambda: f"{len(news_mod.collect_news(c, dt.date.today().isoformat(), max_pages=2))} 条")
check(f"历史跳采 {DATE}", lambda: f"{len(news_mod.collect_news(c, DATE, max_pages=8))} 条")
check("更早历史 2026-06-10", lambda: f"{len(news_mod.collect_news(c, '2026-06-10', max_pages=8))} 条")


def _window_check():
    """窗口越界检查：绝不能出现晚于 T 09:00 的条目（未来数据泄露）。"""
    items = news_mod.collect_news(c, DATE, max_pages=8)
    lo = min(x["showTime"] for x in items)
    hi = max(x["showTime"] for x in items)
    late = [x for x in items if x["showTime"] > f"{DATE} 09:00:00"]
    assert not late, f"{len(late)} 条晚于 {DATE} 09:00:00，首条 {late[0]['showTime']}"
    assert lo >= f"{dt.date.fromisoformat(DATE) - dt.timedelta(days=1)} 18:00:00", lo
    # 条数合理性：正常交易日 A+B 窗合计大致 250~600 条；异常少说明源返回不全
    assert len(items) >= 100, f"仅 {len(items)} 条，疑似源返回不全（断点续采？）"
    days = {}
    for x in items:
        days[x["showTime"][:10]] = days.get(x["showTime"][:10], 0) + 1
    return f"{len(items)} 条，{lo} .. {hi}，按日 {days}"


check(f"窗口越界（{DATE}）", _window_check)


def _repro_check():
    """同一日期连采两次必须完全一致（快讯源是唯一源，可复现性只能靠它保证）。"""
    a = {x["code"] for x in news_mod.collect_news(c, DATE, max_pages=8)}
    b = {x["code"] for x in news_mod.collect_news(c, DATE, max_pages=8)}
    assert a == b, f"两次采集不一致：仅第一次有 {len(a - b)} 条，仅第二次有 {len(b - a)} 条"
    return f"两次一致，{len(a)} 条"


check(f"采集可复现（{DATE}）", _repro_check)

print("\n【2】新浪外围夜盘（10 指标）")
r = check("批量取 10 指标", lambda: quotes_mod.collect_quotes(c))
qs = quotes_mod.collect_quotes(c)
missing = [q["name"] for q in qs if q.get("missing")]
print(f"         有数据 {len(qs) - len(missing)}/10" + (f"，缺 {missing}" if missing else ""))
for q in qs:
    if not q.get("missing") and q.get("price") is None:
        print(f"         ⚠ {q['name']} 价格为 None（字段下标可能变了）")

print("\n【3】日线行情（全池逐标的，腾讯→新浪 双源）")
stocks, etfs = configs.load_stocks(), configs.load_etfs()
beg, end = "2026-09-01", "2026-09-30"


def probe_pool(pool, label):
    fail = []
    for it in pool.by_code.values():
        try:
            bars = market.daily(c, it.code, beg, end, kind="stock")
            if not bars:
                fail.append(f"{it.code}{it.name}(空)")
            elif not any(b["date"] == DATE for b in bars) and DATE in _trading_days():
                fail.append(f"{it.code}{it.name}(缺{DATE})")
        except Exception as e:  # noqa: BLE001
            fail.append(f"{it.code}{it.name}({type(e).__name__})")
    print(f"         {label}: {pool.count - len(fail)}/{pool.count} 可取"
          + (f"，失败 {fail[:6]}" if fail else ""))
    return fail


_days_cache = {}


def _trading_days():
    if "d" not in _days_cache:
        _days_cache["d"] = set(market.trading_days(c, beg, end))
    return _days_cache["d"]


check("上证指数日历", lambda: f"{len(market.trading_days(c, beg, end))} 个交易日")


def _source_check():
    """两个源分别单独验一遍——只测串联链会漏掉"备用源其实也挂了"。"""
    import src.market as m
    out = []
    for name, fn in m.SOURCES:
        try:
            rows = fn(c, "sz002594", beg, end)
            out.append(f"{name}={len(rows)}")
        except Exception as e:  # noqa: BLE001
            out.append(f"{name}=FAIL({type(e).__name__})")
    return "  ".join(out)


check("双源分别可用性", _source_check)

f1 = probe_pool(stocks, "个股 59")
f2 = probe_pool(etfs, "ETF 48")
if f1 or f2:
    bad.append(("日线逐标的", f"个股失败 {len(f1)}，ETF 失败 {len(f2)}"))

print("\n【4】DeepSeek")
check("models 列表", lambda: f"{len(__import__('requests').get('https://api.deepseek.com/models', headers={'Authorization': 'Bearer ' + configs.load_env()['DEEPSEEK_API_KEY']}, timeout=30).json()['data'])} 个模型")


def _one_call():
    r = LLM(use_cache=False).call("agent1", "只输出 JSON。", '回复 {"ok":true}', tag="preflight")
    if r.error:
        raise RuntimeError(r.error)
    return f"thinking=disabled 正常（{r.completion_tokens} tok）"


check("flash 调用", _one_call)


def _pro_call():
    r = LLM(use_cache=False).call("audit", "只输出 JSON。", '回复 {"ok":true}', tag="preflight")
    if r.error:
        raise RuntimeError(r.error)
    return f"pro+max 正常（推理 {len(r.reasoning)} 字）"


check("pro+reasoning 调用", _pro_call)

print("\n" + "=" * 56)
print(f"通过 {len(ok)} 项" + (f"，失败 {len(bad)} 项" if bad else "，全部通过"))
for n, e in bad:
    print(f"  FAIL {n}: {e}")
