"""
signal_router.py — 多信号候选路由（Agent-S Best-of-N 借鉴）
============================================================

核心思想（Agent-S 借鉴）：
  - Agent-S 的 Memory 不是选第一个候选，而是对多个 Trajectory 打分后选最优
  - 本模块对多个信号候选（来自不同策略/不同参数）打分，优先选择：
      ① 近期同类策略胜率高的
      ② 置信度高的
      ③ 当前市场状态匹配的
      ④ 历史 MFE/MAE 表现更好的

使用方式：
  router = SignalRouter(menxia, trade_history)

  candidates = [
      CandidateSignal(symbol="BTC/USDT", strategy="RSI",   confidence=0.72, side="BUY", price=67000, quantity=0.1),
      CandidateSignal(symbol="BTC/USDT", strategy="MACD",  confidence=0.65, side="BUY", price=67000, quantity=0.1),
      CandidateSignal(symbol="BTC/USDT", strategy="BB",    confidence=0.58, side="BUY", price=67000, quantity=0.1),
  ]

  best, alternatives = router.route(candidates)
  # best = 评分最高的候选
  # alternatives = 其余按评分排序的候选项（可作为 Dashboard 展示）
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class CandidateSignal:
    """候选信号"""
    symbol: str
    strategy: str            # RSI / MACD / BB / SMA / VOTE / FORMULA
    confidence: float         # 置信度 0~1
    side: str               # BUY / SELL
    price: float              # 信号触发价格
    quantity: float
    timeframe: str = "4h"
    agent_id: str = "agent_1"

    # 各策略原始指标（可选，用于补充评估）
    indicators: Dict = field(default_factory=dict)
    # e.g. {"rsi": 28.5, "macd_hist": 0.0023, "bb_position": 0.12}

    # 以下由 SignalRouter 填充
    score: float = 0.0        # 综合评分 0~100
    score_breakdown: Dict = field(default_factory=dict)  # 各项得分明细


@dataclass
class RoutingResult:
    """路由结果"""
    chosen: Optional[CandidateSignal]   # 选中的最优候选（None = 全部被否决）
    alternatives: List[CandidateSignal]  # 其余候选（按评分降序）
    rejected: List[CandidateSignal]     # 被风控否决的候选
    routing_reason: str                 # 选择理由


# ─────────────────────────────────────────────────────────────
# 评分引擎
# ─────────────────────────────────────────────────────────────

class SignalScorer:
    """
    候选信号评分器
    评分维度（总分 100）：
      ① 置信度得分          0~30 分
      ② 策略近期胜率        0~25 分（查 trade_history）
      ③ 市场状态匹配度      0~25 分
      ④ 风险/暴露度得分    0~20 分（暴露度越低分越高）
    """

    # 评分权重（可配置）
    WEIGHT_CONFIDENCE   = 30
    WEIGHT_STRATEGY_WIN = 25
    WEIGHT_REGIME_MATCH = 25
    WEIGHT_EXPOSURE     = 20

    def __init__(self, trade_history=None, market_regime=None):
        self._th = trade_history          # TradeHistory instance
        self._mr = market_regime          # MarketRegime instance (P2)

    def score(self, c: CandidateSignal,
              exposure_pct: float = 0.0,
              market_trend: str = "unknown",
              market_volatility: str = "unknown") -> Tuple[float, Dict]:
        """
        对单个候选信号评分。
        Returns: (总分, 各项得分明细)
        """
        bd = {}  # breakdown

        # ① 置信度得分（0~30）
        conf_score = min(c.confidence, 1.0) * self.WEIGHT_CONFIDENCE
        bd["confidence"] = round(conf_score, 2)

        # ② 策略近期胜率（0~25）
        strategy_score = self._strategy_recent_winrate(c.strategy, c.symbol) * self.WEIGHT_STRATEGY_WIN
        bd["strategy_winrate"] = round(strategy_score, 2)

        # ③ 市场状态匹配度（0~25）
        regime_score = self._regime_match_score(
            c.strategy, market_trend, market_volatility
        ) * self.WEIGHT_REGIME_MATCH
        bd["regime_match"] = round(regime_score, 2)

        # ④ 暴露度得分（0~20，暴露度越高分数越低）
        exp_score = max(0, self.WEIGHT_EXPOSURE - exposure_pct * 0.4)
        bd["exposure"] = round(exp_score, 2)

        total = conf_score + strategy_score + regime_score + exp_score
        bd["total"] = round(total, 2)
        return total, bd

    def _strategy_recent_winrate(self, strategy: str, symbol: str) -> float:
        """查询该策略最近 20 次交易的胜率（0~1）"""
        if not self._th:
            return 0.5  # 无数据时默认中性
        try:
            stats = self._th.get_performance_stats(
                strategy=strategy, symbol=symbol, min_trades=3
            )
            if not stats.get("enough_data"):
                return 0.5
            return stats.get("win_rate", 0.5)
        except Exception:
            return 0.5

    def _regime_match_score(self, strategy: str,
                            trend: str, volatility: str) -> float:
        """
        策略 vs 市场状态匹配度（0~1）
        经验规则（来自 Agent-S 类似的 history-guided 思路）：
          - RSI/Oscillator 类策略：适合 ranging 市场，高波动时表现差
          - Trend-following（MACD/SMA）：适合有明显趋势的市场
          - BB：适合波动率高、趋势不明的市场
        """
        if trend == "unknown" or volatility == "unknown":
            return 0.5  # 未知市场状态，中性给分

        # trend/strategy 匹配矩阵
        trend_strategies = {"MACD", "SMA"}
        range_strategies = {"RSI", "BB"}

        if strategy in trend_strategies:
            return 1.0 if trend in ("uptrend", "downtrend") else 0.3
        elif strategy in range_strategies:
            return 0.3 if trend in ("uptrend", "downtrend") else 0.8
        elif strategy == "BB":
            return 0.7 if volatility == "high" else 0.5
        return 0.5


# ─────────────────────────────────────────────────────────────
# 信号路由器
# ─────────────────────────────────────────────────────────────

class SignalRouter:
    """
    多信号候选路由器

    工作流程：
      1. 接收 N 个候选信号（通常来自不同策略或不同参数）
      2. 对每个候选评分（SignalScorer）
      3. 按评分排序
      4. 遍历候选项，通过门下省风控审核的第一个即为选中项
      5. 其余记录为 alternatives（Dashboard 展示用）
      6. 被风控否决的记录为 rejected
    """

    def __init__(self, menxia=None, trade_history=None, market_regime=None):
        self._menxia = menxia
        self._scorer = SignalScorer(trade_history, market_regime)

    def route(self, candidates: List[CandidateSignal],
              exposure_pct: float = 0.0,
              market_trend: str = "unknown",
              market_volatility: str = "unknown",
              max_candidates: int = 5) -> RoutingResult:
        """
        执行路由。
        对候选信号列表评分 → 通过风控审核 → 返回最优候选
        """
        if not candidates:
            return RoutingResult(
                chosen=None,
                alternatives=[],
                rejected=[],
                routing_reason="无候选信号",
            )

        # 限制候选数量（防止策略太多导致评分噪音）
        candidates = candidates[:max_candidates]

        # ── Step 1: 评分 ──
        scored: List[CandidateSignal] = []
        for c in candidates:
            score, breakdown = self._scorer.score(
                c, exposure_pct, market_trend, market_volatility
            )
            c.score = score
            c.score_breakdown = breakdown
            scored.append(c)

        # 按评分降序
        scored.sort(key=lambda x: x.score, reverse=True)

        # ── Step 2: 风控审核（按评分从高到低）──
        rejected: List[CandidateSignal] = []
        for c in scored:
            if self._menxia:
                try:
                    # 构造门下省需要的参数
                    stop_loss = c.price * 0.975   # 默认 2.5% 止损
                    take_profit = c.price * 1.04   # 默认 4% 止盈
                    result = self._menxia.review_open(
                        symbol=c.symbol,
                        entry_price=c.price,
                        quantity=c.quantity,
                        agent_id=c.agent_id,
                        signal_confidence=c.confidence,
                        indicators=c.indicators,
                    )
                    if not result.approved:
                        c.score = -999  # 否决后 score 降为负
                        rejected.append(c)
                        continue
                except Exception as e:
                    logger.warning(f"[SignalRouter] 门下省审核异常 {c.symbol}: {e}")
                    continue
            break  # 第一个通过审核的即为选中

        # 重新整理
        passed = [c for c in scored if c.score > -999]
        chosen = passed[0] if passed else None
        alternatives = passed[1:] if len(passed) > 1 else []

        if chosen:
            reason = (
                f"选中 {chosen.strategy}（评分 {chosen.score}，"
                f"置信度 {chosen.confidence:.0%}，"
                f"胜率 {chosen.score_breakdown.get('strategy_winrate', 0) / 25:.0%}）"
            )
        elif rejected:
            reason = f"全部 {len(rejected)} 个候选被风控否决"
        else:
            reason = "无候选信号"

        return RoutingResult(
            chosen=chosen,
            alternatives=alternatives,
            rejected=rejected,
            routing_reason=reason,
        )

    def score_only(self, candidates: List[CandidateSignal],
                   exposure_pct: float = 0.0,
                   market_trend: str = "unknown",
                   market_volatility: str = "unknown") -> List[CandidateSignal]:
        """
        仅评分（不做风控审核），用于 Dashboard 展示所有候选信号质量
        """
        for c in candidates:
            score, breakdown = self._scorer.score(
                c, exposure_pct, market_trend, market_volatility
            )
            c.score = score
            c.score_breakdown = breakdown
        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates
