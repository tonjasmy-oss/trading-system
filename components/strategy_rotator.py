"""
strategy_rotator.py — 市场状态感知策略轮动
============================================

根据 MarketRegime 检测的市场状态，自动选择最优策略：

  状态                 → 推荐策略
  ──────────────────────────────────────────
  uptrend + high_vol   → ATRSTOP  (趋势跟随+动态止损)
  uptrend + low_vol    → SMA      (简单均线趋势)
  downtrend + high_vol → RSI      (超卖反弹)
  downtrend + low_vol  → MACD     (金叉/死叉)
  ranging  + high_vol  → BOLLINGER(布林带均值回归)
  ranging  + low_vol   → KDJ      (摆动交易)
  unknown              → MACD     (默认稳健)
"""

import logging
logger = logging.getLogger(__name__)

REGIME_STRATEGY_MAP = {
    ("uptrend", "high"):    ("ATRSTOP",  "上升趋势+高波动，ATR动态止损(回测最优)", {"ema_period": 20, "atr_multiplier": 2.0}),
    ("uptrend", "medium"):  ("ATRSTOP",  "上升趋势+中等波动，ATR趋势跟随", {"ema_period": 20, "atr_multiplier": 2.0}),
    ("uptrend", "low"):     ("SMA",      "上升趋势+低波动，均线趋势", {"fast_period": 10, "slow_period": 30}),
    ("downtrend", "high"):  ("RSI",      "下降趋势+高波动，超卖反弹", {"rsi_period": 10, "oversold": 20, "overbought": 55}),
    ("downtrend", "medium"):("RSI",      "下降趋势+中等波动，超卖做多", {"rsi_period": 14, "oversold": 28, "overbought": 55}),
    ("downtrend", "low"):   ("RSI",      "下降趋势+低波动，超卖做多", {"rsi_period": 14, "oversold": 28, "overbought": 55}),
    ("ranging", "high"):    ("ATRSTOP",  "震荡市+高波动，ATR动态止损(回测最优)", {"ema_period": 20, "atr_multiplier": 2.0}),
    ("ranging", "medium"):  ("BOLLINGER","震荡市+中等波动，布林带均值回归", {"period": 20, "std_dev": 2.0}),
    ("ranging", "low"):     ("KDJ",      "震荡市+低波动，KDJ摆动交易", {}),
}
FALLBACK_STRATEGY = ("MACD", "市场状态未知，回退到MACD", {})


class StrategyRotator:
    """市场状态感知策略轮动器"""

    def __init__(self, symbol: str = "", config_map: dict = None):
        self.symbol = symbol
        self.config_map = config_map or {}
        self._last_strategy: str = ""
        self._stability_counter: int = 0
        self._min_stability: int = 2

    def pick(self, regime: dict) -> dict:
        trend = (regime.get("trend") or "unknown").lower()
        vol = (regime.get("volatility") or "unknown").lower()
        confidence = regime.get("confidence", 0.5)
        key = (trend, vol)
        strategy_name, reason, kwargs = REGIME_STRATEGY_MAP.get(key, FALLBACK_STRATEGY)

        if strategy_name == self._last_strategy:
            self._stability_counter += 1
        else:
            self._stability_counter = 0

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
