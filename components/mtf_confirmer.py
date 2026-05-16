"""
mtf_confirmer.py — 多时间框架信号确认
========================================

减少假信号：主周期信号需要更高周期确认后才执行。

规则：
  - 4h BUY  → 需 1d close > 1d SMA(20) 确认（日线趋势向上）
  - 4h SELL → 需 1d close < 1d SMA(20) 确认
  - 1h BUY  → 需 4h RSI > 50 确认（中期不弱）
  - 1h SELL → 需 4h RSI < 50 确认

返回：
  - confirmed: bool
  - strength: 0.0~1.0（0=强烈反对，0.5=中性，1=强烈确认）
  - reason: 说明文字
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class MultiTimeframeConfirmer:
    """多周期信号确认器"""

    # 时间框架层级
    CONFIRMATION_CHAIN = {
        "1m":  [],
        "5m":  ["1m"],
        "15m": ["5m"],
        "1h":  ["4h"],
        "4h":  ["1d"],
        "1d":  [],
        "1w":  [],
    }

    def __init__(self, symbol: str = "", data_provider=None):
        self.symbol = symbol
        self._fetch = data_provider or self._default_fetch

    def _default_fetch(self, symbol: str, timeframe: str, limit: int = 100):
        try:
            from crypto_api import get_ohlcv
            candles = get_ohlcv(symbol.split("/")[0], timeframe, limit=limit)
            return candles
        except Exception:
            return None

    def confirm(self, signal: int, primary_tf: str, primary_candles: List[Dict]) -> Dict:
        """
        确认主周期信号

        Args:
            signal: Signal.BUY(1) / Signal.SELL(-1) / Signal.HOLD(0)
            primary_tf: 主时间框架 ("4h", "1h", etc.)
            primary_candles: 主周期 K线

        Returns:
            {"confirmed": bool, "strength": float, "reason": str}
        """
        if signal == 0:  # HOLD
            return {"confirmed": True, "strength": 0.5, "reason": "无信号，无需确认"}

        confirm_tfs = self.CONFIRMATION_CHAIN.get(primary_tf, [])
        if not confirm_tfs:
            return {"confirmed": True, "strength": 0.7, "reason": f"{primary_tf} 无更高周期确认链"}

        is_buy = signal == 1
        total_strength = 0.0
        num_tfs = len(confirm_tfs)
        reasons = []

        for ct in confirm_tfs:
            result = self._check_tf(ct, is_buy, primary_candles)
            total_strength += result["strength"]
            reasons.append(f"{ct}:{result['reason']}")

        avg_strength = total_strength / num_tfs if num_tfs > 0 else 0.5
        # 确认阈值：平均 strength > 0.4 即通过
        confirmed = avg_strength >= 0.4

        return {
            "confirmed": confirmed,
            "strength": round(avg_strength, 2),
            "reason": " | ".join(reasons),
            "confirm_tfs": confirm_tfs,
        }

    def _check_tf(self, tf: str, is_buy: bool, primary_candles: List[Dict]) -> Dict:
        """检查单个高级周期的趋势方向"""
        candles = self._fetch(self.symbol, tf, limit=100)
        if not candles or len(candles) < 20:
            return {"strength": 0.5, "reason": "数据不足→中性"}

        closes = [c["close"] for c in candles]
        current = closes[-1]

        # 计算 SMA20
        sma20 = sum(closes[-20:]) / 20

        # 计算 RSI(14)
        rsi = self._compute_rsi(closes, 14)

        if tf == "1d":
            # 日线：价格 vs SMA(20)
            if is_buy:
                if current > sma20:
                    ratio = (current - sma20) / sma20
                    strength = min(1.0, 0.5 + ratio * 5)
                    return {"strength": strength, "reason": f"日线多头(价>{sma20:.0f})"}
                else:
                    return {"strength": 0.25, "reason": f"日线空头(价<{sma20:.0f})→弱否决"}
            else:
                if current < sma20:
                    ratio = (sma20 - current) / sma20
                    strength = min(1.0, 0.5 + ratio * 5)
                    return {"strength": strength, "reason": f"日线空头(价<{sma20:.0f})"}
                else:
                    return {"strength": 0.25, "reason": f"日线多头(价>{sma20:.0f})→弱否决"}

        if tf == "4h":
            if rsi > 50:
                strength = min(1.0, 0.5 + (rsi - 50) / 30)
                direction = "偏多" if is_buy else "偏多(逆信号)"
                return {"strength": strength if is_buy else 0.3, "reason": f"4h{ direction}(RSI={rsi:.0f})"}
            elif rsi < 50:
                strength = min(1.0, 0.5 + (50 - rsi) / 30)
                direction = "偏空" if not is_buy else "偏空(逆信号)"
                return {"strength": strength if not is_buy else 0.3, "reason": f"4h{ direction}(RSI={rsi:.0f})"}
            return {"strength": 0.5, "reason": f"4h中性(RSI={rsi:.0f})"}

        if tf == "1m" or tf == "5m":
            # 低周期：方向一致性
            if is_buy and current > sma20:
                return {"strength": 0.6, "reason": f"{tf}同向(价>SMA20)"}
            elif not is_buy and current < sma20:
                return {"strength": 0.6, "reason": f"{tf}同向(价<SMA20)"}
            return {"strength": 0.4, "reason": f"{tf}反向→弱"}

        return {"strength": 0.5, "reason": f"{tf}未知周期→中性"}

    @staticmethod
    def _compute_rsi(prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(prices)):
            d = prices[i] - prices[i-1]
            gains.append(d if d > 0 else 0)
            losses.append(-d if d < 0 else 0)
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
