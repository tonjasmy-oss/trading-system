"""
市场状态识别器 - Market Regime Detector
参考 QuantDinger 的 app/services/experiment/regime.py

识别当前市场处于什么状态（趋势/震荡/高波动），
为策略选择和参数调整提供依据。
"""

import sys
import os
import math
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RegimeResult:
    """市场状态识别结果"""
    regime: str            # "trending_up", "trending_down", "ranging", "high_volatility"
    confidence: float      # 0-1
    features: Dict[str, float] = field(default_factory=dict)
    recommended_strategies: List[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "confidence": self.confidence,
            "features": self.features,
            "recommended_strategies": self.recommended_strategies,
            "description": self.description,
        }


class MarketRegimeDetector:
    """
    规则型市场状态识别

    基于以下特征：
      - ADX (趋势强度)
      - 波动率（ATR/收盘价）
      - 价格相对 SMA 位置
      - 连续涨跌天数
    """

    def __init__(self):
        pass

    def detect(self, ohlcv: List[dict]) -> RegimeResult:
        """
        识别市场状态

        Args:
            ohlcv: OHLCV 数据列表，每项含 open/high/low/close

        Returns:
            RegimeResult
        """
        if len(ohlcv) < 30:
            return RegimeResult(
                regime="insufficient_data",
                confidence=0.0,
                description="数据不足（需要至少 30 根 K 线）",
            )

        closes = [c["close"] for c in ohlcv[-50:]]
        highs = [c["high"] for c in ohlcv[-50:]]
        lows = [c["low"] for c in ohlcv[-50:]]

        # 1. ADX (简化版 - 基于价格方向性)
        adx = self._calc_simple_adx(highs, lows, closes)

        # 2. 波动率
        volatility = self._calc_volatility(closes)

        # 3. SMA 位置
        sma20 = sum(closes[-20:]) / 20
        sma_position = (closes[-1] - sma20) / sma20

        # 4. 近期趋势
        recent_trend = self._calc_recent_trend(closes)

        features = {
            "adx": round(adx, 2),
            "volatility_pct": round(volatility * 100, 2),
            "sma_position_pct": round(sma_position * 100, 2),
            "recent_trend_pct": round(recent_trend * 100, 2),
        }

        # 状态判断
        if adx > 25 and volatility > 0.03:
            if sma_position > 0.02:
                regime = "trending_up"
                desc = "强势上升趋势"
                strategies = ["ATRSTOP", "RSIStrategy", "MACDStrategy"]
            elif sma_position < -0.02:
                regime = "trending_down"
                desc = "明显下跌趋势"
                strategies = ["ATRSTOP"]  # 下跌趋势减少策略
            else:
                regime = "high_volatility"
                desc = "高波动震荡"
                strategies = ["BollingerBandsStrategy", "RSIStrategy"]
        elif adx < 20:
            regime = "ranging"
            desc = "横盘震荡"
            strategies = ["BollingerBandsStrategy", "RSIStrategy"]
        else:
            regime = "trending_up" if sma_position > 0 else "trending_down"
            desc = "温和趋势"
            strategies = ["ATRSTOP", "RSIStrategy", "MACDStrategy"]

        confidence = min(adx / 30, 1.0) if adx > 20 else 0.6

        return RegimeResult(
            regime=regime,
            confidence=round(confidence, 2),
            features=features,
            recommended_strategies=strategies,
            description=desc,
        )

    @staticmethod
    def _calc_simple_adx(highs, lows, closes, period=14):
        """简化 ADX 计算"""
        if len(closes) < period + 1:
            return 0
        tr_list = []
        for i in range(1, min(len(closes), period + 14)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_list.append(tr)
        atr = sum(tr_list[-period:]) / period

        # 方向移动
        up_move = []
        down_move = []
        for i in range(1, len(closes)):
            up = closes[i] - closes[i - 1]
            down = closes[i - 1] - closes[i]
            up_move.append(up if up > 0 else 0)
            down_move.append(down if down > 0 else 0)

        if atr == 0:
            return 0

        plus_di = (sum(up_move[-period:]) / period) / atr * 100
        minus_di = (sum(down_move[-period:]) / period) / atr * 100
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
        return dx

    @staticmethod
    def _calc_volatility(closes, period=20):
        """计算波动率"""
        if len(closes) < period:
            return 0
        returns = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                returns.append((closes[i] - closes[i - 1]) / closes[i - 1])
        if not returns:
            return 0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance)

    @staticmethod
    def _calc_recent_trend(closes, period=10):
        """计算近期趋势"""
        if len(closes) < period:
            return 0
        return (closes[-1] - closes[-period]) / closes[-period]
