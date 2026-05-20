"""
LLM 策略生成器 - AI Strategy Generator
参考 QuantDinger 的 AI 策略生成设计

使用 LLM 根据市场状态和历史数据，生成策略代码和参数建议。
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


@dataclass
class StrategySpec:
    """策略规格"""
    name: str
    strategy_type: str  # "RSI", "MACD", "ATRSTOP", "Bollinger", "SMAcross"
    description: str
    params: Dict[str, float] = field(default_factory=dict)
    rationale: str = ""  # LLM 给出的理由

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "strategy_type": self.strategy_type,
            "description": self.description,
            "params": self.params,
            "rationale": self.rationale,
        }


class LLMStrategist:
    """
    AI 策略顾问

    使用 LLM 根据：
      - 当前市场状态
      - 近期价格数据摘要
      - 可用的策略模板

    生成策略配置建议。
    """

    SYSTEM_PROMPT = """你是一个量化交易策略顾问。根据市场状态和价格数据，为交易系统生成策略配置。

输出格式（严格 JSON）：
{
  "strategies": [
    {
      "name": "策略名称",
      "strategy_type": "RSI|MACD|ATRSTOP|Bollinger|SMAcross",
      "description": "策略描述（一句话）",
      "params": {
        "stop_loss_pct": 0.02,
        "take_profit_pct": 0.04,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70
      },
      "rationale": "推荐理由"
    }
  ]
}

策略类型说明：
- RSI: 超买超卖反转策略，需要 rsi_period/rsi_oversold/rsi_overbought
- MACD: 趋势跟踪策略，需要 fast_period/slow_period/signal_period
- ATRSTOP: 基于ATR的动态止损趋势策略，需要 ema_period/atr_period/atr_multiplier
- Bollinger: 布林带均值回归策略，需要 bb_period/bb_std
- SMAcross: 均线交叉策略，需要 fast_sma/slow_sma

所有策略都需要 stop_loss_pct 和 take_profit_pct。
"""

    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
    ):
        self.api_key = api_key or os.getenv("AI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
        self.base_url = base_url or os.getenv("AI_BASE_URL", "")
        self.model = model or os.getenv("AI_MODEL", "deepseek-chat")
        self._client = None

    @property
    def client(self):
        if self._client is None and _OPENAI_AVAILABLE and self.api_key:
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def is_available(self) -> bool:
        return self.client is not None

    def generate_strategies(
        self,
        regime: dict,
        price_summary: dict,
        count: int = 3,
    ) -> List[StrategySpec]:
        """
        生成策略候选

        Args:
            regime: 市场状态识别结果
            price_summary: 价格数据摘要 {"current_price": ..., "volatility": ..., ...}
            count: 生成策略数量

        Returns:
            StrategySpec 列表
        """
        if not self.is_available():
            logger.warning("LLM 不可用，返回默认策略")
            return self._default_strategies(regime, count)

        user_prompt = self._build_prompt(regime, price_summary, count)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content
            data = json.loads(text)
            strategies = data.get("strategies", [])

            specs = []
            for s in strategies[:count]:
                specs.append(StrategySpec(
                    name=s.get("name", "AI_Strategy"),
                    strategy_type=s.get("strategy_type", "RSI"),
                    description=s.get("description", ""),
                    params=s.get("params", {}),
                    rationale=s.get("rationale", ""),
                ))
            return specs

        except Exception as e:
            logger.error(f"LLM 策略生成失败: {e}")
            return self._default_strategies(regime, count)

    def _build_prompt(self, regime, summary, count):
        return f"""当前市场状态：
- 状态：{regime.get('regime')}
- 置信度：{regime.get('confidence')}
- 特征：{json.dumps(regime.get('features', {}))}
- 描述：{regime.get('description')}
- 推荐策略类型：{regime.get('recommended_strategies')}

价格摘要：
- 当前价格：{summary.get('current_price')}
- 近期波动率：{summary.get('volatility')}%
- 近期趋势：{summary.get('trend')}

请生成 {count} 个最适合当前市场的策略配置。"""

    @staticmethod
    def _default_strategies(regime, count=3) -> List[StrategySpec]:
        """LLM 不可用时的默认策略"""
        defaults = {
            "trending_up": [
                StrategySpec("ATR趋势跟踪", "ATRSTOP", "顺势ATR跟踪", {"ema_period": 14, "atr_period": 14, "atr_multiplier": 2.0, "stop_loss_pct": 0.03, "take_profit_pct": 0.06}),
                StrategySpec("RSI动量", "RSI", "RSI顺势买入", {"rsi_period": 10, "rsi_oversold": 30, "rsi_overbought": 65, "stop_loss_pct": 0.02, "take_profit_pct": 0.05}),
                StrategySpec("MACD趋势", "MACD", "MACD金叉跟踪", {"fast_period": 12, "slow_period": 26, "signal_period": 9, "stop_loss_pct": 0.02, "take_profit_pct": 0.04}),
            ],
            "trending_down": [
                StrategySpec("ATR防御", "ATRSTOP", "下行趋势ATR保护", {"ema_period": 20, "atr_period": 20, "atr_multiplier": 3.0, "stop_loss_pct": 0.02, "take_profit_pct": 0.04}),
            ],
            "ranging": [
                StrategySpec("布林带回归", "Bollinger", "震荡均值回归", {"bb_period": 20, "bb_std": 2.0, "stop_loss_pct": 0.02, "take_profit_pct": 0.03}),
                StrategySpec("RSI震荡", "RSI", "RSI超卖超买", {"rsi_period": 14, "rsi_oversold": 28, "rsi_overbought": 65, "stop_loss_pct": 0.015, "take_profit_pct": 0.025}),
            ],
            "high_volatility": [
                StrategySpec("宽幅布林", "Bollinger", "高波宽幅布林", {"bb_period": 14, "bb_std": 2.5, "stop_loss_pct": 0.03, "take_profit_pct": 0.06}),
            ],
        }
        strategies = defaults.get(regime.get("regime", ""), defaults["ranging"])
        return strategies[:count]
