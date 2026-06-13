#!/usr/bin/env python3
"""
BNB 专用策略
- BNB_4H_TrendMR: 4h 趋势过滤 + 均值回归（做多为主）
- BNB_2H_VolBreak: 2h 波动突破 + ADX 趋势过滤（多空双向）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json, math
from typing import List, Dict, Optional
from strategies import Strategy, StrategyConfig, compute_rsi


class BNB4HTrendMR(Strategy):
    """
    BNB 4h 专用：趋势跟随 + 均值回归
    - 长期趋势过滤：EMA50 > EMA200 才做多
    - 均值回归入场：RSI(5) < 25 超卖反弹
    - 成交量确认：> MA(vol,20) * 1.3
    - 止损 8%，止盈 18%
    """

    def __init__(self, config: Optional[StrategyConfig] = None,
                 ema_fast=50, ema_slow=200, rsi_period=5,
                 rsi_oversold=25.0, rsi_overbought=75.0,
                 vol_ma_period=20, vol_mult=1.3,
                 stop_loss=0.08, take_profit=0.18):
        super().__init__(config or StrategyConfig(
            symbol="BNB/USDT", timeframe="4h",
            stop_loss=stop_loss, take_profit=take_profit, trade_direction="long"))
        self.ema_fast = ema_fast; self.ema_slow = ema_slow
        self.rsi_period = rsi_period; self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought; self.vol_ma_period = vol_ma_period
        self.vol_mult = vol_mult

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [c['close'] for c in candles]
        volumes = [c['volume'] for c in candles]
        return {
            "ema50": self.EMA(closes, self.ema_fast),
            "ema200": self.EMA(closes, self.ema_slow),
            "rsi": compute_rsi(closes, self.rsi_period),
            "vol_ma": self.SMA(volumes, self.vol_ma_period),
        }

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        n = len(candles)
        min_bars = max(self.ema_slow, self.rsi_period, self.vol_ma_period) + 5
        if n < min_bars: return [0] * n
        closes = [c['close'] for c in candles]
        volumes = [c['volume'] for c in candles]
        ema50 = self.EMA(closes, self.ema_fast)
        ema200 = self.EMA(closes, self.ema_slow)
        rsi = compute_rsi(closes, self.rsi_period)
        vol_ma = self.SMA(volumes, self.vol_ma_period)
        signals = [0] * n
        for i in range(self.ema_slow, n):
            if ema50[i] > ema200[i] and rsi[i] < self.rsi_oversold and volumes[i] > vol_ma[i] * self.vol_mult:
                signals[i] = 1
        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        n = len(candles)
        if n < self.rsi_period + 5: return [0] * n
        closes = [c['close'] for c in candles]
        rsi = compute_rsi(closes, self.rsi_period)
        signals = [0] * n
        for i in range(self.rsi_period, n):
            if rsi[i] > self.rsi_overbought:
                signals[i] = -1
        return signals


class BNB2HVolBreak(Strategy):
    """
    BNB 2h 专用：波动突破 + ADX 趋势过滤
    - ADX(14) > 25 允许交易
    - Donchian 突破入场（period=12）
    - 成交量确认 > MA(vol,20) * 1.2
    - 止损 6%，止盈 15%，多空双向
    """

    def __init__(self, config: Optional[StrategyConfig] = None,
                 adx_period=14, adx_threshold=25.0,
                 breakout_period=12, atr_period=14, atr_stop_mult=2.0,
                 vol_ma_period=20, vol_mult=1.2,
                 stop_loss=0.06, take_profit=0.15):
        super().__init__(config or StrategyConfig(
            symbol="BNB/USDT", timeframe="2h",
            stop_loss=stop_loss, take_profit=take_profit, trade_direction="both"))
        self.adx_period = adx_period; self.adx_threshold = adx_threshold
        self.breakout_period = breakout_period; self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult; self.vol_ma_period = vol_ma_period
        self.vol_mult = vol_mult

    def _calc_adx(self, h: List[float], l: List[float], c: List[float], p: int) -> List[float]:
        n = len(c)
        tr, pdi_arr, ndi_arr = [0.]*n, [0.]*n, [0.]*n
        for i in range(1, n):
            tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
            up = h[i]-h[i-1]; dn = l[i-1]-l[i]
            pdi_arr[i] = up if up>dn and up>0 else 0
            ndi_arr[i] = dn if dn>up and dn>0 else 0
        tr_ma = self.SMA(tr, p); pdi_ma = self.SMA(pdi_arr, p); ndi_ma = self.SMA(ndi_arr, p)
        dx = [0.]*n
        for i in range(p*2, n):
            if tr_ma[i]==0: continue
            pdi=pdi_ma[i]/tr_ma[i]*100; ndi=ndi_ma[i]/tr_ma[i]*100
            dx[i] = abs(pdi-ndi)/(pdi+ndi)*100 if(pdi+ndi)>0 else 0
        adx = [0.]*n
        adx[p*2-1] = sum(dx[p*2-p:p*2])/p
        for i in range(p*2, n):
            adx[i] = (adx[i-1]*(p-1)+dx[i])/p
        return adx

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [c['close'] for c in candles]; highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]; volumes = [c['volume'] for c in candles]
        tr = [0.]*len(candles)
        for i in range(1, len(candles)):
            tr[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        return {
            "adx": self._calc_adx(highs, lows, closes, self.adx_period),
            "atr": self.SMA(tr, self.atr_period),
            "vol_ma": self.SMA(volumes, self.vol_ma_period),
        }

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        n = len(candles)
        min_bars = max(self.adx_period*2, self.breakout_period, self.vol_ma_period)+5
        if n < min_bars: return [0]*n
        closes = [c['close'] for c in candles]; highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]; volumes = [c['volume'] for c in candles]
        adx = self._calc_adx(highs, lows, closes, self.adx_period)
        vol_ma = self.SMA(volumes, self.vol_ma_period)
        signals = [0]*n
        for i in range(min_bars, n):
            if adx[i] < self.adx_threshold: continue
            if not volumes[i] > vol_ma[i]*self.vol_mult: continue
            hh = max(highs[i-self.breakout_period:i])
            ll = min(lows[i-self.breakout_period:i])
            if closes[i] > hh: signals[i] = 1
            elif closes[i] < ll: signals[i] = -1
        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        n = len(candles)
        if n < self.breakout_period+5: return [0]*n
        closes = [c['close'] for c in candles]
        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]
        signals = [0]*n
        for i in range(self.breakout_period+1, n):
            hh = max(highs[i-self.breakout_period:i])
            ll = min(lows[i-self.breakout_period:i])
            if closes[i] > hh: signals[i] = 1
            elif closes[i] < ll: signals[i] = -1
        return signals
