#!/usr/bin/env python3
"""
EMA-Volume-RSI 复合趋势策略 (EVR Strategy)
专业数据分析视角设计，结合趋势确认 + 动量验证 + 成交量过滤

设计逻辑：
  1. 三均线多头排列确认趋势方向（EMA9 > EMA21 > EMA55 → 多）
  2. RSI(14) 处于中性区间(40~70)时确认动量，避免追顶/底
  3. 成交量高于20日均量1.2倍时确认信号，避免假突破
  4. 止损5% / 止盈10%（配合框架固定止损）

回测参数：
  - commission: 0.1%（现货taker）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import List, Dict, Optional
from strategies import Strategy, StrategyConfig, compute_rsi


class EVRStrategy(Strategy):
    """
    EMA-Volume-RSI 复合趋势策略

    入场条件（同时满足）：
      - EMA9 > EMA21 > EMA55（多头排列）→ 做多
      - EMA9 < EMA21 < EMA55（空头排列）→ 做空
      - RSI(14) 在 40~70 区间（过滤极端行情）
      - 成交量 > MA20(volume) × 1.2

    出场条件：
      - 止损: 5%
      - 止盈: 10%
      - 反转: 反向均线交叉
    """

    def __init__(
        self,
        config: Optional[StrategyConfig] = None,
        fast_period: int = 9,
        mid_period: int = 21,
        slow_period: int = 55,
        rsi_period: int = 14,
        rsi_lower: float = 40.0,
        rsi_upper: float = 70.0,
        vol_ma_period: int = 20,
        vol_multiplier: float = 1.2,
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
        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]
        volumes = [c['volume'] for c in candles]

        # EMA
        ema9 = self._ema(closes, self.fast)
        ema21 = self._ema(closes, self.mid)
        ema55 = self._ema(closes, self.slow)

        # RSI
        rsi = compute_rsi(closes, self.rsi_period)

        # 成交量均线
        vol_ma = self._sma(volumes, self.vol_ma_period)

        # 缓存到实例变量（供 entry/exit 方法使用）
        self._ema9 = ema9
        self._ema21 = ema21
        self._ema55 = ema55
        self._rsi = rsi
        self._vol_ma = vol_ma
        self._closes = closes
        self._volumes = volumes

        return {
            'ema9': ema9,
            'ema21': ema21,
            'ema55': ema55,
            'rsi': rsi,
            'vol_ma': vol_ma,
        }

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        """
        返回: 1=做多入场, -1=做空入场, 0=无信号
        """
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
        """
        反向均线交叉作为出场信号
        """
        n = len(candles)
        signals = [0] * n

        for i in range(self.slow + 1, n):
            prev = i - 1
            if self._ema9[prev] > self._ema21[prev] > self._ema55[prev] and not (self._ema9[i] > self._ema21[i] > self._ema55[i]):
                signals[i] = 1
            elif self._ema9[prev] < self._ema21[prev] < self._ema55[prev] and not (self._ema9[i] < self._ema21[i] < self._ema55[i]):
                signals[i] = -1

        return signals

    # ---- 辅助指标计算 ----

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


# ============================================================
# 回测运行器
# ============================================================

def run_backtest():
    import json
    from datetime import datetime, timezone
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from history_cache import init_cache_db, get_ohlcv
    from backtest import BacktestEngine

    init_cache_db()

    results_dir = "backtest_results"
    os.makedirs(results_dir, exist_ok=True)

    for tf in ["2h", "4h"]:
        print(f"\n{'='*70}")
        print(f"  EVR策略 回测 | BTC/USDT {tf}")
        print(f"{'='*70}")

        candles = get_ohlcv("BTC/USDT", tf, limit=20000)
        if not candles:
            print(f"  ⚠️ 无数据: {tf}")
            continue

        print(f"  数据: {len(candles)} 条, "
              f"{datetime.fromtimestamp(candles[0]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d')} "
              f"→ {datetime.fromtimestamp(candles[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")

        for direction in ["long", "short", "both"]:
            strat = EVRStrategy(
                config=StrategyConfig(
                    stop_loss=0.05,
                    take_profit=0.10,
                    trade_direction=direction,
                    commission_pct=0.001,
                    slippage_pct=0.0005,
                )
            )

            engine = BacktestEngine(
                strategy=strat,
                initial_capital=10000.0,
                trade_direction=direction,
            )
            engine.candles = candles
            engine.entry_signal = []
            engine.exit_signal = []
            result = engine.run()

            total_ret = result.total_return_pct
            sharpe = getattr(result, 'sharpe_ratio', 0) or 0
            max_dd = getattr(result, 'max_drawdown_pct', 0) or 0
            trades = getattr(result, 'total_trades', 0) or 0
            win_rate = getattr(result, 'win_rate_pct', 0) or 0
            winning = getattr(result, 'winning_trades', 0) or 0
            losing = getattr(result, 'losing_trades', 0) or 0

            print(f"\n  [{direction.upper()}]")
            print(f"    收益率: {total_ret:+.2f}%  夏普: {sharpe:+.2f}  DD: {max_dd:.1f}%")
            print(f"    交易: {trades} 笔 (胜{winning}/负{losing})  胜率: {win_rate:.1f}%")

            # 保存
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_file = f"{results_dir}/evr_{tf}_{direction}_{ts}.json"
            with open(out_file, 'w') as f:
                json.dump({
                    'strategy': 'EVR',
                    'timeframe': tf,
                    'direction': direction,
                    'total_return_pct': total_ret,
                    'sharpe_ratio': sharpe,
                    'max_drawdown_pct': max_dd,
                    'total_trades': trades,
                    'win_rate': win_rate,
                    'winning_trades': winning,
                    'losing_trades': losing,
                }, f, indent=2)

    print(f"\n✅ 回测完成")


if __name__ == "__main__":
    run_backtest()