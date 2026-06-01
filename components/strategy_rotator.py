"""
strategy_rotator.py — 市场状态感知策略轮动
============================================

根据 MarketRegime 检测的市场状态，自动选择最优策略：

  状态                 → 推荐策略
  ──────────────────────────────────────────
  uptrend + high_vol   → DONCHIAN (通道突破最强)
  uptrend + high_vol   → ATRSTOP  (趋势跟随+动态止损)
  uptrend + low_vol    → SMA      (简单均线趋势)
  downtrend + high_vol → DONCHIAN (通道向下突破做空)
  downtrend + high_vol → RSI      (超卖反弹)
  downtrend + low_vol  → RSI      (超卖做多)
  ranging  + high_vol  → ATRSTOP  (震荡+高波动，动态止损防假突破)
  ranging  + medium_vol → BOLLINGER(布林带均值回归)
  ranging  + low_vol   → KDJ      (摆动交易)
  unknown              → MACD     (默认稳健)
"""

import os
import logging
logger = logging.getLogger(__name__)

REGIME_STRATEGY_MAP = {
    ("uptrend", "high"):    ("DONCHIAN", "上升趋势+高波动，通道突破捕捉主升浪", {"channel_period": 20, "trend_ema_period": 30}),
    ("uptrend", "medium"):  ("DONCHIAN", "上升趋势+中等波动，通道突破策略", {"channel_period": 20, "trend_ema_period": 40}),
    ("uptrend", "low"):     ("SMA",      "上升趋势+低波动，均线趋势", {"fast_period": 10, "slow_period": 30}),
    ("downtrend", "high"):  ("DONCHIAN", "下降趋势+高波动，通道向下突破做空", {"channel_period": 20, "trend_ema_period": 30}),
    ("downtrend", "medium"):("RSI",      "下降趋势+中等波动，超卖做多", {"rsi_period": 14, "oversold": 28, "overbought": 55}),
    ("downtrend", "low"):   ("RSI",      "下降趋势+低波动，超卖做多", {"rsi_period": 14, "oversold": 28, "overbought": 55}),
    ("ranging", "high"):    ("ATRSTOP",  "震荡市+高波动，ATR动态止损(回测最优)", {"ema_period": 20, "atr_multiplier": 2.0}),
    ("ranging", "medium"):  ("BOLLINGER","震荡市+中等波动，布林带均值回归", {"period": 20, "std_dev": 2.0}),
    ("ranging", "low"):     ("KDJ",      "震荡市+低波动，KDJ摆动交易", {}),
}
FALLBACK_STRATEGY = ("MACD", "市场状态未知，回退到MACD", {})

# 策略适配度评分表（基于 market_regime.STRATEGY_RECOMMENDATIONS 同步）
_STRATEGY_FIT_SCORE = {
    ("DONCHIAN", "uptrend", "high"): 95,
    ("DONCHIAN", "uptrend", "medium"): 92,
    ("DONCHIAN", "uptrend", "low"): 70,
    ("DONCHIAN", "downtrend", "high"): 75,
    ("DONCHIAN", "downtrend", "medium"): 72,
    ("DONCHIAN", "ranging", "high"): 40,
    ("DONCHIAN", "ranging", "medium"): 30,
    ("DONCHIAN", "ranging", "low"): 25,
    ("BOLLINGER", "ranging", "high"): 92,
    ("BOLLINGER", "ranging", "medium"): 90,
    ("BOLLINGER", "ranging", "low"): 80,
    ("BOLLINGER", "downtrend", "high"): 70,
    ("RSI", "downtrend", "high"): 88,
    ("RSI", "downtrend", "medium"): 85,
    ("RSI", "downtrend", "low"): 80,
    ("RSI", "ranging", "medium"): 75,
    ("KDJ", "ranging", "low"): 88,
    ("KDJ", "ranging", "medium"): 82,
    ("KDJ", "downtrend", "low"): 72,
    ("SMA", "uptrend", "low"): 90,
    ("SMA", "uptrend", "medium"): 85,
    ("ATRSTOP", "uptrend", "high"): 90,
    ("ATRSTOP", "uptrend", "medium"): 82,
    ("ATRSTOP", "ranging", "high"): 78,
    ("MACD", "unknown", "unknown"): 60,
}


class StrategyRotator:
    """市场状态感知策略轮动器"""

    def __init__(self, symbol: str = "", config_map: dict = None, stability: int = 2):
        self.symbol = symbol
        self.config_map = config_map or {}
        self._last_strategy: str = ""
        self._last_regime_key: tuple = ("", "")
        self._stability_counter: int = 0
        self._min_stability: int = stability

    def pick(self, regime: dict) -> dict:
        trend = (regime.get("trend") or "unknown").lower()
        vol = (regime.get("volatility") or "unknown").lower()
        confidence = regime.get("confidence", 0.5)
        key = (trend, vol)
        strategy_name, reason, kwargs = REGIME_STRATEGY_MAP.get(key, FALLBACK_STRATEGY)

        if key == self._last_regime_key:
            self._stability_counter += 1
        else:
            self._stability_counter = 0
        self._last_regime_key = key

        if self._stability_counter < self._min_stability and self._last_strategy:
            reason = f"待确认: {reason} ({self._stability_counter+1}/{self._min_stability})"
            strategy_name = self._last_strategy
            kwargs = {}

        if self._stability_counter >= self._min_stability:
            self._last_strategy = strategy_name

        symbol_overrides = self.config_map.get(self.symbol, {})
        strategy_overrides = symbol_overrides.get(strategy_name, {})
        final_kwargs = {**kwargs, **strategy_overrides}

        logger.info(f"[策略轮动] {self.symbol} {trend}+{vol} → {strategy_name} "
                    f"({reason}) 置信度={confidence:.2f}")
        return {
            "strategy": strategy_name,
            "reason": reason,
            "kwargs": final_kwargs,
            "confidence": confidence,
            "regime_trend": trend,
            "regime_volatility": vol,
        }

    def get_current_strategy(self) -> str:
        return self._last_strategy or "MACD"

    def reset(self):
        """重置轮动状态（切换模式时调用）"""
        self._last_strategy = ""
        self._last_regime_key = ("", "")
        self._stability_counter = 0

    def score_fit(self, strategy_name: str, trend: str, volatility: str) -> int:
        """
        评估指定策略对当前市场的适配度（0-100）。
        优先查本地评分表，然后回退到 market_regime.recommend_strategies。
        """
        key = (strategy_name.upper(), trend.lower(), volatility.lower())
        if key in _STRATEGY_FIT_SCORE:
            return _STRATEGY_FIT_SCORE[key]
        # 回退到 market_regime 的推荐引擎
        try:
            from components.market_regime import recommend_strategies
            recs = recommend_strategies(trend, volatility, top_n=10)
            for rec in recs:
                if rec["strategy"].upper() == strategy_name.upper():
                    return rec["fit_score"]
        except ImportError:
            pass
        return 30  # 默认低适配度

    def is_current_strategy_appropriate(self, regime: dict, threshold: int = 50) -> bool:
        """当前策略是否适配当前市场（fit_score >= threshold）"""
        if not self._last_strategy:
            return True
        trend = (regime.get("trend") or "unknown").lower()
        vol = (regime.get("volatility") or "unknown").lower()
        return self.score_fit(self._last_strategy, trend, vol) >= threshold

    def get_better_strategies(self, current_strategy: str, regime: dict, top_n: int = 3) -> list:
        """
        获取比当前策略更适配市场的推荐策略。
        返回适配度超过当前策略的推荐列表。
        """
        trend = (regime.get("trend") or "unknown").lower()
        vol = (regime.get("volatility") or "unknown").lower()
        current_score = self.score_fit(current_strategy, trend, vol)

        try:
            from components.market_regime import recommend_strategies
            recs = recommend_strategies(trend, vol, top_n=10)
        except ImportError:
            return []

        better = []
        for rec in recs:
            if rec["fit_score"] > current_score and rec["strategy"].upper() != current_strategy.upper():
                better.append(rec)
        return better[:top_n]
