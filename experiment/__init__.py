"""
AI 策略实验管线 - Experiment Pipeline
参考 QuantDinger 的 app/services/experiment/

流程：
  市场状态识别 → LLM 生成策略候选 → 批量回测 → 多因子评分 → 参数进化 → 最优策略输出

使用方式：
  from experiment import ExperimentRunner
  runner = ExperimentRunner(symbol="ETH/USDT", timeframe="4h")
  result = runner.run()
"""

from .regime import MarketRegimeDetector, RegimeResult
from .scoring import StrategyScorer, ScoreResult
from .evolution import ParameterEvolver, EvolutionResult
from .runner import ExperimentRunner, ExperimentResult
from .llm_strategist import LLMStrategist

__all__ = [
    "MarketRegimeDetector", "RegimeResult",
    "StrategyScorer", "ScoreResult",
    "ParameterEvolver", "EvolutionResult",
    "ExperimentRunner", "ExperimentResult",
    "LLMStrategist",
]
