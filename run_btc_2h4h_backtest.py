#!/usr/bin/env python3
"""
BTC/USDT 2h和4h全策略回测，生成compare JSON和report MD
"""
import sys, os, json, sqlite3
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ATRSTOP_EMA_PERIOD, ATRSTOP_ATR_PERIOD, ATRSTOP_ATR_MULTIPLIER
from backtest import BacktestEngine
from strategies import (
    SMAcrossStrategy, RSIStrategy, MACDStrategy,
    DonchianChannelStrategy, BollingerBandsStrategy,
    ATRStopStrategy, KDJStrategy, StrategyConfig,
)
from history_cache import get_ohlcv as cache_get_ohlcv, init_cache_db

init_cache_db()

RESULTS_DIR = "backtest_results"
os.makedirs(RESULTS_DIR, exist_ok=True)

SYMBOL = "BTC/USDT"
TIMEFRAMES = ["2h", "4h"]
DIRECTION = "both"

STRATEGIES_CONFIG = [
    ("DONCHIAN", DonchianChannelStrategy, {"channel_period": 20, "trend_ema_period": 50}),
    ("KDJ", KDJStrategy, {"k_period": 9, "d_period": 3, "j_period": 3}),
    ("MACD", MACDStrategy, {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
    ("BOLLINGER", BollingerBandsStrategy, {"period": 20, "std_dev": 2.0}),
    ("RSI", RSIStrategy, {"rsi_period": 14, "oversold": 30.0, "overbought": 70.0}),
    ("ATRSTOP", ATRStopStrategy, {"ema_period": ATRSTOP_EMA_PERIOD, "atr_period": ATRSTOP_ATR_PERIOD, "atr_multiplier": ATRSTOP_ATR_MULTIPLIER}),
    ("SMA", SMAcrossStrategy, {"fast_period": 10, "slow_period": 30}),
]

def run_all_strategies(symbol, timeframe, direction="both",
                       stop_loss=0.05, take_profit=0.10,
                       capital_pct=1.0, initial_capital=10000.0,
                       commission_pct=0.001, slippage_pct=0.0005):
    """对所有策略运行回测"""
    print(f"\n{'='*70}")
    print(f"  {symbol} {timeframe}  direction={direction}")
    print(f"{'='*70}")
    
    # Load data
    candles = cache_get_ohlcv(symbol, timeframe, limit=20000)
    if not candles or len(candles) < 50:
        print(f"  ERROR: 数据不足 ({len(candles) if candles else 0} 条)")
        return []
    
    print(f"  数据: {len(candles)} 条 "
          f"{datetime.fromtimestamp(candles[0]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d')} → "
          f"{datetime.fromtimestamp(candles[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    
    results = []
    
    for name, strat_cls, strat_kw in STRATEGIES_CONFIG:
        config = StrategyConfig(
            symbol=symbol, timeframe=timeframe,
            capital_pct=capital_pct, stop_loss=stop_loss, take_profit=take_profit,
            commission_pct=commission_pct, slippage_pct=slippage_pct,
            trade_direction=direction,
        )
        
        try:
            strat = strat_cls(config=config, **strat_kw)
            engine = BacktestEngine(
                strategy=strat,
                initial_capital=initial_capital,
                trade_direction=direction,
            )
            engine.candles = candles
            engine.load_data()
            result = engine.run()
            
            results.append({
                "strategy": name,
                "return": round(result.total_return_pct, 2),
                "sharpe": round(result.sharpe_ratio, 2),
                "drawdown": round(result.max_drawdown_pct, 2),
                "win_rate": round(result.win_rate_pct, 1),
                "trades": result.total_trades,
                "total_return_pct": result.total_return_pct,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "win_rate_pct": result.win_rate_pct,
            })
            
            print(f"  {name:<12} {result.total_return_pct:>+8.2f}%  SR={result.sharpe_ratio:.2f}  "
                  f"DD={result.max_drawdown_pct:>6.2f}%  WR={result.win_rate_pct:>5.1f}%  Trades={result.total_trades}")
            
        except Exception as e:
            print(f"  {name:<12} ERROR: {e}")
    
    return results

def generate_compare_json(results, symbol, timeframe, direction):
    """生成compare JSON"""
    # Sort by return descending
    ranked = sorted(results, key=lambda x: x["return"], reverse=True)
    rankings = []
    for i, r in enumerate(ranked, 1):
        rankings.append({
            "rank": i,
            "strategy": r["strategy"],
            "return": r["return"],
            "sharpe": r["sharpe"],
            "drawdown": r["drawdown"],
            "win_rate": r["win_rate"],
            "trades": r["trades"],
        })
    
    compare_data = {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "rankings": rankings,
    }
    
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{RESULTS_DIR}/compare_BTC_USDT_{timeframe}_{ts}.json"
    with open(path, "w") as f:
        json.dump(compare_data, f, ensure_ascii=False, indent=2)
    print(f"\n  compare JSON: {path}")
    return path, ts

def generate_report_md(results, symbol, timeframe, direction, ts_str):
    """生成report Markdown"""
    ranked = sorted(results, key=lambda x: x["return"], reverse=True)
    
    md_path = f"{RESULTS_DIR}/report_BTC_USDT_{timeframe}_{ts_str}.md"
    with open(md_path, "w") as f:
        f.write(f"# BTC/USDT 回测报告\n\n")
        f.write(f"**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write(f"**交易对**: {symbol}\n")
        f.write(f"**时间周期**: {timeframe}\n")
        f.write(f"**交易方向**: {direction}\n")
        f.write(f"**策略数量**: {len(results)} 个\n")
        f.write(f"**止损/止盈**: 5% / 10% (默认值)\n\n")
        f.write("---\n\n")
        
        f.write("## 策略排名\n\n")
        f.write("| 排名 | 策略 | 总收益% | 夏普比率 | 最大回撤% | 交易次数 | 胜率% |\n")
        f.write("|------|------|--------|---------|---------|---------|-------|\n")
        for r in ranked:
            f.write(f"| {r['strategy']} | {r['return']:+.2f} | {r['sharpe']:.2f} | {r['drawdown']:.2f} | {r['trades']} | {r['win_rate']:.1f} |\n")
        
        f.write("\n## 最佳策略\n\n")
        if ranked:
            best = ranked[0]
            f.write(f"**{best['strategy']}** 收益 {best['return']:+.2f}%  "
                    f"夏普 {best['sharpe']:.2f}  回撤 {best['drawdown']:.2f}%\n")
    
    print(f"  report MD: {md_path}")
    return md_path

def main():
    all_files = []
    
    for tf in TIMEFRAMES:
        print(f"\n{'#'*70}")
        print(f"#  BTC/USDT {tf} 回测")
        print(f"{'#'*70}")
        
        results = run_all_strategies(
            symbol=SYMBOL,
            timeframe=tf,
            direction=DIRECTION,
        )
        
        if not results:
            print(f"  WARNING: {tf} 无结果，跳过")
            continue
        
        compare_path, ts_str = generate_compare_json(results, SYMBOL, tf, DIRECTION)
        report_path = generate_report_md(results, SYMBOL, tf, DIRECTION, ts_str)
        all_files.extend([compare_path, report_path])
    
    print(f"\n\n{'='*70}")
    print("  生成文件列表")
    print(f"{'='*70}")
    for fp in all_files:
        print(f"  {fp}")

if __name__ == "__main__":
    main()