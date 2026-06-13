#!/usr/bin/env python3
"""批量回测BTC/USDT 2h和4h所有策略"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from datetime import datetime, timezone
from history_cache import init_cache_db, get_ohlcv
from strategies import (
    StrategyConfig,
    SMAcrossStrategy, RSIStrategy, MACDStrategy,
    DonchianChannelStrategy, ATRStopStrategy, KDJStrategy, BollingerBandsStrategy,
)
from backtest import BacktestEngine

RESULTS_DIR = "backtest_results"
os.makedirs(RESULTS_DIR, exist_ok=True)

STRATEGIES = [
    ("SMAcrossStrategy", SMAcrossStrategy, {"fast_period": 10, "slow_period": 30}),
    ("RSIStrategy", RSIStrategy, {"rsi_period": 14, "oversold": 30.0, "overbought": 70.0}),
    ("MACDStrategy", MACDStrategy, {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
    ("DonchianChannelStrategy", DonchianChannelStrategy, {"channel_period": 20, "trend_ema_period": 50}),
    ("BollingerBandsStrategy", BollingerBandsStrategy, {"period": 20, "std_dev": 2.0}),
    ("ATRStopStrategy", ATRStopStrategy, {"ema_period": 20, "atr_period": 14, "atr_multiplier": 2.0}),
    ("KDJStrategy", KDJStrategy, {"k_period": 9, "d_period": 3, "j_period": 3}),
]

timeframes = ["2h", "4h"]

all_results = []

for tf in timeframes:
    print(f"\n{'='*70}")
    print(f"  BTC/USDT {tf} 全策略回测")
    print(f"{'='*70}")
    
    # Get data from cache
    candles = get_ohlcv("BTC/USDT", tf, limit=20000)
    if not candles:
        print(f"  ⚠️ No data in cache for {tf}")
        continue
    
    print(f"  Data: {len(candles)} bars, {datetime.fromtimestamp(candles[0]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(candles[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    
    for strat_name, strat_cls, strat_kwargs in STRATEGIES:
        print(f"\n  >>> {strat_name} on BTC/USDT {tf}")
        
        result_data = {
            "strategy": strat_name,
            "timeframe": tf,
            "symbol": "BTC/USDT",
        }
        
        try:
            # Create config
            config = StrategyConfig(
                stop_loss=0.05,
                take_profit=0.10,
                capital_pct=1.0,
                commission_pct=0.001,
                slippage_pct=0.0005,
            )
            
            # Create strategy instance
            strategy = strat_cls(config=config, **strat_kwargs)
            
            # Create engine
            engine = BacktestEngine(
                strategy=strategy,
                initial_capital=10000.0,
                trade_direction="long",
            )
            
            # Load data - set candles directly
            engine.candles = candles
            engine.entry_signal = []
            engine.exit_signal = []
            
            # Run backtest
            result = engine.run()
            
            result_data.update({
                "total_return_pct": result.total_return_pct,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "max_drawdown_duration_ms": result.max_drawdown_duration_ms,
                "total_trades": result.total_trades,
                "winning_trades": result.winning_trades,
                "losing_trades": result.losing_trades,
                "win_rate_pct": result.win_rate_pct,
                "avg_holding_ms": result.avg_holding_ms,
                "stop_loss_pct": result.stop_loss_pct,
                "take_profit_pct": result.take_profit_pct,
                "capital_pct": result.capital_pct,
                "commission_pct": result.commission_pct,
                "slippage_pct": result.slippage_pct,
                "start_date": datetime.fromtimestamp(candles[0]["timestamp"]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                "end_date": datetime.fromtimestamp(candles[-1]["timestamp"]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
            })
            
            # Calculate profit factor
            if result.trades:
                gross_profit = sum(t.pnl_pct for t in result.trades if t.pnl_pct > 0)
                gross_loss = abs(sum(t.pnl_pct for t in result.trades if t.pnl_pct < 0))
                result_data["profit_factor"] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0
            else:
                result_data["profit_factor"] = 0.0
            
            print(f"      {result.total_return_pct:+.2f}%  SR={result.sharpe_ratio:.2f}  DD={result.max_drawdown_pct:.1f}%  Trades={result.total_trades}  WR={result.win_rate_pct:.1f}%  PF={result_data.get('profit_factor', 0):.2f}")
            
        except Exception as e:
            import traceback
            result_data["error"] = str(e)
            print(f"      ERROR: {e}")
            traceback.print_exc()
        
        all_results.append(result_data)

# Save JSON
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
json_path = f"{RESULTS_DIR}/btc_both_tf_all_strategies_{ts}.json"
with open(json_path, "w") as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
print(f"\n\nJSON: {json_path}")

# Generate markdown report
md_path = f"{RESULTS_DIR}/report_BTC_USDT_both_tf_all_strategies_{ts}.md"

with open(md_path, "w") as f:
    f.write(f"# BTC/USDT 回测报告 (2h + 4h)\n\n")
    f.write(f"**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
    f.write(f"**数据范围**: \n")
    f.write(f"- 4h: 2022-01-01 至 2026-06-11 (共9733条K线)\n")
    f.write(f"- 2h: 2024-03-01 至 2026-06-11 (共9986条K线，受Gate.io API限制)\n\n")
    f.write(f"**策略数量**: {len(STRATEGIES)} 个\n\n")
    f.write(f"**时间周期**: 2h, 4h\n\n")
    f.write(f"**止损/止盈**: 5% / 10%\n\n")
    f.write(f"**手续费**: 0.1% / 滑点: 0.05%\n\n")
    f.write(f"**初始资金**: 10,000 USDT\n\n")
    f.write("---\n\n")
    
    for tf in timeframes:
        tf_results = [r for r in all_results if r.get("timeframe") == tf]
        has_error = any("error" in r for r in tf_results)
        
        if not has_error:
            f.write(f"\n## {tf} 周期\n\n")
            f.write("| 策略 | 总收益% | 夏普比率 | 最大回撤% | 交易次数 | 胜率% | 盈亏比 |\n")
            f.write("|------|--------|---------|---------|---------|-------|-------|\n")
            tf_results.sort(key=lambda x: x.get("total_return_pct", -999), reverse=True)
            for r in tf_results:
                pf = r.get("profit_factor", 0)
                f.write(f"| {r.get('strategy','')} | {r.get('total_return_pct',0):+.2f} | {r.get('sharpe_ratio',0):.2f} | {r.get('max_drawdown_pct',0):.2f} | {r.get('total_trades',0)} | {r.get('win_rate_pct',0):.1f} | {pf:.2f} |\n")
        else:
            f.write(f"\n## {tf} 周期\n\n")
            f.write("| 策略 | 总收益% | 夏普比率 | 最大回撤% | 交易次数 | 胜率% | 盈亏比 |\n")
            f.write("|------|--------|---------|---------|---------|-------|-------|\n")
            for r in tf_results:
                if "error" in r:
                    f.write(f"| {r.get('strategy','')} | ERROR | - | - | - | - | - |\n")
                else:
                    pf = r.get("profit_factor", 0)
                    f.write(f"| {r.get('strategy','')} | {r.get('total_return_pct',0):+.2f} | {r.get('sharpe_ratio',0):.2f} | {r.get('max_drawdown_pct',0):.2f} | {r.get('total_trades',0)} | {r.get('win_rate_pct',0):.1f} | {pf:.2f} |\n")
    
    f.write("\n\n## 最佳策略 (按周期)\n\n")
    for tf in timeframes:
        tf_results = [r for r in all_results if r.get("timeframe") == tf and "error" not in r]
        if tf_results:
            best = max(tf_results, key=lambda x: x.get("total_return_pct", -999))
            worst = min(tf_results, key=lambda x: x.get("total_return_pct", 999))
            f.write(f"- **{tf}** 最佳: {best.get('strategy')} ({best.get('total_return_pct'):+.2f}%) | 最差: {worst.get('strategy')} ({worst.get('total_return_pct'):+.2f}%)\n")
    
    f.write("\n\n## 数据说明\n\n")
    f.write("- 4h数据: 2022-01-01 至 2026-06-11 (共9733条K线)\n")
    f.write("- 2h数据: 2024-03-01 至 2026-06-11 (共9986条K线，受Gate.io API 10000点限制)\n")
    f.write("- 2h数据最早从2024-03-01开始，2022-2024初的2h数据因Gate.io API限制无法获取\n")

print(f"Markdown: {md_path}")
print("\n✅ 全部完成!")