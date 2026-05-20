#!/usr/bin/env python3
"""
CoinGlassSentimentStrategy 专用回测脚本

用法:
  python coinglass_backtest.py                          # 默认 BTC 4h
  python coinglass_backtest.py --symbol ETH --timeframe 1h
  python coinglass_backtest.py --symbol BTC --compare   # 对比多个参数组合
"""
import sys
import os
import json
import math
import logging
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Tuple
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies import CoinGlassSentimentStrategy, StrategyConfig, Signal
from history_cache import get_ohlcv, init_cache_db
from config import STRATEGY_STOP_LOSS, STRATEGY_TAKE_PROFIT

# Gate.io 手续费率 (taker 0.05% ≈ 0.0005, 双向)
FEE_PCT     = 0.0005
# 滑点估算
SLIPPAGE_PCT = 0.0003

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================
# 数据加载（自动处理符号映射）
# ============================================================

def load_candles(symbol: str, timeframe: str, limit: int = 2000):
    """
    从缓存加载K线，自动处理两种符号格式:
      - 缓存中: "BTC/USDT", "ETH/USDT"
      - 配置中: "BTC", "ETH"
    """
    init_cache_db()
    # 尝试两种符号格式
    for sym in [f"{symbol}/USDT", symbol]:
        candles = get_ohlcv(sym, timeframe, limit=limit)
        if candles and len(candles) >= 100:
            logger.info(f"加载 {sym} {timeframe}: {len(candles)} 条 (from {_ts(candles[0]['timestamp'])} to {_ts(candles[-1]['timestamp'])})")
            return candles
    logger.error(f"无法加载 {symbol} {timeframe} 数据")
    return []


def _ts(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


# ============================================================
# 模拟历史情绪数据生成器
# ============================================================

def generate_historical_sentiment(
    candles: List[Dict],
    funding_rate_base: float = 0.0001,
    ls_ratio_base: float = 1.0,
) -> Tuple[List[float], List[float], List[float], List[float]]:
    """
    基于K线数据生成启发式历史情绪序列。

    逻辑：
      - 资金费率: 与价格动量相关（价格涨→资金费率正；价格跌→资金费率负）
      - 多空比: 基于RSI反转（RSI极端时多空比偏离均值）
      - 清算集群: 基于价格位置（高位→上方清算密集，低位→下方清算密集）

    Returns:
        (funding_rates, ls_ratios, liq_levels, liq_strengths)
    """
    closes = [float(c["close"]) for c in candles]
    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]

    # 预计算RSI（14周期）
    def compute_rsi(series, period=14):
        deltas = [series[i] - series[i-1] for i in range(1, len(series))]
        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rsi = [50.0] * period
        for i in range(period, len(series)):
            avg_gain = (avg_gain * (period - 1) + gains[i-1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i-1]) / period
            if avg_loss == 0:
                rsi.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi.append(100 - 100 / (1 + rs))
        return rsi

    rsi = compute_rsi(closes)
    price_mean = sum(closes) / len(closes)
    price_std  = math.sqrt(sum((c - price_mean)**2 for c in closes) / len(closes))

    funding_rates  = []
    ls_ratios      = []
    liq_levels     = []
    liq_strengths  = []

    for i in range(len(closes)):
        # ── 资金费率（基于价格动量 + 随机噪声）─────────────
        mom_5 = (closes[i] / closes[i-5] - 1) if i >= 5 else 0.0
        fr = funding_rate_base + mom_5 * 0.5
        fr = max(-0.001, min(0.001, fr))  # 限制在 ±0.1%
        funding_rates.append(fr)

        # ── 多空比（基于RSI极端程度）──────────────────────
        r = rsi[i]
        if r < 30:       # 超卖 → 散户多头被套 → 机构空头主导
            ls = 1.0 + (30 - r) / 30 * 0.4   # 1.0 ~ 1.4
        elif r > 70:     # 超买 → 散户空头被套 → 机构多头主导
            ls = 1.0 - (r - 70) / 30 * 0.4   # 0.6 ~ 1.0
        else:
            ls = ls_ratio_base
        ls_ratios.append(ls)

        # ── 清算集群（基于价格Z-score位置）────────────────
        z = (closes[i] - price_mean) / price_std if price_std > 0 else 0.0
        # Z>0 → 价格偏高 → 上方空头清算密集 → liq_level>0
        # Z<0 → 价格偏低 → 下方多头清算密集 → liq_level<0
        liq = max(-1.0, min(1.0, z * 0.3))
        liq_levels.append(liq)
        # 清算强度：价格偏离越大，强度越高
        liq_strengths.append(min(1.0, abs(z) / 2.0))

    return funding_rates, ls_ratios, liq_levels, liq_strengths


# ============================================================
# 回测引擎（简化版，不依赖完整backtest.py）
# ============================================================

@dataclass
class Trade:
    entry_time:    int
    entry_price:   float
    exit_time:     int
    exit_price:    float
    side:          str   # "long" / "short"
    pnl_pct:       float
    exit_reason:   str   # "signal" / "stop_loss" / "take_profit"


def run_backtest(
    candles:       List[Dict],
    funding_rates: List[float],
    ls_ratios:     List[float],
    liq_levels:    List[float],
    liq_strengths: List[float],
    symbol:        str,
    timeframe:     str,
    capital:       float = 10000.0,
    stop_loss_pct: float = 0.015,
    take_profit1:  float = 0.025,
    take_profit2:  float = 0.05,
    score_buy:     float = 65.0,
    score_sell:    float = 35.0,
) -> Dict:
    """
    运行回测，返回完整结果字典。
    """
    if not candles:
        return {"error": "无数据"}

    cfg = StrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        capital_pct=1.0,
        stop_loss=stop_loss_pct,
        take_profit=take_profit2,
    )
    strategy = CoinGlassSentimentStrategy(config=cfg)

    # 预热：至少需要30根K线再开始模拟
    warmup = 30

    equity = capital
    trades: List[Trade] = []
    equity_curve: List[Tuple[int, float]] = []
    position = None  # None = 空仓

    tp1_hit = False  # 追踪止盈第一档是否已触发

    for i in range(warmup, len(candles)):
        c = candles[i]
        close = float(c["close"])
        ts    = c["timestamp"]

        # ── 更新策略情绪数据（每根K线用当时的数据）────────────
        strategy.set_sentiment_data(
            funding_rate=funding_rates[i],
            ls_ratio=ls_ratios[i],
            liq_cluster_level=liq_levels[i],
            liq_cluster_strength=liq_strengths[i],
            source="simulated",
        )

        # ── 用全部数据重新计算指标和信号 ────────────────────
        sub_candles = candles[:i+1]
        strategy.populate_indicators(sub_candles)
        entry_signals = strategy.populate_entry_trend(sub_candles)
        exit_signals  = strategy.populate_exit_trend(sub_candles)

        sig_in = entry_signals[-1]
        sig_ou = exit_signals[-1]

        # ── 入场逻辑 ─────────────────────────────────────
        if position is None:
            if sig_in == Signal.BUY:
                # 买入（做多）
                position = {
                    "entry_price": close,
                    "entry_time":  ts,
                    "side":        "long",
                    "stop_loss":   close * (1 - stop_loss_pct),
                    "tp1":         close * (1 + take_profit1),
                    "tp2":         close * (1 + take_profit2),
                }
                tp1_hit = False
            elif sig_in == Signal.SELL:
                # 卖出信号 → 做空（反向）
                position = {
                    "entry_price": close,
                    "entry_time":  ts,
                    "side":        "short",
                    "stop_loss":   close * (1 + stop_loss_pct),
                    "tp1":         close * (1 - take_profit1),
                    "tp2":         close * (1 - take_profit2),
                }
                tp1_hit = False

        else:
            pos  = position
            side = pos["side"]
            ep   = pos["entry_price"]
            sl   = pos["stop_loss"]
            tp1  = pos["tp1"]
            tp2  = pos["tp2"]

            # ── 止损/止盈检查 ─────────────────────────────
            exit_reason = None

            if side == "long":
                # 止损
                if close <= sl:
                    exit_reason = "stop_loss"
                # 止盈第二档
                elif close >= tp2:
                    exit_reason = "take_profit_2"
                # 止盈第一档（追踪止损）
                elif close >= tp1 and not tp1_hit:
                    tp1_hit = True
                    # 止损移动到成本价
                    position["stop_loss"] = ep

            elif side == "short":
                if close >= sl:
                    exit_reason = "stop_loss"
                elif close <= tp2:
                    exit_reason = "take_profit_2"
                elif close <= tp1 and not tp1_hit:
                    tp1_hit = True
                    position["stop_loss"] = ep

            # ── 出场信号强制退出 ──────────────────────────
            if sig_ou == Signal.SELL and exit_reason is None:
                exit_reason = "signal"

            # ── 执行平仓 ─────────────────────────────────
            if exit_reason:
                if side == "long":
                    pnl_pct = (close - ep) / ep
                else:
                    pnl_pct = (ep - close) / ep

                # 扣除手续费和滑点
                net_pnl = pnl_pct - FEE_PCT * 2 - SLIPPAGE_PCT
                equity *= (1 + net_pnl)

                trades.append(Trade(
                    entry_time=pos["entry_time"],
                    entry_price=ep,
                    exit_time=ts,
                    exit_price=close,
                    side=side,
                    pnl_pct=net_pnl * 100,
                    exit_reason=exit_reason,
                ))
                position = None
                tp1_hit  = False

        # ── 记录权益曲线 ─────────────────────────────────
        equity_curve.append((ts, equity))

    # ── 最终持仓强平 ──────────────────────────────────────
    if position is not None:
        c = candles[-1]
        close = float(c["close"])
        ep = position["entry_price"]
        side = position["side"]
        if side == "long":
            pnl_pct = (close - ep) / ep
        else:
            pnl_pct = (ep - close) / ep
        net_pnl = pnl_pct - FEE_PCT * 2 - SLIPPAGE_PCT
        equity *= (1 + net_pnl)
        trades.append(Trade(
            entry_time=position["entry_time"],
            entry_price=ep,
            exit_time=c["timestamp"],
            exit_price=close,
            side=side,
            pnl_pct=net_pnl * 100,
            exit_reason="end_of_data",
        ))
        position = None

    # ── 计算绩效指标 ─────────────────────────────────────
    total_return_pct = (equity - capital) / capital * 100
    winning = [t for t in trades if t.pnl_pct > 0]
    losing  = [t for t in trades if t.pnl_pct <= 0]
    win_rate = len(winning) / len(trades) * 100 if trades else 0.0

    # 夏普比率（简化：日收益/日波动 * sqrt(252)）
    if len(equity_curve) > 1:
        rets = []
        for j in range(1, len(equity_curve)):
            rets.append((equity_curve[j][1] - equity_curve[j-1][1]) / equity_curve[j-1][1])
        import statistics
        mean_ret = statistics.mean(rets) if rets else 0.0
        std_ret  = statistics.stdev(rets) if len(rets) > 1 else 0.0
        sharpe = (mean_ret / std_ret * math.sqrt(365 * 6)) if std_ret > 0 else 0.0  # 4h → 6/day
    else:
        sharpe = 0.0

    # 最大回撤
    peak = capital
    max_dd = 0.0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    return {
        "strategy":      "CoinGlassSentimentStrategy",
        "symbol":        symbol,
        "timeframe":     timeframe,
        "start_date":    _ts(candles[warmup]["timestamp"]),
        "end_date":      _ts(candles[-1]["timestamp"]),
        "total_return_pct": total_return_pct,
        "sharpe_ratio":  sharpe,
        "max_drawdown_pct": max_dd,
        "total_trades":  len(trades),
        "winning_trades": len(winning),
        "losing_trades":  len(losing),
        "win_rate_pct":  win_rate,
        "final_equity":  equity,
        "initial_capital": capital,
        "trades":        trades,
        "equity_curve":   equity_curve,
        "warmup_bars":   warmup,
    }


# ============================================================
# 参数扫描（Grid Search）
# ============================================================

GRID = [
    # score_buy, score_sell, stop_loss_pct, take_profit1, take_profit2
    (65, 35, 0.015, 0.020, 0.040),  # 原始参数
    (60, 40, 0.015, 0.020, 0.040),  # 更宽松买入
    (65, 35, 0.020, 0.025, 0.050),  # 更宽止损
    (70, 30, 0.010, 0.015, 0.030),  # 激进参数
    (60, 40, 0.020, 0.030, 0.060),  # 宽止盈
]


def print_result(r: Dict):
    print()
    print("=" * 62)
    print(f"  CoinGlassSentimentStrategy  回测结果")
    print(f"  {r['symbol']} {r['timeframe']}  |  {r['start_date']} ~ {r['end_date']}")
    print("=" * 62)
    print(f"  {'总收益率':<12} : {r['total_return_pct']:+.2f}%")
    print(f"  {'夏普比率':<12} : {r['sharpe_ratio']:.3f}")
    print(f"  {'最大回撤':<12} : {r['max_drawdown_pct']:.2f}%")
    print(f"  {'总交易次数':<10} : {r['total_trades']}")
    print(f"  {'盈利次数':<12} : {r['winning_trades']}")
    print(f"  {'亏损次数':<12} : {r['losing_trades']}")
    print(f"  {'胜率':<12} : {r['win_rate_pct']:.1f}%")
    print(f"  {'最终权益':<12} : ${r['final_equity']:.2f}  (初始 ${r['initial_capital']})")
    print("-" * 62)

    # 出金原因分布
    if r['trades']:
        from collections import Counter
        reasons = Counter(t.exit_reason for t in r['trades'])
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"  出场 {reason:<20} : {count} 次")
    print("=" * 62)


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CoinGlass情绪策略回测")
    parser.add_argument("--symbol",    default="BTC",  help="交易对，如 BTC/ETH/SOL")
    parser.add_argument("--timeframe", default="4h",   help="K线周期，如 1h/4h/1d")
    parser.add_argument("--capital",   type=float, default=10000.0)
    parser.add_argument("--compare",  action="store_true", help="Grid Search对比")
    parser.add_argument("--output",   default="backtest_results/coinglass",
                        help="结果保存目录")
    args = parser.parse_args()

    symbol_map = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "SUI": "SUI"}
    sym = symbol_map.get(args.symbol.upper(), args.symbol.upper())

    # 加载数据
    candles = load_candles(sym, args.timeframe)
    if not candles:
        print(f"错误: 无法加载 {sym} {args.timeframe} 数据")
        sys.exit(1)

    # 生成历史情绪数据
    fr_base = 0.0001   # 基准资金费率
    ls_base = 1.0      # 基准多空比
    frs, lss, lbs, lss2 = generate_historical_sentiment(
        candles, funding_rate_base=fr_base, ls_ratio_base=ls_base
    )

    print(f"\n数据范围: {len(candles)} 根 {args.timeframe} K线")
    print(f"情绪数据: 资金费率 base={fr_base}, 多空比 base={ls_base}")

    if args.compare:
        print("\n启动 Grid Search 参数扫描...")
        best = None
        best_score = float("-inf")

        for grid in GRID:
            score_buy, score_sell, sl, tp1, tp2 = grid
            r = run_backtest(
                candles, frs, lss, lbs, lss2,
                symbol=sym,
                timeframe=args.timeframe,
                capital=args.capital,
                stop_loss_pct=sl,
                take_profit1=tp1,
                take_profit2=tp2,
                score_buy=score_buy,
                score_sell=score_sell,
            )
            metric = r["sharpe_ratio"] * 0.4 + r["total_return_pct"] * 0.01
            marker = " ← 当前最优" if metric > best_score else ""
            print(f"  buy≥{score_buy} sell≤{score_sell}  SL={sl:.3f} TP={tp1}/{tp2}  "
                  f"ret={r['total_return_pct']:+.1f}% sharpe={r['sharpe_ratio']:.2f} "
                  f"DD={r['max_drawdown_pct']:.1f}% win={r['win_rate_pct']:.0f}%{marker}")
            if metric > best_score:
                best_score = metric
                best = r
                best_params = (score_buy, score_sell, sl, tp1, tp2)

        print_result(best)
        print(f"\n最优参数: buy≥{best_params[0]} sell≤{best_params[1]} "
              f"SL={best_params[2]} TP1={best_params[3]} TP2={best_params[4]}")

    else:
        # 单次回测
        r = run_backtest(
            candles, frs, lss, lbs, lss2,
            symbol=sym,
            timeframe=args.timeframe,
            capital=args.capital,
        )
        print_result(r)

        # 保存结果
        os.makedirs(args.output, exist_ok=True)
        out_file = os.path.join(args.output, f"coinglass_{sym}_{args.timeframe}.json")
        # 序列化时排除 equity_curve（太大）
        r_save = {k: v for k, v in r.items() if k not in ("equity_curve", "trades")}
        with open(out_file, "w") as f:
            json.dump(r_save, f, indent=2, default=str)
        print(f"\n结果已保存: {out_file}")
