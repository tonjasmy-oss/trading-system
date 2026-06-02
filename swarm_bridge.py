"""
swarm_bridge.py — Vibe-Trading Swarm 预设桥接模块
==================================================

将 Vibe-Trading 的 29 种 Swarm 智能体蜂群预设适配到本地交易系统，
用 Swarm 的多 Agent 角色分工替换原有的简单 RSI+MACD+BOLL 三策略投票。

原理：
  1. 读取 Swarm 预设 YAML，解析 Agent 角色和任务分工
  2. 每个 Agent 角色映射到本地策略（RSI/MACD/BOLLINGER/FACTOR/DONCHIAN）
  3. 按预设中的优先级权重聚合信号
  4. 输出统一的买入/卖出/持仓信号

支持 29 种预设，包括：
  - crypto_trading_desk    加密货币交易台（流动性+费率+链上+风控）
  - macro_strategy_forum   宏观策略论坛
  - quant_strategy_desk    量化策略台
  - risk_committee         风险委员会
  - sentiment_intelligence 情绪情报团队
  - ...等

使用方式：
  from swarm_bridge import SwarmVoteStrategy, list_swarm_presets

  # 列出所有预设
  presets = list_swarm_presets()

  # 使用加密货币交易台预设
  strategy = SwarmVoteStrategy(
      config=StrategyConfig(symbol="BTC/USDT", timeframe="4h"),
      preset_name="crypto_trading_desk",
  )
"""

from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# 路径设置
# ══════════════════════════════════════════════════════════════

_VT_ROOT = Path("/root/Vibe-Trading/agent")
if str(_VT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VT_ROOT))

_PRESETS_DIR = Path("/root/Vibe-Trading/agent/src/swarm/presets")

# 本地策略
from strategies import Strategy, Signal, StrategyConfig

# ── 延迟导入 ──
def _build_local_strategy(name: str, config: StrategyConfig, **kwargs) -> Strategy:
    """构建本地策略实例（复用 strategies.build_strategy）"""
    import strategies as st
    return st.build_strategy(name, config, **kwargs)


# ══════════════════════════════════════════════════════════════
# Agent 角色 → 本地策略映射表
# ══════════════════════════════════════════════════════════════

# 每个 Swarm 预设中的 Agent 角色，根据其分析职责映射到本地策略类型
# key: 预定义角色关键词 → (策略名, 权重, 参数覆盖)
_ROLE_STRATEGY_MAP: Dict[str, Tuple[str, float, dict]] = {
    # ── 技术分析类 ──
    "technical":       ("BOLLINGER", 0.30, {}),
    "momentum":        ("MACD",      0.25, {}),
    "trend":           ("DONCHIAN",  0.25, {}),
    "oscillator":      ("RSI",       0.20, {"rsi_period": 14}),
    "pattern":         ("BOLLINGER", 0.20, {"period": 20, "std_dev": 2.0}),
    "volume":          ("RSI",       0.15, {"rsi_period": 8}),   # volume 用 RSI 代理

    # ── 风险/资金类 ──
    "risk":            ("ATRSTOP",   0.25, {}),
    "volatility":      ("ATRSTOP",   0.20, {}),
    "hedging":         ("ATRSTOP",   0.15, {}),

    # ── 宏观/基本面类 ──
    "macro":           ("DONCHIAN",  0.20, {"channel_period": 50, "trend_ema_period": 100}),
    "fundamental":     ("MULTIFACTOR", 0.20, {}),
    "sentiment":       ("COINGLASS", 0.15, {}),
    "flow":            ("MULTIFACTOR", 0.20, {}),
    "on.chain":        ("MULTIFACTOR", 0.15, {}),

    # ── 量化/统计类 ──
    "quant":           ("MULTIFACTOR", 0.25, {}),
    "statistical":     ("STAT_ARB",  0.25, {}),
    "factor":          ("MULTIFACTOR", 0.30, {}),
    "alpha":           ("MULTIFACTOR", 0.30, {}),

    # ── 加密货币特有 ──
    "funding":         ("MULTIFACTOR", 0.20, {}),
    "liquidation":     ("COINGLASS", 0.20, {}),
    "derivatives":     ("MULTIFACTOR", 0.20, {}),
    "arbitrage":       ("FUNDING_ARB", 0.20, {}),

    # ── 商品/周期 ──
    "supply":          ("DONCHIAN",   0.20, {"channel_period": 50}),
    "demand":          ("MULTIFACTOR", 0.20, {}),
    "commodity":       ("DONCHIAN",   0.25, {}),
    "cycle":           ("DONCHIAN",   0.20, {"channel_period": 50, "trend_ema_period": 100}),

    # ── 信用/固收/利率 ──
    "credit":          ("MULTIFACTOR", 0.20, {}),
    "bond":            ("MULTIFACTOR", 0.20, {}),
    "fixed":           ("MULTIFACTOR", 0.20, {}),
    "interest":        ("DONCHIAN",   0.20, {}),

    # ── 事件驱动 ──
    "event":           ("COINGLASS",  0.20, {}),
    "scout":           ("COINGLASS",  0.15, {}),
    "impact":          ("MULTIFACTOR", 0.20, {}),

    # ── 选基/业绩 ──
    "fund":            ("MULTIFACTOR", 0.20, {}),
    "screener":        ("MULTIFACTOR", 0.20, {}),
    "fof":             ("MULTIFACTOR", 0.20, {}),
    "performance":     ("MULTIFACTOR", 0.20, {}),

    # ── 基本面/估值/研究 ──
    "financial":       ("MULTIFACTOR", 0.20, {}),
    "valuation":       ("MULTIFACTOR", 0.20, {}),
    "quality":         ("MULTIFACTOR", 0.20, {}),
    "research":        ("MULTIFACTOR", 0.20, {}),
    "editor":          ("MULTIFACTOR", 0.15, {}),

    # ── 地缘政治/能源 ──
    "geopolitical":    ("COINGLASS",  0.25, {}),
    "energy":          ("DONCHIAN",   0.20, {"channel_period": 50}),
    "chain":           ("MULTIFACTOR", 0.15, {}),

    # ── 全球配置/权益/加密货币 ──
    "share":           ("MULTIFACTOR", 0.20, {}),
    "allocation":      ("MULTIFACTOR", 0.25, {}),
    "crypto":          ("MULTIFACTOR", 0.25, {}),
    "equity":          ("MULTIFACTOR", 0.25, {}),
    "researcher":      ("MULTIFACTOR", 0.20, {}),

    # ── ML/数据科学 ──
    "feature":         ("MULTIFACTOR", 0.25, {}),
    "scientist":       ("MULTIFACTOR", 0.25, {}),

    # ── 配对交易/微结构 ──
    "correlation":     ("STAT_ARB",   0.25, {}),
    "cointegration":   ("STAT_ARB",   0.25, {}),
    "pair":            ("STAT_ARB",   0.20, {}),
    "microstructure":  ("MULTIFACTOR", 0.20, {}),

    # ── 默认为多因子 ──
    "option":          ("MULTIFACTOR", 0.20, {}),
    "underlying":      ("MULTIFACTOR", 0.20, {}),
    "strategist":      ("MULTIFACTOR", 0.25, {}),
    "builder":         ("MULTIFACTOR", 0.20, {}),
    "designer":        ("MULTIFACTOR", 0.20, {}),
    "greeks":          ("ATRSTOP",    0.25, {}),

    # ── 组合/投委会/执行/审核 ──
    "portfolio":       ("MULTIFACTOR", 0.25, {}),
    "bull":            ("MACD",       0.25, {}),
    "bear":            ("ATRSTOP",    0.25, {}),
    "chief":           ("MULTIFACTOR", 0.25, {}),
    "execution":       ("MULTIFACTOR", 0.20, {}),
    "inspector":       ("ATRSTOP",    0.20, {}),
    "monitor":         ("ATRSTOP",    0.20, {}),
    "scanner":         ("MULTIFACTOR", 0.20, {}),
    "tester":          ("STAT_ARB",   0.20, {}),
    "reviewer":        ("MULTIFACTOR", 0.15, {}),

    # ── 默认回退 ──
    "default":         ("VOTE",      0.25, {}),
}

# 预设级覆盖：特定预设使用自定义角色权重
_PRESET_ROLE_OVERRIDES: Dict[str, list] = {
    # ── 加密货币类 ──
    "crypto_trading_desk": [
        ("RSI",         0.30),   # 加密货币波动大，RSI 超买超卖信号强
        ("BOLLINGER",   0.25),   # 波动率区间
        ("ATRSTOP",     0.25),   # funding/liquidation → 风控
        ("COINGLASS",   0.20),   # 链上/情绪辅助
    ],
    "crypto_research_lab": [
        ("COINGLASS",   0.35),   # On-Chain + Sentiment → 链上情绪
        ("RSI",         0.25),   # 超买超卖
        ("BOLLINGER",   0.25),   # 波动率
        ("MULTIFACTOR", 0.15),   # Alpha 综合
    ],
    # ── 技术分析 ──
    "technical_analysis_panel": [
        ("RSI",       0.30),
        ("MACD",      0.25),
        ("BOLLINGER", 0.25),
        ("DONCHIAN",  0.20),
    ],
    # ── 风险/投委会 ──
    "risk_committee": [
        ("ATRSTOP",     0.35),
        ("MULTIFACTOR", 0.25),
        ("COINGLASS",   0.20),
        ("BOLLINGER",   0.20),
    ],
    "investment_committee": [
        ("MULTIFACTOR", 0.30),
        ("ATRSTOP",     0.30),
        ("MACD",        0.25),
        ("DONCHIAN",    0.15),
    ],
    "portfolio_review_board": [
        ("MULTIFACTOR", 0.35),
        ("ATRSTOP",     0.30),
        ("STAT_ARB",    0.20),
        ("BOLLINGER",   0.15),
    ],
    # ── 量化类 ──
    "quant_strategy_desk": [
        ("MULTIFACTOR", 0.40),
        ("STAT_ARB",    0.30),
        ("BOLLINGER",   0.30),
    ],
    "factor_research_committee": [
        ("MULTIFACTOR", 0.35),   # factor miner/combiner
        ("STAT_ARB",    0.30),   # validator/backtest → 统计验证
        ("DONCHIAN",    0.20),   # 趋势辅助
        ("BOLLINGER",   0.15),
    ],
    "ml_quant_lab": [
        ("STAT_ARB",    0.35),   # backtest + feature → 统计
        ("RSI",         0.25),   # 特征信号
        ("MULTIFACTOR", 0.25),   # data scientist
        ("BOLLINGER",   0.15),
    ],
    # ── 宏观/配置 ──
    "macro_strategy_forum": [
        ("DONCHIAN",    0.35),
        ("MULTIFACTOR", 0.35),
        ("ATRSTOP",     0.30),
    ],
    "macro_rates_fx_desk": [
        ("DONCHIAN",    0.35),   # 利率/汇率趋势
        ("MULTIFACTOR", 0.30),
        ("ATRSTOP",     0.20),
        ("MACD",        0.15),
    ],
    "global_allocation_committee": [
        ("DONCHIAN",    0.25),   # 多市场趋势
        ("MACD",        0.25),   # 动量
        ("MULTIFACTOR", 0.25),   # 基本面
        ("ATRSTOP",     0.25),   # 风控
    ],
    "global_equities_desk": [
        ("RSI",         0.30),   # A股/美股高波动用RSI
        ("DONCHIAN",    0.25),   # 趋势
        ("BOLLINGER",   0.25),   # 波动率
        ("COINGLASS",   0.20),   # 加密货币情绪
    ],
    "etf_allocation_desk": [
        ("MULTIFACTOR", 0.30),   # screener + optimizer
        ("DONCHIAN",    0.25),   # macro allocator → 趋势
        ("ATRSTOP",     0.25),   # risk budgeter
        ("MACD",        0.20),   # 动量辅助
    ],
    # ── 情绪/事件 ──
    "sentiment_intelligence_team": [
        ("COINGLASS",   0.40),
        ("BOLLINGER",   0.30),
        ("RSI",         0.30),
    ],
    "social_alpha_team": [
        ("COINGLASS",   0.45),   # Twitter/Telegram/Reddit → 纯情绪
        ("RSI",         0.30),
        ("BOLLINGER",   0.15),
        ("MULTIFACTOR", 0.10),
    ],
    "event_driven_task_force": [
        ("COINGLASS",   0.40),   # event scout → 情绪先行
        ("RSI",         0.30),   # impact → 快速反应
        ("ATRSTOP",     0.20),   # strategy builder → 风控
        ("BOLLINGER",   0.10),
    ],
    "geopolitical_war_room": [
        ("COINGLASS",   0.35),   # geopolitical → 情绪冲击
        ("DONCHIAN",    0.25),   # energy → 趋势
        ("ATRSTOP",     0.25),   # chief strategist → 风控
        ("MULTIFACTOR", 0.15),
    ],
    # ── 权益/基本面 ──
    "equity_research_team": [
        ("DONCHIAN",    0.30),   # 股票趋势
        ("MACD",        0.25),   # 动量
        ("MULTIFACTOR", 0.25),   # 基本面
        ("ATRSTOP",     0.20),
    ],
    "earnings_research_desk": [
        ("MULTIFACTOR", 0.30),   # fundamental + consensus
        ("COINGLASS",   0.25),   # earnings event → 情绪
        ("DONCHIAN",    0.25),   # revision trend
        ("BOLLINGER",   0.20),
    ],
    "fundamental_research_team": [
        ("DONCHIAN",    0.30),   # 长期趋势 (估值驱动)
        ("MACD",        0.25),   # 动量 (质量)
        ("MULTIFACTOR", 0.25),   # financial/valuation
        ("ATRSTOP",     0.20),
    ],
    "sector_rotation_team": [
        ("DONCHIAN",    0.30),   # 板块轮动 → 趋势
        ("MACD",        0.25),   # 动量
        ("RSI",         0.25),   # 超买超卖
        ("MULTIFACTOR", 0.20),
    ],
    # ── 固收/信用 ──
    "credit_research_team": [
        ("ATRSTOP",     0.35),   # 信用风险 → 风控为主
        ("MULTIFACTOR", 0.30),   # sector credit
        ("DONCHIAN",    0.20),   # 利率趋势
        ("FUNDING_ARB", 0.15),   # fixed income → 利差
    ],
    "convertible_bond_team": [
        ("DONCHIAN",    0.25),   # underlying equity → 股票趋势
        ("MACD",        0.25),   # 动量
        ("MULTIFACTOR", 0.20),   # bond floor
        ("BOLLINGER",   0.15),   # option embedded volatility
        ("ATRSTOP",     0.15),
    ],
    # ── 衍生品/套利 ──
    "derivatives_strategy_desk": [
        ("ATRSTOP",     0.35),
        ("BOLLINGER",   0.25),
        ("MULTIFACTOR", 0.25),
        ("COINGLASS",   0.15),
    ],
    "statistical_arbitrage_desk": [
        ("STAT_ARB",    0.30),   # pair scanner → 统计套利
        ("FUNDING_ARB", 0.30),   # arbitrage → 费率套利
        ("MULTIFACTOR", 0.25),   # microstructure
        ("ATRSTOP",     0.15),   # risk monitor
    ],
    "pairs_research_lab": [
        ("STAT_ARB",    0.35),   # correlation/cointegration
        ("FUNDING_ARB", 0.25),   # pair strategist
        ("BOLLINGER",   0.20),   # microstructure
        ("ATRSTOP",     0.20),   # 风控
    ],
    # ── 选基 ──
    "fund_selection_panel": [
        ("MULTIFACTOR", 0.30),   # screener + FOF optimizer
        ("DONCHIAN",    0.25),   # 趋势
        ("STAT_ARB",    0.25),   # performance attribution → 统计
        ("ATRSTOP",     0.20),
    ],
    # ── 商品 ──
    "commodity_research_team": [
        ("DONCHIAN",    0.35),   # supply/cycle → 长周期趋势
        ("MULTIFACTOR", 0.25),   # demand analyst
        ("MACD",        0.20),   # 动量
        ("ATRSTOP",     0.20),   # 风控
    ],
}


# ══════════════════════════════════════════════════════════════
# 预设加载
# ══════════════════════════════════════════════════════════════

def list_swarm_presets() -> List[Dict[str, str]]:
    """列出所有可用的 Swarm 预设"""
    if not _PRESETS_DIR.exists():
        return []
    results = []
    for path in sorted(_PRESETS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            results.append({
                "name": data.get("name", path.stem),
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "agent_count": len(data.get("agents", [])),
                "task_count": len(data.get("tasks", [])),
            })
        except Exception:
            pass
    return results


def load_preset(name: str) -> dict:
    """加载指定 Swarm 预设"""
    path = _PRESETS_DIR / f"{name}.yaml"
    if not path.exists():
        available = [p.stem for p in _PRESETS_DIR.glob("*.yaml")]
        raise FileNotFoundError(f"预设 {name!r} 不存在。可用: {available}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════
# SwarmVoteStrategy — Swarm 多 Agent 投票策略
# ══════════════════════════════════════════════════════════════

class SwarmVoteStrategy(Strategy):
    """
    Swarm 多 Agent 投票策略。

    读取 Vibe-Trading 的 Swarm 预设，将其中的 Agent 角色映射到
    本地策略，然后加权投票聚合信号。

    相比原来的 MultiStrategyVote（固定 RSI+MACD+BOLL），SwarmVote
    的优势：
      - 29 种预设覆盖不同市场场景
      - 4~8 个 Agent 角色并行评估（而非只有 3 个）
      - 预设级权重由领域专家设计
      - 支持在预设之间切换

    参数：
      - preset_name: Swarm 预设名（如 "crypto_trading_desk"）
      - threshold:   投票阈值（默认 0.25）
    """

    def __init__(
        self,
        config: Optional[StrategyConfig] = None,
        preset_name: str = "crypto_trading_desk",
        threshold: float = 0.25,
    ):
        super().__init__(config)
        self.preset_name = preset_name
        self.threshold = threshold
        self.name = f"Swarm:{preset_name}"

        # 加载预设
        self._preset = None
        self._worker_strategies: List[Tuple[Strategy, float]] = []

        try:
            self._preset = load_preset(preset_name)
            self._worker_strategies = self._build_workers()
            logger.info(
                f"SwarmVote({preset_name}): {len(self._worker_strategies)} 个 Worker 策略就绪"
            )
        except Exception as e:
            logger.warning(f"Swarm 预设 {preset_name} 加载失败: {e}。回退到默认 VOTE。")
            # 回退：标准三策略投票
            self._worker_strategies = [
                (_build_local_strategy("RSI", config, rsi_period=14, oversold=30.0, overbought=65.0), 0.4),
                (_build_local_strategy("MACD", config), 0.3),
                (_build_local_strategy("BOLLINGER", config, period=20, std_dev=2.0), 0.3),
            ]
            self.name = "Swarm:fallback-VOTE"

    def _build_workers(self) -> List[Tuple[Strategy, float]]:
        """根据预设构建 Worker 策略列表"""
        # 先尝试预设级覆盖
        if self.preset_name in _PRESET_ROLE_OVERRIDES:
            overrides = _PRESET_ROLE_OVERRIDES[self.preset_name]
            strategies: List[Tuple[Strategy, float]] = []
            for strategy_name, weight in overrides:
                try:
                    # 为每个 worker 创建不同的参数变体以避免重复
                    variant = len(strategies)
                    kwargs = {}
                    if strategy_name == "RSI":
                        kwargs = {"rsi_period": 14 - variant * 2, "oversold": 28.0 + variant * 2, "overbought": 65.0 + variant * 2}
                    elif strategy_name == "BOLLINGER":
                        kwargs = {"period": 18 + variant * 2, "std_dev": 2.0 + variant * 0.2}
                    elif strategy_name == "DONCHIAN":
                        kwargs = {"channel_period": 18 + variant * 4, "trend_ema_period": 40 + variant * 20}
                    s = _build_local_strategy(strategy_name, self.config, **kwargs)
                    strategies.append((s, weight))
                except Exception as e:
                    logger.debug(f"Worker {strategy_name} 构建跳过: {e}")
            if strategies:
                return self._normalize_weights(strategies)

        # 回退：根据 Agent 角色映射
        agents = self._preset.get("agents", []) if self._preset else []
        role_strategies: Dict[str, float] = {}

        for agent in agents:
            role = agent.get("role", "").lower()
            # 关键词匹配
            matched = False
            for keyword, (strat_name, base_weight, _) in _ROLE_STRATEGY_MAP.items():
                if keyword in role:
                    role_strategies.setdefault(strat_name, 0.0)
                    role_strategies[strat_name] += base_weight
                    matched = True
                    break
            if not matched:
                role_strategies.setdefault("VOTE", 0.0)
                role_strategies["VOTE"] += 0.25

        if not role_strategies:
            role_strategies = {"VOTE": 1.0}

        strategies: List[Tuple[Strategy, float]] = []
        for strat_name, weight in role_strategies.items():
            try:
                s = _build_local_strategy(strat_name, self.config)
                strategies.append((s, weight))
            except Exception:
                pass

        return self._normalize_weights(strategies)

    @staticmethod
    def _normalize_weights(strategies: List[Tuple[Strategy, float]]) -> List[Tuple[Strategy, float]]:
        """归一化权重到总和 1.0"""
        total = sum(w for _, w in strategies)
        if abs(total) < 1e-8:
            return strategies
        return [(s, w / total) for s, w in strategies]

    # ── 指标填充 ────────────────────────────────────────────

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        """聚合所有 Worker 策略的指标"""
        result: Dict[str, List[float]] = {}
        for i, (strategy, _) in enumerate(self._worker_strategies):
            try:
                inds = strategy.populate_indicators(candles)
                for k, v in inds.items():
                    result[f"w{i}.{k}"] = v
            except Exception:
                pass
        return result

    # ── 入场投票 ────────────────────────────────────────────

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        """Swarm 加权投票入场信号"""
        n = len(candles)
        votes = [0.0] * n

        for strategy, weight in self._worker_strategies:
            try:
                sigs = strategy.populate_entry_trend(candles)
                for i in range(min(n, len(sigs))):
                    if isinstance(sigs[i], Signal):
                        votes[i] += sigs[i].value * weight
                    else:
                        votes[i] += int(sigs[i]) * weight
            except Exception:
                continue

        return [
            Signal.BUY if v > self.threshold else
            (Signal.SELL if v < -self.threshold else Signal.HOLD)
            for v in votes
        ]

    # ── 出场投票 ────────────────────────────────────────────

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        """Swarm 加权投票出场信号"""
        n = len(candles)
        votes = [0.0] * n

        for strategy, weight in self._worker_strategies:
            try:
                try:
                    sigs = strategy.populate_exit_trend(candles)
                except Exception:
                    sigs = strategy.populate_entry_trend(candles)
                    sigs = [-int(s) if not isinstance(s, Signal) else -s.value for s in sigs]

                for i in range(min(n, len(sigs))):
                    if isinstance(sigs[i], Signal):
                        votes[i] += sigs[i].value * weight
                    else:
                        votes[i] += int(sigs[i]) * weight
            except Exception:
                continue

        return [
            Signal.SELL if v > self.threshold else
            (Signal.BUY if v < -self.threshold else Signal.HOLD)
            for v in votes
        ]

    # ── 带置信度的投票（兼容 MultiStrategyVote 接口） ───────

    def populate_signals_with_confidence(
        self, candles: List[Dict]
    ) -> Tuple[List[int], List[float]]:
        """返回 (signals, confidences) 元组"""
        n = len(candles)
        votes = [0.0] * n

        for strategy, weight in self._worker_strategies:
            try:
                entry = strategy.populate_entry_trend(candles)
                try:
                    exit_s = strategy.populate_exit_trend(candles)
                except Exception:
                    exit_s = [-s if not isinstance(s, Signal) else -s.value for s in entry]

                for i in range(min(n, len(entry))):
                    e_sig = entry[i].value if isinstance(entry[i], Signal) else int(entry[i])
                    x_sig = exit_s[i].value if isinstance(exit_s[i], Signal) else int(exit_s[i])
                    combined = e_sig if abs(e_sig) > abs(x_sig) else x_sig
                    votes[i] += combined * weight
            except Exception:
                continue

        signals = []
        confidences = []
        for v in votes:
            if v > self.threshold:
                signals.append(Signal.BUY)
            elif v < -self.threshold:
                signals.append(Signal.SELL)
            else:
                signals.append(Signal.HOLD)
            conf = min(1.0, abs(v) / max(self.threshold * 2, 0.25))
            confidences.append(round(conf, 3))

        return signals, confidences


# ══════════════════════════════════════════════════════════════
# 便捷工厂函数
# ══════════════════════════════════════════════════════════════

def create_swarm_strategy(
    preset_name: str,
    config: StrategyConfig,
    threshold: float = 0.25,
) -> SwarmVoteStrategy:
    """
    根据预设名创建 SwarmVoteStrategy 实例。

    适合在 live_trading.py 的 _build_strategy 中调用。

    Args:
        preset_name: Swarm 预设名
        config:      策略配置
        threshold:   投票阈值

    Returns:
        SwarmVoteStrategy 实例
    """
    return SwarmVoteStrategy(
        config=config,
        preset_name=preset_name,
        threshold=threshold,
    )