"""
BTC TrendFlow 趋势流策略（BTC/USDT 专用）

设计理念：
  BTC 作为市值最大的加密资产，具有明显的牛熊周期和趋势延续性。
  本策略通过 EMA 三级趋势结构 + ADX 趋势强度 + 成交量确认，
  在强趋势中入场，趋势转弱时离场，避免震荡磨损。

核心逻辑：
  1. EMA 趋势结构（20/50/200）— 三级多头/空头排列确认方向
  2. ADX(14) > 20 — 只参与有强度的趋势
  3. 成交量 > 20周期均量 — 确认市场参与度
  4. ATR 移动止损 — 动态跟踪趋势，保护利润
  5. 仓位按 ATR 反比 — 波动大时减仓，波动小时加仓

入场条件（同时满足）：
  LONG:  EMA20 > EMA50 > EMA200  AND  ADX > 20  AND  Vol > VolSMA20
  SHORT: EMA20 < EMA50 < EMA200  AND  ADX > 20  AND  Vol > VolSMA20

出场条件：
  - EMA20 反向穿越 EMA50（趋势结构破坏）
  - ATR 移动止损触发
  - 硬止损 5%

参数（BTC/USDT 优化）：
  - 时间框架: 4h（BTC 核心交易周期）
  - 4h最优: EMA20/50/200, ADX>15, SL5%, TP25%, TS6%, +134.9%, 夏普1.07
  - 2h最优: EMA40/120/400, ADX>15, SL5%, TP25%, TS6%, +157.2%, 夏普0.96
"""

import os, sys, math, logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("btc_trendflow")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies import Strategy, StrategyConfig, Signal, compute_atr

# ============================================================
# 基础工具
# ============================================================

def calc_ema(prices: List[float], period: int) -> List[float]:
    """EMA"""
    if len(prices) < period:
        return [0.0] * len(prices)
    mult = 2.0 / (period + 1)
    result = [0.0] * (period - 1) + [sum(prices[:period]) / period]
    for i in range(period, len(prices)):
        result.append((prices[i] - result[-1]) * mult + result[-1])
    return result

def calc_sma(prices: List[float], period: int) -> List[float]:
    """SMA"""
    result = [0.0] * len(prices)
    for i in range(period - 1, len(prices)):
        result[i] = sum(prices[i - period + 1:i + 1]) / period
    return result

def calc_adx(candles: List[Dict], period: int = 14) -> List[float]:
    """ADX"""
    n = len(candles)
    if n < period + 1:
        return [0.0] * n

    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n

    for i in range(1, n):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i-1]["close"]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = h - candles[i-1]["high"]
        dn = candles[i-1]["low"] - l
        plus_dm[i] = up if up > dn and up > 0 else 0.0
        minus_dm[i] = dn if dn > up and dn > 0 else 0.0

    atr = [0.0] * n
    atr[period] = sum(tr[1:period+1]) / period
    for i in range(period + 1, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period

    pdi = [0.0] * n
    mdi = [0.0] * n
    pdi[period] = sum(plus_dm[1:period+1]) / period
    mdi[period] = sum(minus_dm[1:period+1]) / period
    for i in range(period + 1, n):
        pdi[i] = (pdi[i-1] * (period - 1) + plus_dm[i]) / period
        mdi[i] = (mdi[i-1] * (period - 1) + minus_dm[i]) / period

    dx = [0.0] * n
    for i in range(period, n):
        if atr[i] > 0:
            diff = abs(pdi[i] - mdi[i])
            sm = pdi[i] + mdi[i]
            dx[i] = (diff / sm * 100) if sm > 0 else 0.0

    adx = [0.0] * n
    adx[period * 2 - 1] = sum(dx[period:period*2]) / period
    for i in range(period * 2, n):
        adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period

    return adx


# ============================================================
# 风险与信号
# ============================================================

class TradeSide(Enum):
    LONG = "long"
    SHORT = "short"

class ExitReason(Enum):
    STOP_LOSS = "SL"
    TAKE_PROFIT = "TP"
    TRAILING_STOP = "TS"
    TREND_WEAKEN = "TW"  # EMA 结构破坏
    SIGNAL_EXIT = "SE"

@dataclass
class Trade:
    entry_time: datetime
    exit_time: Optional[datetime] = None
    side: TradeSide = TradeSide.LONG
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: float = 0.0
    pnl_pct: float = 0.0
    pnl_abs: float = 0.0
    exit_reason: Optional[ExitReason] = None
    stop_loss: float = 0.0
    take_profit: float = 0.0
    entry_atr: float = 0.0


# ============================================================
# 策略主体
# ============================================================

class BTCTrendFlowStrategy(Strategy):
    """
    BTC TrendFlow — EMA 趋势结构 + ADX 强度 + 成交量确认

    参数:
      - ema_fast/slow/trend: EMA 周期 (20/50/200)
      - adx_period: ADX 周期 (14)
      - adx_threshold: ADX 阈值 (20)
      - vol_period: 成交量 SMA 周期 (20)
    """

    def __init__(self, config=None,
                 ema_fast=20, ema_slow=50, ema_trend=200,
                 adx_period=14, adx_threshold=15,
                 vol_period=20):
        super().__init__(config)
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_trend = ema_trend
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.vol_period = vol_period

    def populate_indicators(self, candles):
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        volumes = [c.get("volume", 0) for c in candles]

        ema_f = calc_ema(closes, self.ema_fast)
        ema_s = calc_ema(closes, self.ema_slow)
        ema_t = calc_ema(closes, self.ema_trend)
        adx = calc_adx(candles, self.adx_period)
        atr = compute_atr(candles, 14)
        vol_sma = calc_sma(volumes, self.vol_period)

        self._indicators = {
            "ema_fast": ema_f, "ema_slow": ema_s, "ema_trend": ema_t,
            "adx": adx, "atr": atr, "vol_sma": vol_sma,
            "close": closes, "high": highs, "low": lows, "volume": volumes,
        }
        return self._indicators

    def populate_entry_trend(self, candles):
        ind = self._indicators
        if not ind:
            self.populate_indicators(candles)
            ind = self._indicators

        n = len(candles)
        signals = [Signal.HOLD] * n
        ef = ind["ema_fast"]; es = ind["ema_slow"]; et = ind["ema_trend"]
        adx = ind["adx"]; vol = ind["volume"]; vol_sma = ind["vol_sma"]

        for i in range(self.ema_trend, n):
            if ef[i] == 0 or es[i] == 0 or et[i] == 0:
                continue
            if adx[i] < self.adx_threshold:
                continue
            if vol_sma[i] == 0 or vol[i] < vol_sma[i]:
                continue

            # 三级多头排列
            if ef[i] > es[i] > et[i]:
                signals[i] = Signal.BUY
            # 三级空头排列
            elif ef[i] < es[i] < et[i]:
                signals[i] = Signal.SELL

        return signals

    def populate_exit_trend(self, candles):
        ind = self._indicators
        if not ind:
            self.populate_indicators(candles)
            ind = self._indicators

        n = len(candles)
        signals = [Signal.HOLD] * n
        ef = ind["ema_fast"]; es = ind["ema_slow"]

        for i in range(1, n):
            if ef[i] == 0 or es[i] == 0 or ef[i-1] == 0 or es[i-1] == 0:
                continue
            # EMA20 下穿 EMA50 → 多头趋势破坏
            if ef[i-1] >= es[i-1] and ef[i] < es[i]:
                signals[i] = Signal.SELL
            # EMA20 上穿 EMA50 → 空头趋势破坏
            elif ef[i-1] <= es[i-1] and ef[i] > es[i]:
                signals[i] = Signal.SELL

        return signals

    @staticmethod
    def for_2h():
        """2h 周期优化预设（EMA40/120/400 ADX15 SL5% TP25% TS6%）"""
        return BTCTrendFlowStrategy(
            ema_fast=40, ema_slow=120, ema_trend=400,
            adx_threshold=15, vol_period=20,
        )


class BTCTrendFlow2HStrategy(BTCTrendFlowStrategy):
    """BTC TrendFlow 2h 专用版 — EMA40/120/400 ADX>15"""
    def __init__(self, config=None):
        super().__init__(config,
            ema_fast=40, ema_slow=120, ema_trend=400,
            adx_threshold=15, vol_period=20,
        )


# ============================================================
# 风险计算器
# ============================================================

class RiskCalculator:
    def __init__(self, risk_pct=0.01, sl_pct=0.05, tp_pct=0.25,
                 ts_pct=0.06, atr_ts_mult=2.5, max_dd=0.40):
        self.risk_pct = risk_pct
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.ts_pct = ts_pct
        self.atr_ts_mult = atr_ts_mult
        self.max_dd = max_dd

    def position_size(self, capital, price, atr):
        if price <= 0 or capital <= 0:
            return 0.0
        risk_amount = capital * self.risk_pct
        stop_pct = min((atr * self.atr_ts_mult / price), self.sl_pct) if atr > 0 else self.sl_pct
        return risk_amount / (price * stop_pct) if stop_pct > 0 else 0.0

    def stop(self, price, side):
        return price * (1 - self.sl_pct) if side == TradeSide.LONG else price * (1 + self.sl_pct)

    def target(self, price, side):
        return price * (1 + self.tp_pct) if side == TradeSide.LONG else price * (1 - self.tp_pct)

    def trailing(self, best_price, side):
        if side == TradeSide.LONG:
            return best_price * (1 - self.ts_pct)
        return best_price * (1 + self.ts_pct)


# ============================================================
# 回测引擎
# ============================================================

class TrendFlowBacktest:
    def __init__(self, candles, initial_capital=10000.0,
                 risk=None, strategy=None, leverage=3.0):
        self.candles = candles
        self.n = len(candles)
        self.initial_capital = initial_capital
        self.risk = risk or RiskCalculator()
        self.strategy = strategy or BTCTrendFlowStrategy()
        self.leverage = leverage

        self.capital = initial_capital
        self.peak = initial_capital
        self.max_dd = 0.0
        self.trades: List[Trade] = []
        self.current: Optional[Trade] = None
        self.best_price = 0.0
        self.equity = []

        logger.info("[TrendFlow] 计算指标...")
        self.strategy.populate_indicators(candles)
        self.entry_signals = self.strategy.populate_entry_trend(candles)
        self.exit_signals = self.strategy.populate_exit_trend(candles)

    def run(self):
        logger.info(f"[TrendFlow] 回测 {self.n} 根K线, 初始 ${self.initial_capital:,.0f}")

        for i in range(self.n):
            price = self.candles[i]["close"]
            high = self.candles[i]["high"]
            low = self.candles[i]["low"]

            # ── 平仓检查 ──
            if self.current is not None:
                reason = self._check_exit(i, price, high, low)
                if reason:
                    self._close(i, price, reason)

            # ── 开仓检查 ──
            if self.current is None and self.entry_signals[i] != Signal.HOLD:
                self._open(i, price)

            # ── 权益/回撤 ──
            eq = self._equity(price)
            self.equity.append(eq)
            self.peak = max(self.peak, eq)
            dd = (self.peak - eq) / self.peak if self.peak > 0 else 0
            self.max_dd = max(self.max_dd, dd)

            if dd >= self.risk.max_dd:
                if self.current:
                    self._close(i, price, ExitReason.STOP_LOSS)
                logger.warning(f"[TrendFlow] ⛔ 回撤 {dd:.1%} 达上限")
                break

        if self.current:
            self._close(self.n - 1, self.candles[-1]["close"], ExitReason.SIGNAL_EXIT)

        return self._report()

    def _check_exit(self, i, price, high, low):
        t = self.current
        ind = self.strategy._indicators

        if t.side == TradeSide.LONG:
            self.best_price = max(self.best_price, high)
            if price <= t.stop_loss:
                return ExitReason.STOP_LOSS
            if price >= t.take_profit:
                return ExitReason.TAKE_PROFIT
            ts = self.risk.trailing(self.best_price, t.side)
            if price <= ts:
                return ExitReason.TRAILING_STOP
        else:
            self.best_price = min(self.best_price, low)
            if price >= t.stop_loss:
                return ExitReason.STOP_LOSS
            if price <= t.take_profit:
                return ExitReason.TAKE_PROFIT
            ts = self.risk.trailing(self.best_price, t.side)
            if price >= ts:
                return ExitReason.TRAILING_STOP

        # EMA 结构破坏
        if self.exit_signals[i] == Signal.SELL:
            return ExitReason.TREND_WEAKEN

        return None

    def _open(self, i, price):
        ind = self.strategy._indicators
        atr = ind["atr"][i] if i < len(ind["atr"]) else 0

        side = TradeSide.LONG if self.entry_signals[i] == Signal.BUY else TradeSide.SHORT
        qty = self.risk.position_size(self.capital, price, atr)
        if qty <= 0:
            return

        t = Trade(
            entry_time=self._ts(i), side=side, entry_price=price,
            quantity=qty, stop_loss=self.risk.stop(price, side),
            take_profit=self.risk.target(price, side), entry_atr=atr,
        )
        self.current = t
        self.best_price = price

    def _close(self, i, price, reason):
        t = self.current
        pnl_pct = ((price - t.entry_price) / t.entry_price if t.side == TradeSide.LONG
                   else (t.entry_price - price) / t.entry_price)
        pnl_pct *= self.leverage
        pnl_abs = t.quantity * t.entry_price * pnl_pct

        t.exit_time = self._ts(i)
        t.exit_price = price
        t.pnl_pct = pnl_pct * 100
        t.pnl_abs = pnl_abs
        t.exit_reason = reason

        self.capital += pnl_abs
        self.trades.append(t)
        self.current = None
        self.best_price = 0.0

    def _equity(self, price):
        if self.current is None:
            return self.capital
        t = self.current
        upnl = ((price - t.entry_price) / t.entry_price if t.side == TradeSide.LONG
                else (t.entry_price - price) / t.entry_price)
        return self.capital + t.quantity * t.entry_price * upnl * self.leverage

    def _ts(self, i):
        ts = self.candles[i].get("timestamp", 0)
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    def _report(self):
        n = len(self.trades)
        wins = [t for t in self.trades if t.pnl_pct > 0]
        losses = [t for t in self.trades if t.pnl_pct <= 0]
        wr = len(wins) / n * 100 if n else 0
        total_ret = (self.capital - self.initial_capital) / self.initial_capital * 100

        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        total_win = sum(t.pnl_pct for t in wins)
        total_loss = abs(sum(t.pnl_pct for t in losses))
        pf = total_win / total_loss if total_loss > 0 else float("inf")

        returns = [t.pnl_pct for t in self.trades]
        sharpe = 0.0
        if len(returns) > 1:
            m = sum(returns) / len(returns)
            v = sum((r-m)**2 for r in returns) / (len(returns)-1)
            sharpe = (m / math.sqrt(v) * math.sqrt(n)) if v > 0 else 0

        reasons = {}
        for t in self.trades:
            r = t.exit_reason.value if t.exit_reason else "?"
            reasons[r] = reasons.get(r, 0) + 1

        return {
            "initial_capital": self.initial_capital,
            "final_capital": round(self.capital, 2),
            "total_return": round(total_ret, 2),
            "total_trades": n,
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(wr, 1),
            "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
            "profit_factor": round(pf, 2),
            "sharpe": round(sharpe, 2),
            "max_dd": round(self.max_dd * 100, 2),
            "exit_reasons": reasons,
            "trades": self.trades,
        }


# ============================================================
# CLI 回测入口
# ============================================================

def print_report(stats):
    print("\n" + "=" * 60)
    print("  BTC TrendFlow — 回测报告")
    print("=" * 60)
    print(f"  初始资金:    ${stats['initial_capital']:>14,.2f}")
    print(f"  最终资金:    ${stats['final_capital']:>14,.2f}")
    print(f"  总收益率:    {stats['total_return']:>13.2f}%")
    print(f"  交易次数:    {stats['total_trades']:>14}")
    print(f"  胜率:        {stats['win_rate']:>13.1f}%")
    print(f"  平均盈利:    {stats['avg_win']:>13.2f}%")
    print(f"  平均亏损:    {stats['avg_loss']:>13.2f}%")
    print(f"  盈亏比:      {stats['profit_factor']:>14.2f}")
    print(f"  夏普比率:    {stats['sharpe']:>14.2f}")
    print(f"  最大回撤:    {stats['max_dd']:>13.2f}%")
    print(f"  平仓分布:    {stats['exit_reasons']}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse, sqlite3
    from collections import defaultdict

    parser = argparse.ArgumentParser(description="BTC TrendFlow 回测")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.01)
    parser.add_argument("--sl", type=float, default=0.05)
    parser.add_argument("--tp", type=float, default=0.25)
    parser.add_argument("--ts", type=float, default=0.06)
    parser.add_argument("--leverage", type=float, default=3.0)
    parser.add_argument("--timeframe", default="4h")
    args = parser.parse_args()

    DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ohlcv_cache/ohlcv_cache.db")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv_cache "
        "WHERE symbol='BTC/USDT' AND timeframe=? ORDER BY timestamp ASC",
        (args.timeframe,)
    )
    candles = [{"timestamp": r["timestamp"]//1000, "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"]} for r in cur.fetchall()]
    conn.close()

    risk = RiskCalculator(risk_pct=args.risk, sl_pct=args.sl, tp_pct=args.tp, ts_pct=args.ts)
    strat = BTCTrendFlowStrategy()
    bt = TrendFlowBacktest(candles, args.capital, risk, strat, args.leverage)
    stats = bt.run()
    print_report(stats)
