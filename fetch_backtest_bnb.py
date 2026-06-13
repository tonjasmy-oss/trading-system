#!/usr/bin/env python3
"""
BNB/USDT 2h 数据拉取（Binance）+ 全策略回测
"""
import sys, os, json, time, requests
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ATRSTOP_EMA_PERIOD, ATRSTOP_ATR_PERIOD, ATRSTOP_ATR_MULTIPLIER
from backtest import BacktestEngine
from strategies import (
    SMAcrossStrategy, RSIStrategy, MACDStrategy,
    DonchianChannelStrategy, BollingerBandsStrategy,
    ATRStopStrategy, KDJStrategy, StrategyConfig,
)
from history_cache import init_cache_db, save_ohlcv, get_ohlcv as cache_get_ohlcv

init_cache_db()

SYMBOL = "BNB/USDT"
RESULTS_DIR = "backtest_results"
os.makedirs(RESULTS_DIR, exist_ok=True)

STRATEGIES_CONFIG = [
    ("DONCHIAN", DonchianChannelStrategy, {"channel_period": 20, "trend_ema_period": 50}),
    ("KDJ", KDJStrategy, {"k_period": 9, "d_period": 3, "j_period": 3}),
    ("MACD", MACDStrategy, {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
    ("BOLLINGER", BollingerBandsStrategy, {"period": 20, "std_dev": 2.0}),
    ("RSI", RSIStrategy, {"rsi_period": 14, "oversold": 30.0, "overbought": 70.0}),
    ("ATRSTOP", ATRStopStrategy, {"ema_period": ATRSTOP_EMA_PERIOD, "atr_period": ATRSTOP_ATR_PERIOD, "atr_multiplier": ATRSTOP_ATR_MULTIPLIER}),
    ("SMA", SMAcrossStrategy, {"fast_period": 10, "slow_period": 30}),
]


def binance_fetch(symbol_base, timeframe, start_ms, limit=1000):
    """从 Binance 获取历史 K 线"""
    symbol = f"{symbol_base.upper()}USDT"
    tf_map = {"2h": "2h", "4h": "4h", "1h": "1h", "1d": "1d"}
    interval = tf_map.get(timeframe, timeframe)

    all_candles = []
    end_ms = int(time.time() * 1000)

    while True:
        params = {
            "symbol": symbol, "interval": interval,
            "limit": min(limit, 1000),
            "startTime": start_ms,
        }
        if all_candles:
            params["startTime"] = all_candles[-1]["timestamp"] + 1

        try:
            resp = requests.get("https://api.binance.com/api/v3/klines", params=params, timeout=20)
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
                break
            raw = resp.json()
            if not isinstance(raw, list) or len(raw) == 0:
                break

            for item in raw:
                ts_ms = item[0]
                if ts_ms >= end_ms:
                    continue
                all_candles.append({
                    "timestamp": ts_ms,
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                })

            last_ts = all_candles[-1]["timestamp"]
            print(f"    获取 {len(raw)} 条, 最新 {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
            if len(raw) < limit:
                break
            time.sleep(1.0)
        except Exception as e:
            print(f"  Error: {e}")
            break

    return all_candles


def fetch_and_save(tf):
    start_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)
    print(f"\n📥 拉取 {SYMBOL} {tf}（Binance, {start_dt.strftime('%Y-%m-%d')} 至今）...")

    candles = binance_fetch("BNB", tf, start_ms)
    # 去重排序
    seen = set()
    unique = []
    for c in candles:
        if c["timestamp"] not in seen:
            seen.add(c["timestamp"])
            unique.append(c)
    unique.sort(key=lambda x: x["timestamp"])

    print(f"  共 {len(unique)} 条")
    if unique:
        first = datetime.fromtimestamp(unique[0]['timestamp']/1000, tz=timezone.utc)
        last = datetime.fromtimestamp(unique[-1]['timestamp']/1000, tz=timezone.utc)
        print(f"  范围: {first.strftime('%Y-%m-%d')} → {last.strftime('%Y-%m-%d')}")
        save_ohlcv(SYMBOL, tf, unique)
        print(f"  ✅ 已保存")
    return unique


def run_backtest(tf):
    candles = cache_get_ohlcv(SYMBOL, tf, limit=20000)
    if not candles or len(candles) < 50:
        print(f"  ❌ {tf} 数据不足: {len(candles) if candles else 0} 条")
        return []
    print(f"\n📊 {SYMBOL} {tf} 回测 ({len(candles)} 条)")
    results = []
    for name, strat_cls, strat_kw in STRATEGIES_CONFIG:
        config = StrategyConfig(symbol=SYMBOL, timeframe=tf, stop_loss=0.05, take_profit=0.10, trade_direction="both")
        try:
            strategy = strat_cls(config, **strat_kw)
            engine = BacktestEngine(strategy, initial_capital=10000, trade_direction="both")
            engine.candles = candles
            engine.compute_signals()
            r = engine.run()
            pf = round(r.winning_trades / max(r.losing_trades, 1), 2)
            entry = {
                "strategy": strat_cls.__name__, "timeframe": tf, "symbol": SYMBOL,
                "total_return_pct": round(r.total_return_pct, 2),
                "sharpe_ratio": round(r.sharpe_ratio, 2),
                "max_drawdown_pct": round(r.max_drawdown_pct, 2),
                "total_trades": r.total_trades,
                "winning_trades": r.winning_trades, "losing_trades": r.losing_trades,
                "win_rate_pct": round(r.win_rate_pct, 2),
                "profit_factor": pf,
                "start_date": datetime.fromtimestamp(candles[0]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d'),
                "end_date": datetime.fromtimestamp(candles[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d'),
            }
            results.append(entry)
            pnl = "+" if r.total_return_pct >= 0 else ""
            print(f"  {name:>12s}: {pnl}{r.total_return_pct:.1f}%  PF={pf:.2f}  WR={r.win_rate_pct:.1f}%  T={r.total_trades}  DD=-{r.max_drawdown_pct:.1f}%")
        except Exception as e:
            print(f"  {name:>12s}: ❌ {e}")
    return results


def main():
    print(f"BNB/USDT 回测 · 起始 2024-01-01")

    # Fetch 2h from Binance, 4h already cached
    fetch_and_save("2h")

    # Run backtests
    all_results = []
    for tf in ["2h", "4h"]:
        res = run_backtest(tf)
        all_results.extend(res)

    # Save
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(RESULTS_DIR, f"bnb_both_tf_all_strategies_{date_str}.json")
    with open(out, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n✅ {out}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  BNB/USDT 回测汇总")
    print(f"{'='*60}")
    for tf in ["2h", "4h"]:
        tf_r = [r for r in all_results if r.get("timeframe") == tf and "error" not in r]
        if tf_r:
            best = max(tf_r, key=lambda x: x.get("total_return_pct", -999))
            print(f"\n  {tf} 最优: {best['strategy']}")
            print(f"    收益: {best['total_return_pct']:+.1f}%  PF={best['profit_factor']:.2f}  WR={best['win_rate_pct']:.1f}%  T={best['total_trades']}  DD=-{best['max_drawdown_pct']:.1f}%")

    print("\n完成!")


if __name__ == "__main__":
    main()
