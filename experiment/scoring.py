"""
多因子策略评分器 - Strategy Scorer
参考 QuantDinger 的 app/services/experiment/scoring.py

对回测结果进行多维度评分，综合输出一个 0-100 的分数。
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


@dataclass
class ScoreResult:
    """评分结果"""
    strategy_name: str
    total_score: float          # 0-100
    factors: Dict[str, float] = field(default_factory=dict)
    rank: int = 0


class StrategyScorer:
    """
    多因子策略评分器

    评分维度（权重可配）：
      - total_return    总收益率         权重 25%
      - sharpe_ratio    夏普比率         权重 20%
      - max_drawdown    最大回撤         权重 20%
      - win_rate        胜率             权重 15%
      - profit_factor   盈亏比           权重 10%
      - stability       稳定性（交易频率） 权重 10%
    """

    DEFAULT_WEIGHTS = {
        "total_return":    0.25,
        "sharpe_ratio":    0.20,
        "max_drawdown":    0.20,
        "win_rate":        0.15,
        "profit_factor":   0.10,
        "stability":       0.10,
    }

    def __init__(self, weights: Dict[str, float] = None):
        self.weights = weights or self.DEFAULT_WEIGHTS

    def score_one(self, bt_result: dict, strategy_name: str) -> ScoreResult:
        """
        对单个回测结果评分

        Args:
            bt_result: BacktestResult 的字典表示
            strategy_name: 策略名称

        Returns:
            ScoreResult
        """
        factors = {}

        # 1. 总收益率（归一化到 0-100）
        total_return = bt_result.get("total_return_pct", 0)
        factors["total_return"] = self._normalize_return(total_return)

        # 2. 夏普比率（假设 > 2 是满分）
        sharpe = bt_result.get("sharpe_ratio", 0)
        factors["sharpe_ratio"] = min(sharpe / 2.0, 1.0) * 100 if sharpe > 0 else 0

        # 3. 最大回撤（回撤越小越好）
        max_dd = abs(bt_result.get("max_drawdown_pct", 100))
        factors["max_drawdown"] = max(0, (1 - max_dd / 50)) * 100  # 50%回撤=0分

        # 4. 胜率
        win_rate = bt_result.get("win_rate_pct", 0)
        factors["win_rate"] = min(win_rate / 70, 1.0) * 100  # 70%胜率=满分

        # 5. 盈亏比
        avg_win = bt_result.get("avg_win_pct", 0)
        avg_loss = abs(bt_result.get("avg_loss_pct", 1))
        profit_factor = avg_win / avg_loss if avg_loss > 0 else 0
        factors["profit_factor"] = min(profit_factor / 3.0, 1.0) * 100

        # 6. 稳定性（交易频率是否合理，避免过度交易）
        total_trades = bt_result.get("total_trades", 0)
        factors["stability"] = min(total_trades / 50, 1.0) * 100 if total_trades > 0 else 0

        # 加权计算
        total_score = sum(
            factors.get(k, 0) * self.weights.get(k, 0)
            for k in self.weights
        )

        return ScoreResult(
            strategy_name=strategy_name,
            total_score=round(total_score, 2),
            factors={k: round(v, 2) for k, v in factors.items()},
        )

    def score_batch(self, results: List[tuple]) -> List[ScoreResult]:
        """
        批量评分并排名

        Args:
            results: [(strategy_name, bt_result_dict), ...]

        Returns:
            按总分降序排列的 ScoreResult 列表
        """
        scored = []
        for name, result in results:
            scored.append(self.score_one(result, name))

        scored.sort(key=lambda x: x.total_score, reverse=True)
        for i, s in enumerate(scored):
            s.rank = i + 1

        return scored

    @staticmethod
    def _normalize_return(ret_pct: float) -> float:
        """收益率归一化"""
        if ret_pct >= 100:
            return 100.0
        if ret_pct <= -50:
            return 0.0
        return ((ret_pct + 50) / 150) * 100
