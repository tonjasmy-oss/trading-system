"""
components/ — 三省六部重构产物

signal_engine.py  — 中书省（信号生成）
position_manager.py — 尚书省（持仓管理）

保留原始模块（live_trading.py / menxia_sheng.py / shangshu_sheng.py）
待新架构稳定后替代。
"""

from .signal_engine import (
    SignalEngine,
    Signal,
    BaseStrategy,
    RSIStrategy,
    SMAcrossStrategy,
    MACDStrategy,
    BollingerBandsStrategy,
    MultiStrategyVote,
    FormulaStrategy,
    AISignalFilter,
    AIModel,
    compute_rsi,
    BUILTIN_FORMULAS,
)

from .position_manager import (
    PositionManager,
    Position,
    PnLResult,
)
