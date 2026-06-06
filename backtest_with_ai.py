"""
backtest_with_ai.py — 扩展回测：加入 AI 过滤器模拟，全品种全策略对比
================================================================

用法:
    python backtest_with_ai.py                          # 全部品种+策略
    python backtest_with_ai.py --symbol SUI/USDT        # 单品种
    python backtest_with_ai.py --no-ai                  # 纯策略（无AI）
    python backtest_with_ai.py --ai-threshold 0.35      # AI 阈值（默认0.50）

AI 模拟逻辑（基于实盘 DeepSeek 行为模式）：
  - 计算 regime（ranging/uptrend/downtrend）和 volatility
  - RSI 在超卖区 + regime不是uptrend → AI 大概率否决（confidence~0.4）
  - regime==ranging + signal==BUY → 中等概率否决
  - 趋势明确 + RSI配合 → 大概率放行
"""

import sys
import os
import json
import math
import time as time_mod
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from history_cache import get_ohlcv as cache_get_ohlcv, init_cache_db, save_ohlcv
from strategies import (
    Strategy, StrategyConfig, Signal,
    SMAcrossStrategy, RSIStrategy, MACDStrategy,
    DonchianChannelStrategy, ATRStopStrategy, KDJStrategy, BollingerBandsStrategy,
)
from backtest import BacktestEngine, BacktestResult, _ts_to_dt

# ============================================================
# AI 模拟过滤器
# ============================================================

def compute_rsi(closes: List[float], period: int = 14) -> List[float]:
    """计算 RSI 序列"""
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    rsi = [50.0] * period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi.append(100.0 - 100.0 / (1.0 + rs))
    return rsi

def compute_sma(closes: List[float], period: int) -> List[float]:
    sma = []
    for i in range(len(closes)):
        if i < period - 1:
            sma.append(None)
        else:
            sma.append(sum(closes[i-period+1:i+1]) / period)
    return sma

def detect_regime(closes: List[float], idx: int, sma50: List[float], sma200: List[float]) -> Tuple[str, str]:
    """检测当前市场 regime"""
    if idx < 200:
        return ("unknown", "unknown")
    
    price = closes[idx]
    s50 = sma50[idx]
    s200 = sma200[idx]
    
    if s50 is None or s200 is None:
        return ("unknown", "unknown")
    
    if price > s50 > s200:
        trend = "uptrend"
    elif price < s50 < s200:
        trend = "downtrend"
    else:
        recent = closes[max(0,idx-20):idx+1]
        high = max(recent)
        low = min(recent)
        range_pct = (high - low) / low * 100
        trend = "ranging" if range_pct < 15 else "ranging"
    
    recent_20 = closes[max(0,idx-20):idx+1]
    returns = [(recent_20[i] - recent_20[i-1]) / recent_20[i-1] for i in range(1, len(recent_20))]
    std = (sum(r*r for r in returns) / len(returns)) ** 0.5 if returns else 0
    if std > 0.03:
        vol = "high"
    elif std > 0.015:
        vol = "medium"
    else:
        vol = "low"
    
    return trend, vol

class AISimulator:
    """模拟 DeepSeek AI 过滤器的行为"""
    
    def __init__(self, ai_threshold: float = 0.50, seed: int = 42):
        self.threshold = ai_threshold
        self.seed = seed
        self.call_count = 0
        self.reject_count = 0
        self.approve_count = 0
        self.fuzzy_count = 0
    
    def should_reject(self, signal: int, closes: List[float], idx: int,
                      rsi_vals: List[float], sma50: List[float], sma200: List[float]) -> Tuple[bool, str, float]:
        self.call_count += 1
        
        if signal not in (Signal.BUY, Signal.SELL):
            return True, "非交易信号", 1.0
        
        trend, vol = detect_regime(closes, idx, sma50, sma200)
        rsi = rsi_vals[idx] if idx < len(rsi_vals) else 50.0
        
        if idx >= 24:
            price_24h_ago = closes[idx-24]
            price_change = (closes[idx] - price_24h_ago) / price_24h_ago * 100
        else:
            price_change = 0
        
        # 前置硬拦截
        if signal == Signal.BUY:
            if price_change < -8.0:
                self.reject_count += 1
                return True, f"24h跌幅{price_change:.1f}%极端，禁止买入", 0.95
            if rsi < 15.0 and price_change < -3.0:
                self.reject_count += 1
                return True, f"RSI={rsi:.1f}极深超卖+仍在下跌", 0.90
        if signal == Signal.SELL:
            if price_change > 10.0:
                self.reject_count += 1
                return True, f"24h涨幅{price_change:.1f}%极端，禁止做空", 0.95
        
        import random
        rng = random.Random(f"{self.seed}_{idx}_{self.call_count}")
        
        confidence = 0.50
        
        if trend == "uptrend" and signal == Signal.BUY:
            base_confidence = 0.65
            if rsi > 70:
                base_confidence -= 0.15
            if price_change > 5:
                base_confidence -= 0.10
            confidence = base_confidence + rng.uniform(-0.08, 0.05)
        elif trend == "downtrend" and signal == Signal.SELL:
            base_confidence = 0.62
            if rsi < 30:
                base_confidence -= 0.10
            confidence = base_confidence + rng.uniform(-0.08, 0.05)
        elif trend == "ranging":
            if rsi < 35 and signal == Signal.BUY:
                base_confidence = 0.38
                if price_change < -2:
                    base_confidence -= 0.05
            elif rsi > 65 and signal == Signal.SELL:
                base_confidence = 0.38
            else:
                base_confidence = 0.42
            confidence = base_confidence + rng.uniform(-0.05, 0.08)
        else:
            confidence = 0.40 + rng.uniform(-0.05, 0.10)
        
        confidence = max(0.20, min(0.95, confidence))
        
        if confidence >= self.threshold:
            self.approve_count += 1
            return False, f"AI批准(conf={confidence:.2f})", confidence
        elif confidence >= 0.35:
            self.fuzzy_count += 1
            return True, f"AI否决(信号模糊)", confidence
        else:
            self.reject_count += 1
            return True, f"AI否决", confidence
    
    def stats(self) -> dict:
        return {
            "total_calls": self.call_count,
            "approved": self.approve_count,
            "rejected": self.reject_count,
            "fuzzy_rejected": self.fuzzy_count,
            "approval_rate": round(self.approve_count / max(1, self.call_count), 3),
            "threshold": self.threshold,
        }


# ============================================================
# 回测运行器
# ============================================================

def run_backtest_with_ai(
    symbol, timeframe, strategy_cls, strategy_kwargs,
    ai_sim=None, direction="long", stop_loss=0.05, take_profit=0.10,
    capital_pct=1.0, initial_capital=10000.0,
    commission_pct=0.001, slippage_pct=0.0005, max_lookback_months=24,
):
    init_cache_db()
    candles = cache_get_ohlcv(symbol, timeframe, limit=20000)
    
    if len(candles) < 100:
        import crypto_api
        since_ms = int((datetime.now().timestamp() - max_lookback_months * 30 * 24 * 3600) * 1000)
        online = crypto_api.get_ohlcv(symbol, timeframe, since=since_ms, limit=5000)
        if online:
            save_ohlcv(symbol, timeframe, online)
            candles = cache_get_ohlcv(symbol, timeframe, limit=20000)
    
    cutoff_ts = int((datetime.now().timestamp() - max_lookback_months * 30 * 24 * 3600) * 1000)
    candles = [c for c in candles if c.get("timestamp", 0) >= cutoff_ts]
    
    if len(candles) < 50:
        return {"error": f"数据不足: {len(candles)} 条", "symbol": symbol}
    
    closes = [c["close"] for c in candles]
    rsi_vals = compute_rsi(closes, 14)
    sma50 = compute_sma(closes, 50)
    sma200 = compute_sma(closes, 200)
    
    config = StrategyConfig(
        symbol=symbol, timeframe=timeframe,
        capital_pct=capital_pct, stop_loss=stop_loss, take_profit=take_profit,
        commission_pct=commission_pct, slippage_pct=slippage_pct,
    )
    
    strat_kw = {k: v for k, v in strategy_kwargs.items() if k != "config"}
    strategy = strategy_cls(config=config, **strat_kw)
    
    # 无 AI：直接用引擎
    if not ai_sim:
        engine = BacktestEngine(
            strategy=strategy, initial_capital=initial_capital, trade_direction=direction,
        )
        engine.commission_pct = commission_pct
        engine.slippage_pct = slippage_pct
        engine.load_data(candles)
        result = engine.run()
        return {
            "symbol": symbol, "timeframe": timeframe,
            "strategy": strategy_cls.__name__, "ai_filter": False,
            "candles": len(candles),
            "total_return_pct": round(result.total_return_pct, 2),
            "sharpe_ratio": round(result.sharpe_ratio, 2),
            "max_drawdown_pct": round(result.max_drawdown_pct, 2),
            "win_rate_pct": round(result.win_rate_pct, 1),
            "total_trades": result.total_trades,
            "profit_factor": round(result.profit_factor, 2),
            "avg_pnl_pct": round(result.avg_pnl_pct, 2),
            "start_date": result.start_date,
            "end_date": result.end_date,
        }
    
    # AI 过滤模式：手动循环
    trades = []
    equity = initial_capital
    equity_curve = [(candles[0]["timestamp"], equity)]
    position = None
    
    strategy._indicators = {}
    strategy.populate_indicators(candles)
    entry_signals = strategy.populate_entry_trend(candles)
    exit_signals = strategy.populate_exit_trend(candles)
    
    for i, candle in enumerate(candles):
        if i < 50:
            continue
        
        ts = candle["timestamp"]
        close = candle["close"]
        
        # 止损/止盈
        if position:
            if position["side"] == "long":
                pnl = (close - position["entry_price"]) / position["entry_price"]
            else:
                pnl = (position["entry_price"] - close) / position["entry_price"]
            
            if pnl <= -stop_loss:
                trade_value = equity * capital_pct
                equity += trade_value * pnl
                trades.append({"pnl_pct": pnl*100})
                position = None
                equity_curve.append((ts, equity))
                continue
            if pnl >= take_profit:
                trade_value = equity * capital_pct
                equity += trade_value * pnl
                trades.append({"pnl_pct": pnl*100})
                position = None
                equity_curve.append((ts, equity))
                continue
        
        buy_sig = entry_signals[i] == Signal.BUY
        sell_sig = exit_signals[i] == Signal.SELL
        
        # ── 信号处理（先平后开，与 BacktestEngine 一致）──
        
        # 多头出场（SELL 信号）
        if position and position["side"] == "long" and sell_sig:
            pnl = (close * (1 - slippage_pct) - position["entry_price"]) / position["entry_price"]
            trade_value = equity * capital_pct
            equity += trade_value * pnl
            trades.append({"pnl_pct": pnl*100})
            position = None
            equity_curve.append((ts, equity))
            continue
        
        # 空头出场（BUY 信号）
        if position and position["side"] == "short" and buy_sig:
            pnl = (position["entry_price"] - close * (1 + slippage_pct)) / position["entry_price"]
            trade_value = equity * capital_pct
            equity += trade_value * pnl
            trades.append({"pnl_pct": pnl*100})
            position = None
            equity_curve.append((ts, equity))
            continue
        
        # 无持仓：处理入场信号
        if position is None:
            sig = None
            if buy_sig:
                sig = Signal.BUY
            elif sell_sig:
                sig = Signal.SELL
            
            if sig is None:
                equity_curve.append((ts, equity))
                continue
            
            # AI 过滤
            should_reject, reason, conf = ai_sim.should_reject(
                sig, closes, i, rsi_vals, sma50, sma200
            )
            if should_reject:
                equity_curve.append((ts, equity))
                continue
            
            # 入场
            if sig == Signal.BUY:
                position = {"entry_price": close * (1 + slippage_pct), "entry_time": ts, "side": "long"}
            elif sig == Signal.SELL:
                position = {"entry_price": close * (1 - slippage_pct), "entry_time": ts, "side": "short"}
        
        equity_curve.append((ts, equity))
    
    # 强制平仓
    if position and candles:
        last_close = candles[-1]["close"]
        if position["side"] == "long":
            pnl = (last_close - position["entry_price"]) / position["entry_price"]
        else:
            pnl = (position["entry_price"] - last_close) / position["entry_price"]
        trade_value = equity * capital_pct
        equity += trade_value * pnl
        trades.append({"pnl_pct": pnl*100})
    
    total_return = (equity - initial_capital) / initial_capital * 100 if initial_capital > 0 else 0
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    total_trades = len(trades)
    win_rate = wins / total_trades * 100 if total_trades > 0 else 0
    
    win_pnls = [t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]
    loss_pnls = [abs(t["pnl_pct"]) for t in trades if t["pnl_pct"] <= 0]
    avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
    avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
    pf = avg_win / avg_loss if avg_loss > 0 else (999 if avg_win > 0 else 0)
    avg_pnl = sum(t["pnl_pct"] for t in trades) / total_trades if total_trades > 0 else 0
    
    peak = initial_capital
    max_dd = 0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    
    if len(equity_curve) > 1:
        returns = []
        for j in range(1, len(equity_curve)):
            ret = (equity_curve[j][1] - equity_curve[j-1][1]) / max(1, equity_curve[j-1][1])
            returns.append(ret)
        avg_ret = sum(returns) / len(returns)
        std_ret = (sum((r - avg_ret)**2 for r in returns) / len(returns)) ** 0.5 if returns else 0
        periods = {"1h": 365*24, "2h": 365*12, "4h": 365*6, "1d": 365}.get(timeframe, 365*24)
        sharpe = (avg_ret / std_ret * periods ** 0.5) if std_ret > 0 else 0
    else:
        sharpe = 0
    
    start_dt = _ts_to_dt(candles[0]["timestamp"])
    end_dt = _ts_to_dt(candles[-1]["timestamp"])
    
    return {
        "symbol": symbol, "timeframe": timeframe,
        "strategy": strategy_cls.__name__, "ai_filter": True,
        "ai_threshold": ai_sim.threshold, "ai_stats": ai_sim.stats(),
        "candles": len(candles),
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "win_rate_pct": round(win_rate, 1),
        "total_trades": total_trades,
        "profit_factor": round(pf, 2),
        "avg_pnl_pct": round(avg_pnl, 2),
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),
    }


# ============================================================
# 主入口
# ============================================================

STRATEGIES = [
    (SMAcrossStrategy, {"fast_period": 10, "slow_period": 30}),
    (RSIStrategy, {"rsi_period": 14, "oversold": 30.0, "overbought": 70.0}),
    (MACDStrategy, {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
    (DonchianChannelStrategy, {"channel_period": 20, "trend_ema_period": 50}),
    (BollingerBandsStrategy, {"period": 20, "std_dev": 2.0, "oversold_threshold": 0.0}),
    (ATRStopStrategy, {"ema_period": 20, "atr_period": 14, "atr_multiplier": 2.0}),
    (KDJStrategy, {"k_period": 9, "d_period": 3, "j_period": 3}),
]

SYMBOLS_TIMEFRAMES = [
    ("SUI/USDT", "2h"),
    ("SOL/USDT", "2h"),
    ("XAUT/USDT", "4h"),
]

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI 过滤回测")
    parser.add_argument("--symbol", type=str, help="单品种")
    parser.add_argument("--no-ai", action="store_true", help="纯策略无AI")
    parser.add_argument("--ai-threshold", type=float, default=0.50)
    parser.add_argument("--direction", type=str, default="long")
    parser.add_argument("--stop-loss", type=float, default=0.05)
    parser.add_argument("--take-profit", type=float, default=0.10)
    parser.add_argument("--capital-pct", type=float, default=1.0)
    parser.add_argument("--max-months", type=int, default=24)
    
    args = parser.parse_args()
    
    if args.symbol:
        symbols_tfs = [(args.symbol, tf) for s, tf in SYMBOLS_TIMEFRAMES if args.symbol in s]
        if not symbols_tfs:
            for s, tf in SYMBOLS_TIMEFRAMES:
                if args.symbol in s:
                    symbols_tfs = [(s, tf)]
                    break
    else:
        symbols_tfs = SYMBOLS_TIMEFRAMES
    
    all_results = []
    
    for symbol, timeframe in symbols_tfs:
        print(f"\n{'='*80}")
        label = "纯策略" if args.no_ai else f"AI阈值={args.ai_threshold}"
        print(f"  {symbol} {timeframe}  ({label})")
        print(f"{'='*80}")
        
        for strat_cls, strat_kw in STRATEGIES:
            name = strat_cls.__name__
            ai_sim = None if args.no_ai else AISimulator(ai_threshold=args.ai_threshold, seed=hash(symbol) % 10000)
            
            try:
                result = run_backtest_with_ai(
                    symbol=symbol, timeframe=timeframe,
                    strategy_cls=strat_cls, strategy_kwargs=strat_kw,
                    ai_sim=ai_sim,
                    direction=args.direction,
                    stop_loss=args.stop_loss, take_profit=args.take_profit,
                    capital_pct=args.capital_pct, max_lookback_months=args.max_months,
                )
            except Exception as e:
                import traceback
                result = {"symbol": symbol, "strategy": name, "error": str(e)[:200]}
                traceback.print_exc()
            
            tr = result.get("total_return_pct", 0)
            dd = result.get("max_drawdown_pct", 0)
            wr = result.get("win_rate_pct", 0)
            trades = result.get("total_trades", 0)
            sharpe = result.get("sharpe_ratio", 0)
            pf = result.get("profit_factor", 0)
            
            ai_info = ""
            if result.get("ai_filter"):
                stats = result.get("ai_stats", {})
                ai_info = f" | AI:批准{stats.get('approved',0)}/否决{stats.get('rejected',0)+stats.get('fuzzy_rejected',0)}"
            
            error = result.get("error", "")
            if error:
                print(f"  {name:<14} ERROR: {error}")
            else:
                print(f"  {name:<14} {tr:>+8.2f}%  SR={sharpe:.2f}  DD={dd:>6.1f}%  "
                      f"WR={wr:>5.1f}%  PF={pf:.2f}  Trades={trades:>4d}{ai_info}")
            
            all_results.append(result)
    
    by_symbol = defaultdict(list)
    for r in all_results:
        by_symbol[r.get("symbol", "?")].append(r)
    
    for sym, results in by_symbol.items():
        valid = [r for r in results if "error" not in r]
        valid.sort(key=lambda r: r.get("total_return_pct", -999), reverse=True)
        
        print(f"\n--- {sym} 排名 (TOP 3) ---")
        for rank, r in enumerate(valid[:3], 1):
            tag = "★" if rank == 1 else " "
            ai_tag = "AI=" + str(r.get("ai_threshold","-")) if r.get("ai_filter") else "无AI"
            print(f"  {rank}. {tag} {r['strategy']:<14} {r['total_return_pct']:>+8.2f}%  "
                  f"SR={r['sharpe_ratio']:.2f}  DD={r['max_drawdown_pct']:.1f}%  "
                  f"Trades={r['total_trades']}  [{ai_tag}]")
    
    out_path = f"backtest_results/ai_backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs("backtest_results", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📄 完整结果: {out_path}")
