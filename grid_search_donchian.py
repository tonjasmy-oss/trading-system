"""
Donchian Channel 策略 Grid Search 参数寻优
用法: 
  python3 grid_search_donchian.py --symbols SOL SUI --timeframe 2h
  python3 grid_search_donchian.py --symbols BTC ETH --timeframe 4h
"""
import sys, os, itertools, json, time, logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies import DonchianChannelStrategy, StrategyConfig
from backtest import BacktestEngine, MAX_CACHE_LIMIT
from history_cache import init_cache_db

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

# ── 按周期分档的参数空间 ────────────────────────────
PARAM_GRIDS = {
    "2h": {
        "channel": [10, 20, 30, 55],
        "ema":     [10, 20, 50],
        "sl":      [0.015, 0.02, 0.03],
        "tp":      [0.03, 0.05, 0.07],
    },
    "4h": {
        "channel": [20, 55, 100],
        "ema":     [50, 100, 200],
        "sl":      [0.02, 0.03, 0.04],
        "tp":      [0.04, 0.06, 0.08],
    },
}

DIRECTION = "both"
CAPITAL = 10000.0

def run_one(symbol, timeframe, channel, ema, sl, tp):
    config = StrategyConfig(
        symbol=symbol, timeframe=timeframe,
        stop_loss=sl, take_profit=tp,
        capital_pct=1.0, commission_pct=0.001, slippage_pct=0.0005,
        trade_direction=DIRECTION,
    )
    strategy = DonchianChannelStrategy(
        config=config,
        channel_period=channel,
        trend_ema_period=ema,
    )
    engine = BacktestEngine(strategy, initial_capital=CAPITAL, trade_direction=DIRECTION)
    if not engine.load_data():
        return None
    engine.compute_signals()
    return engine.run()

def score(r):
    """综合评分：夏普*0.3 + 收益率*0.3 + 胜率*0.15 - 回撤*0.25"""
    return r.sharpe_ratio * 0.3 + r.total_return_pct * 0.3 + r.win_rate_pct * 0.15 - r.max_drawdown_pct * 0.25

def main():
    symbols = ["SOL/USDT", "SUI/USDT"]
    timeframe = "2h"

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--symbols":
            symbols = []
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                s = args[i].upper()
                symbols.append(s if "/" in s else f"{s}/USDT")
                i += 1
        elif args[i] == "--timeframe":
            timeframe = args[i + 1]
            i += 2
        else:
            i += 1

    grid = PARAM_GRIDS.get(timeframe, PARAM_GRIDS["4h"])
    GRID_CH = grid["channel"]
    GRID_EMA = grid["ema"]
    GRID_SL = grid["sl"]
    GRID_TP = grid["tp"]

    init_cache_db()

    total = len(symbols) * len(GRID_CH) * len(GRID_EMA) * len(GRID_SL) * len(GRID_TP)
    print(f"Donchian Grid Search · {timeframe} · {len(symbols)} 标的 · 最多 {total} 组合")
    print(f"通道: {GRID_CH}  EMA: {GRID_EMA}  SL: {GRID_SL}  TP: {GRID_TP}")
    print("=" * 95)

    all_results = []
    count = 0
    t0 = time.time()

    for symbol in symbols:
        for ch, ema, sl, tp in itertools.product(GRID_CH, GRID_EMA, GRID_SL, GRID_TP):
            if tp <= sl:
                continue
            count += 1
            try:
                r = run_one(symbol, timeframe, ch, ema, sl, tp)
                if r is None or r.total_trades == 0:
                    continue
                s = score(r)
                all_results.append({
                    "symbol": symbol, "timeframe": timeframe,
                    "channel": ch, "ema": ema,
                    "sl": sl, "tp": tp,
                    "return": round(r.total_return_pct, 2),
                    "sharpe": round(r.sharpe_ratio, 2),
                    "drawdown": round(r.max_drawdown_pct, 2),
                    "win_rate": round(r.win_rate_pct, 1),
                    "trades": r.total_trades,
                    "score": round(s, 2),
                })
            except Exception as e:
                pass

            if count % 30 == 0:
                elapsed = time.time() - t0
                print(f"  进度: {count}/{total}  ({elapsed:.0f}s)")

    all_results.sort(key=lambda x: x["score"], reverse=True)
    elapsed = time.time() - t0
    print(f"\n完成 {len(all_results)} 组有效结果 耗时 {elapsed:.0f}s\n")

    # ── Top 30 ──────────────────────────────────────
    print(f"{'排名':<4} {'标的':<10} {'通道':>4} {'EMA':>4} {'SL':>6} {'TP':>6} {'收益率':>9} {'夏普':>6} {'回撤':>8} {'胜率':>6} {'交易':>5} {'评分':>6}")
    print("-" * 95)
    for i, r in enumerate(all_results[:30], 1):
        print(f"{i:<4} {r['symbol']:<10} {r['channel']:>4} {r['ema']:>4} "
              f"{r['sl']:>5.1%} {r['tp']:>5.1%} "
              f"{r['return']:>+8.2f}% {r['sharpe']:>6.2f} "
              f"{r['drawdown']:>7.2f}% {r['win_rate']:>5.1f}% {r['trades']:>5d} {r['score']:>6.2f}")

    # ── 按标的分组 Top 5 ───────────────────────────
    for symbol in symbols:
        sym_results = [r for r in all_results if r["symbol"] == symbol]
        if not sym_results:
            continue
        print(f"\n── {symbol} Top 5 ──")
        for i, r in enumerate(sym_results[:5], 1):
            print(f"  {i}. ch={r['channel']} ema={r['ema']} sl={r['sl']:.1%} tp={r['tp']:.1%}  "
                  f"return={r['return']:+.1f}% sharpe={r['sharpe']} dd={r['drawdown']:.1f}% "
                  f"wr={r['win_rate']:.0f}% trades={r['trades']} score={r['score']}")

    # ── 保存 ────────────────────────────────────────
    out_path = f"backtest_results/donchian_grid_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs("backtest_results", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n📄 完整结果: {out_path}")

if __name__ == "__main__":
    main()
