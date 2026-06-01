"""
batch_experiment.py — 批量实验运行脚本
对 5 个标的 × 7+ 策略运行实验管线，补齐回测数据
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from components.experiment_pipeline import ExperimentPipeline

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT", "XAUT/USDT"]
TIMEFRAMES = ["4h", "4h", "2h", "2h", "2h"]

ALL_STRATEGIES = [
    "RSI", "DONCHIAN", "BOLLINGER", "MACD", "KDJ",
    "ATRSTOP", "SMA", "MULTIFACTOR", "FUNDING_ARB", "STAT_ARB",
]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "backtest_results")

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pipeline = ExperimentPipeline()
    all_results = {}

    t0 = time.time()
    for sym, tf in zip(SYMBOLS, TIMEFRAMES):
        print(f"\n{'='*60}")
        print(f"  {sym} @ {tf}")
        print(f"{'='*60}")

        # 先检测 regime，筛选适合的策略
        regime = pipeline._detect_regime(sym, tf)
        regime_recs = [r["strategy"] for r in regime.get("recommended_strategies", [])]

        # regime 推荐的策略 + 其他有对比数据的策略，去重
        strategies = list(dict.fromkeys(regime_recs + [s for s in ALL_STRATEGIES if s not in regime_recs]))

        print(f"  Regime: {regime['trend']}/{regime['volatility']}")
        print(f"  Strategies: {len(strategies)} total")

        try:
            result = pipeline.run(
                symbol=sym, timeframe=tf,
                strategies=strategies[:7],  # 限制7个避免太久
                max_workers=4,
            )

            # 保存结果
            ts = time.strftime("%Y%m%d_%H%M%S")
            out_file = os.path.join(OUTPUT_DIR, f"experiment_{sym.replace('/','_')}_{tf}_{ts}.json")
            with open(out_file, "w") as f:
                json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)

            all_results[sym] = {
                "regime": result.regime,
                "best": result.best,
                "candidates": result.candidates_tested,
                "duration_s": round(result.duration_seconds, 1),
                "file": out_file,
            }

            if result.best:
                print(f"  Best: {result.best['strategy']} score={result.best['score']:.1f} "
                      f"ret={result.best['summary']['total_return_pct']:.1f}% "
                      f"dd={result.best['summary']['max_drawdown_pct']:.1f}%")
            print(f"  {result.candidates_tested} candidates in {result.duration_seconds:.1f}s")
        except Exception as e:
            print(f"  ERROR: {e}")
            all_results[sym] = {"error": str(e)}

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  Batch complete: {len(SYMBOLS)} symbols in {elapsed:.1f}s")
    print(f"{'='*60}")

    # 汇总
    print("\n--- Summary ---")
    for sym, r in all_results.items():
        if r.get("best"):
            b = r["best"]
            print(f"  {sym:>12} → {b['strategy']:>12} score={b['score']:.0f}  ret={b['summary']['total_return_pct']:+.1f}%  dd={b['summary']['max_drawdown_pct']:.1f}%")
        else:
            print(f"  {sym:>12} → {r.get('error','?')}")

    # 保存汇总
    summary_file = os.path.join(OUTPUT_DIR, f"batch_summary_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(summary_file, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\nSummary saved: {summary_file}")

if __name__ == "__main__":
    main()
