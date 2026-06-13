#!/usr/bin/env python3
"""
EVR策略 v2: 去掉EMA退出信号，仅靠SL/TP退出
减少过早退出导致的频繁止损
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import List, Dict, Optional
from strategies import Strategy, StrategyConfig, compute_rsi


class EVRStrategyV2(Strategy):
    """
    入场：EMA多头排列 + RSI中性 + 成交量放大
    出场：仅SL/TP（无EMA交叉退出，避免趋势中被震出）
    """

    def __init__(
        self,
        config: Optional[StrategyConfig] = None,
        fast_period: int = 9,
        mid_period: int = 21,
        slow_period: int = 55,
        rsi_period: int = 14,
        rsi_lower: float = 35.0,
        rsi_upper: float = 75.0,
        vol_ma_period: int = 20,
        vol_multiplier: float = 1.1,
    ):
        super().__init__(config)
        self.fast = fast_period
        self.mid = mid_period
        self.slow = slow_period
        self.rsi_period = rsi_period
        self.rsi_lower = rsi_lower
        self.rsi_upper = rsi_upper
        self.vol_ma_period = vol_ma_period
        self.vol_mult = vol_multiplier

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [c['close'] for c in candles]
        volumes = [c['volume'] for c in candles]

        ema9 = self._ema(closes, self.fast)
        ema21 = self._ema(closes, self.mid)
        ema55 = self._ema(closes, self.slow)
        rsi = compute_rsi(closes, self.rsi_period)
        vol_ma = self._sma(volumes, self.vol_ma_period)

        self._ema9 = ema9
        self._ema21 = ema21
        self._ema55 = ema55
        self._rsi = rsi
        self._vol_ma = vol_ma
        self._volumes = volumes

        return {'ema9': ema9, 'ema21': ema21, 'ema55': ema55, 'rsi': rsi, 'vol_ma': vol_ma}

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        n = len(candles)
        signals = [0] * n
        for i in range(self.slow, n):
            bull = self._ema9[i] > self._ema21[i] > self._ema55[i]
            bear = self._ema9[i] < self._ema21[i] < self._ema55[i]
            rsi_ok = self.rsi_lower <= self._rsi[i] <= self.rsi_upper if self._rsi[i] else False
            vol_ok = self._volumes[i] > self._vol_ma[i] * self.vol_mult if self._vol_ma[i] else False

            if bull and rsi_ok and vol_ok:
                signals[i] = 1
            elif bear and rsi_ok and vol_ok:
                signals[i] = -1
        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        # 无EMA退出信号，全部由SL/TP处理
        return [0] * len(candles)

    def _ema(self, data: List[float], period: int) -> List[float]:
        result = []
        k = 2.0 / (period + 1)
        for i, val in enumerate(data):
            if i == 0:
                result.append(val)
            else:
                result.append(val * k + result[-1] * (1 - k))
        return result

    def _sma(self, data: List[float], period: int) -> List[float]:
        result = []
        for i in range(len(data)):
            if i < period - 1:
                result.append(data[i])
            else:
                result.append(sum(data[i - period + 1:i + 1]) / period)
        return result


def run():
    from datetime import datetime, timezone
    import json
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from history_cache import init_cache_db, get_ohlcv
    from backtest import BacktestEngine

    init_cache_db()
    os.makedirs("backtest_results", exist_ok=True)

    param_grid = [
        # (sl, tp, rsi_l, rsi_u, vol_mult, label)
        (0.05, 0.10, 35, 75, 1.1, '基准(35-75)'),
        (0.03, 0.06, 35, 75, 1.1, '紧SL/TP'),
        (0.04, 0.08, 30, 80, 1.0, '宽RSI+紧SL'),
        (0.05, 0.15, 40, 70, 1.2, '宽TP15%'),
        (0.02, 0.04, 35, 75, 1.0, '超紧2/4'),
        (0.03, 0.09, 35, 75, 1.1, '中3/9'),
    ]

    for tf in ["2h", "4h"]:
        candles = get_ohlcv("BTC/USDT", tf, limit=20000)
        if not candles:
            continue
        print(f"\n{'='*60}\n  EVR v2 参数优化 | {tf} | {len(candles)}条\n{'='*60}")

        for sl, tp, rsi_l, rsi_u, vol_m, label in param_grid:
            strat = EVRStrategyV2(
                rsi_lower=rsi_l, rsi_upper=rsi_u, vol_multiplier=vol_m,
                config=StrategyConfig(stop_loss=sl, take_profit=tp,
                                     trade_direction='both',
                                     commission_pct=0.001, slippage_pct=0.0005)
            )
            engine = BacktestEngine(strategy=strat, initial_capital=10000.0, trade_direction='both')
            engine.candles = candles
            result = engine.run()

            print(f"  {label:20s}: {result.total_return_pct:+.2f}% SR={result.sharpe_ratio:+.2f} "
                  f"DD={result.max_drawdown_pct:.1f}% WR={result.win_rate_pct:.1f}% ({result.total_trades}笔)")

    print(f"\n✅ 完成")


if __name__ == "__main__":
    run()