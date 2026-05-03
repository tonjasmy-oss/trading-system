"""
MultiStrategyVote — 多策略投票聚合器
=====================================

将多个策略（内置策略或公式策略）的信号加权投票，
输出统一的买入/卖出/持仓信号。

设计原则：
  - 各子策略独立计算信号，互不干扰
  - 投票权重可配（默认 RSI 40% + SMA 30% + MACD 30%）
  - 支持阈值设置（如 threshold=0.3，则加权和 > 0.3 才视为买入）
  - 阈值对称：> threshold 买入，< -threshold 卖出，中间持仓

使用方式：
  vote = MultiStrategyVote([
      (strategy_rsi,  0.4),
      (strategy_sma,  0.3),
      (strategy_macd, 0.3),
  ], threshold=0.3)

  engine = BacktestEngine(strategy=vote, ...)
"""

from typing import List, Tuple, Dict
from strategies import Strategy, Signal, StrategyConfig


class MultiStrategyVote(Strategy):
    """
    多策略投票聚合器

    Args:
        strategies: List[Tuple[Strategy, float]] — (策略实例, 权重)
        threshold:  float — 投票阈值（加权绝对值超过此值才算有效信号）
        name:       str  — 投票器名称（用于显示）
    """

    def __init__(
        self,
        strategies: List[Tuple[Strategy, float]],
        threshold: float = 0.3,
        name: str = "MultiVote",
    ):
        # 归一化权重
        total = sum(w for _, w in strategies)
        if abs(total - 1.0) > 1e-6:
            strategies = [(s, w / total) for s, w in strategies]

        self.strategies = strategies
        self.threshold = threshold
        self.name = name
        self.config: StrategyConfig = strategies[0][0].config if strategies else StrategyConfig()

    # ── 指标聚合 ──────────────────────────────────────────────

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        """
        聚合所有子策略的指标。
        返回 dict，key 格式为 "策略名.指标名"。
        """
        result: Dict[str, List[float]] = {}
        for strategy, weight in self.strategies:
            try:
                inds = strategy.populate_indicators(candles)
                prefix = strategy.__class__.__name__
                for k, v in inds.items():
                    result[f"{prefix}.{k}"] = v
            except Exception:
                # 某些策略可能不支持 indicators，直接跳过
                pass
        return result

    # ── 信号投票 ──────────────────────────────────────────────

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        """
        加权投票买入信号。
        子策略信号 × 权重 累加，超过 threshold → 买入(1)，
        低于 -threshold → 卖出(-1)，中间 → 持仓(0)。
        """
        n = len(candles)
        votes: List[float] = [0.0] * n

        for strategy, weight in self.strategies:
            try:
                sigs = strategy.populate_entry_trend(candles)
                for i, s in enumerate(sigs):
                    if isinstance(s, Signal):
                        s = s.value if hasattr(s, 'value') else int(s)
                    votes[i] += int(s) * weight
            except Exception:
                # 策略计算失败则跳过
                continue

        return self._threshold(votes)

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        """
        加权投票卖出信号（逻辑同 entry，但方向取反）。
        注意：exit 信号的含义是"退出持仓"而非"做空"。
        合并子策略的 exit + entry（做空端）一起投票。
        """
        n = len(candles)
        votes: List[float] = [0.0] * n

        for strategy, weight in self.strategies:
            try:
                # 优先用 exit 策略
                try:
                    sigs = strategy.populate_exit_trend(candles)
                except Exception:
                    # fallback：用 entry 信号的负数（反向）
                    sigs = strategy.populate_entry_trend(candles)
                    sigs = [-x for x in sigs]

                for i, s in enumerate(sigs):
                    if isinstance(s, Signal):
                        s = s.value if hasattr(s, 'value') else int(s)
                    votes[i] += int(s) * weight
            except Exception:
                continue

        return self._threshold(votes)

    def populate_all_signals(self, candles: List[Dict]) -> List[int]:
        """
        返回聚合后的综合信号（entry + exit 合并）。
        同时考虑买入和卖出，取最终决策。
        """
        n = len(candles)
        votes = [0.0] * n

        for strategy, weight in self.strategies:
            try:
                entry = strategy.populate_entry_trend(candles)
                try:
                    exit_s = strategy.populate_exit_trend(candles)
                except Exception:
                    exit_s = [-x for x in entry]  # 无 exit 用反向 entry

                for i in range(n):
                    entry_sig = int(entry[i]) if not isinstance(entry[i], Signal) else int(entry[i].value)
                    exit_sig  = int(exit_s[i])  if not isinstance(exit_s[i],  Signal) else int(exit_s[i].value)
                    # 综合信号：买入=1，卖出=-1，其他=0
                    combined = entry_sig if abs(entry_sig) > abs(exit_sig) else exit_sig
                    votes[i] += combined * weight
            except Exception:
                continue

        return self._threshold(votes)

    def _threshold(self, votes: List[float]) -> List[int]:
        """将加权投票值映射为 [-1, 0, 1] 信号"""
        result = []
        for v in votes:
            if v > self.threshold:
                result.append(Signal.BUY)
            elif v < -self.threshold:
                result.append(Signal.SELL)
            else:
                result.append(Signal.HOLD)
        return result

    # ── 策略接口 ──────────────────────────────────────────────

    def get_config(self) -> StrategyConfig:
        return self.config

    def __repr__(self) -> str:
        weights = {s.__class__.__name__: w for s, w in self.strategies}
        return f"MultiStrategyVote(strategies={weights}, threshold={self.threshold})"
