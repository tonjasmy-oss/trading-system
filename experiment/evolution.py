"""
参数进化器 - Parameter Evolver
参考 QuantDinger 的 app/services/experiment/evolution.py

基于回测结果，对策略参数进行网格搜索或随机变异进化。
"""

import itertools
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)


@dataclass
class EvolutionResult:
    """进化结果"""
    variants: List[Dict] = field(default_factory=list)  # 生成的参数变体列表
    method: str = "grid"
    total_variants: int = 0


class ParameterEvolver:
    """
    参数进化器

    支持两种方法：
      - grid: 网格搜索（全排列）
      - random: 随机采样（max_variants 控制数量）
    """

    def __init__(self):
        pass

    def evolve(
        self,
        parameter_space: Dict[str, list],
        method: str = "grid",
        max_variants: int = 20,
    ) -> EvolutionResult:
        """
        生成参数变体

        Args:
            parameter_space: {"stop_loss_pct": [0.01, 0.02], "take_profit_pct": [0.04, 0.06], ...}
            method: "grid" 或 "random"
            max_variants: random 模式下的最大变体数

        Returns:
            EvolutionResult
        """
        if method == "grid":
            variants = self._grid_search(parameter_space)
        else:
            variants = self._random_search(parameter_space, max_variants)

        return EvolutionResult(
            variants=variants,
            method=method,
            total_variants=len(variants),
        )

    @staticmethod
    def _grid_search(param_space: Dict[str, list]) -> List[Dict]:
        """网格搜索：生成所有参数组合"""
        keys = list(param_space.keys())
        values = [param_space[k] for k in keys]
        variants = []
        for combo in itertools.product(*values):
            variants.append(dict(zip(keys, combo)))
        return variants

    @staticmethod
    def _random_search(param_space: Dict[str, list], max_variants: int) -> List[Dict]:
        """随机搜索"""
        variants = []
        seen = set()
        keys = list(param_space.keys())
        attempts = 0
        max_attempts = max_variants * 10

        while len(variants) < max_variants and attempts < max_attempts:
            attempts += 1
            combo = tuple(
                random.choice(param_space[k])
                for k in keys
            )
            if combo not in seen:
                seen.add(combo)
                variants.append(dict(zip(keys, combo)))

        return variants

    @staticmethod
    def default_parameter_space(strategy_type: str = "RSI") -> Dict[str, list]:
        """返回默认参数空间"""
        spaces = {
            "RSI": {
                "rsi_period": [7, 10, 14, 21],
                "rsi_oversold": [20, 25, 30, 35],
                "rsi_overbought": [60, 65, 70, 75],
                "stop_loss_pct": [0.01, 0.015, 0.02, 0.03, 0.04],
                "take_profit_pct": [0.02, 0.04, 0.06, 0.08, 0.10],
            },
            "MACD": {
                "fast_period": [8, 12, 16],
                "slow_period": [21, 26, 31],
                "signal_period": [7, 9, 12],
                "stop_loss_pct": [0.01, 0.02, 0.03],
                "take_profit_pct": [0.03, 0.05, 0.08],
            },
            "ATRSTOP": {
                "ema_period": [10, 14, 20, 26],
                "atr_period": [10, 14, 20],
                "atr_multiplier": [1.0, 1.5, 2.0, 2.5, 3.0],
                "stop_loss_pct": [0.01, 0.02, 0.03],
                "take_profit_pct": [0.03, 0.05, 0.08],
            },
            "Bollinger": {
                "bb_period": [14, 20, 26],
                "bb_std": [1.5, 2.0, 2.5],
                "stop_loss_pct": [0.01, 0.02, 0.03],
                "take_profit_pct": [0.03, 0.05, 0.08],
            },
        }
        return spaces.get(strategy_type, spaces["RSI"])
