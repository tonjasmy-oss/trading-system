"""
layered_strategy.py — 分层策略架构
====================================

借鉴 QuantDinger IndicatorStrategy / ScriptStrategy 分层设计，
提供「指标层 → 信号层 → 风险层」三层分离的策略基类。

三层职责：
  1. 指标层 (compute_indicators)  — 计算技术指标（EMA/RSI/Bollinger/Donchian...）
  2. 信号层 (generate_signals)    — 基于指标生成 buy/sell 信号
  3. 风险层 (risk_config)         — 声明止损/止盈/仓位/方向（独立于信号逻辑）

优势：
  - 策略代码更清晰，3层各司其职
  - AI 生成策略时只需填充信号层和风险层
  - 风险配置与信号逻辑解耦，方便参数优化
  - 完全兼容现有 BacktestEngine 和 live_trading.py

使用示例：

    class RsiLayeredStrategy(LayeredStrategy):
        '''RSI 超卖反弹策略 — 分层实现'''

        risk_config = StrategyRisk(
            stop_loss=0.02, take_profit=0.04, capital_pct=1.0
        )

        def __init__(self, config=None, rsi_period=14, oversold=30, overbought=65):
            super().__init__(config)
            self.rsi_period = rsi_period
            self.oversold = oversold
            self.overbought = overbought

        def compute_indicators(self, candles):
            closes = [c["close"] for c in candles]
            return {"rsi": self.RSI(closes, self.rsi_period)}

        def generate_signals(self, candles, indicators):
            rsi = indicators["rsi"]
            n = len(candles)
            entry = [0] * n
            exit_ = [0] * n
            for i in range(1, n):
                if rsi[i-1] <= self.oversold and rsi[i] > self.oversold:
                    entry[i] = Signal.BUY
                elif rsi[i-1] >= self.overbought and rsi[i] < self.overbought:
                    exit_[i] = Signal.SELL
            return entry, exit_
"""

import os
import sys
import logging
from abc import abstractmethod
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import Strategy, StrategyConfig, Signal

logger = logging.getLogger(__name__)


# ============================================================
# 风险层数据类
# ============================================================

@dataclass
class StrategyRisk:
    """
    策略风险配置 — 与信号逻辑完全解耦
    
    继承自 QuantDinger 的 # @strategy 元数据声明的设计理念。
    每个策略类声明自己的风险偏好，回测引擎和实盘引擎自动读取。
    """
    stop_loss: float = 0.03          # 止损比例 (e.g. 0.03 = 3%)
    take_profit: float = 0.06        # 止盈比例
    capital_pct: float = 1.0         # 每次下单资金占比
    max_holding_hours: int = 72      # 最大持仓时间（小时）
    trade_direction: str = "long"    # 交易方向: "long" | "short" | "both"
    max_daily_trades: int = 10       # 单日最大交易次数
    commission_pct: float = 0.001    # 手续费率
    slippage_pct: float = 0.0005     # 滑点率
    
    # 元数据（供 AI 生成和前端展示）
    name: str = ""                   # 策略名称
    description: str = ""            # 策略描述
    suitable_regimes: List[str] = field(default_factory=list)  # 适合的市场状态
    version: str = "1.0"

    def to_strategy_config(self, symbol: str = "", timeframe: str = "") -> StrategyConfig:
        """转换为现有 StrategyConfig（向后兼容）"""
        return StrategyConfig(
            symbol=symbol,
            timeframe=timeframe,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            capital_pct=self.capital_pct,
            commission_pct=self.commission_pct,
            slippage_pct=self.slippage_pct,
            trade_direction=self.trade_direction,
        )

    def to_dict(self) -> dict:
        return {
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "capital_pct": self.capital_pct,
            "max_holding_hours": self.max_holding_hours,
            "trade_direction": self.trade_direction,
            "max_daily_trades": self.max_daily_trades,
            "name": self.name,
            "description": self.description,
            "suitable_regimes": self.suitable_regimes,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyRisk":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ============================================================
# LayeredStrategy 基类
# ============================================================

class LayeredStrategy(Strategy):
    """
    三层分离策略基类
    
    子类只需实现两个方法：
      - compute_indicators(candles) → Dict[str, List[float]]
      - generate_signals(candles, indicators) → Tuple[List[int], List[int]]
    
    和一个类属性：
      - risk_config: StrategyRisk
    
    populate_indicators / populate_entry_trend / populate_exit_trend
    由基类自动生成，完全兼容现有 BacktestEngine 和 live_trading.py。
    """

    # 子类覆盖此类属性声明风险配置
    risk_config: StrategyRisk = StrategyRisk()

    def __init__(self, config: Optional[StrategyConfig] = None):
        # 如果子类声明了 risk_config，用它覆盖 config 的默认值
        if config is None:
            config = self.risk_config.to_strategy_config()
        else:
            # 用 risk_config 中非默认值覆盖 config
            rc = self.risk_config
            if rc.stop_loss != StrategyRisk.stop_loss:
                config.stop_loss = rc.stop_loss
            if rc.take_profit != StrategyRisk.take_profit:
                config.take_profit = rc.take_profit
            if rc.capital_pct != StrategyRisk.capital_pct:
                config.capital_pct = rc.capital_pct
            if rc.trade_direction != StrategyRisk.trade_direction:
                config.trade_direction = rc.trade_direction
        super().__init__(config)
        self._cached_indicators: Optional[Dict[str, List[float]]] = None

    # ── 子类必须实现 ──

    @abstractmethod
    def compute_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        """
        指标层：计算所有技术指标
        
        Args:
            candles: OHLCV 列表
            
        Returns:
            dict，键为指标名，值为与 candles 等长的列表
            例如: {"rsi": [...], "ema20": [...], "upper_bb": [...], "lower_bb": [...]}
        """
        ...

    @abstractmethod
    def generate_signals(
        self, candles: List[Dict], indicators: Dict[str, List[float]]
    ) -> Tuple[List[int], List[int]]:
        """
        信号层：基于指标生成买卖信号
        
        Args:
            candles:    OHLCV 列表
            indicators: compute_indicators 的返回值
            
        Returns:
            (entry_signals, exit_signals) — 两个与 candles 等长的列表
            entry:  Signal.BUY (1), Signal.HOLD (0)
            exit_:  Signal.SELL (-1), Signal.HOLD (0)
        """
        ...

    # ── 自动生成的 populate_* 方法 ──

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        """自动委托给 compute_indicators（带缓存）"""
        if self._cached_indicators is not None and len(self._cached_indicators.get(
            list(self._cached_indicators.keys())[0] if self._cached_indicators else "", []
        )) == len(candles):
            return self._cached_indicators
        self._cached_indicators = self.compute_indicators(candles)
        self._indicators = self._cached_indicators
        return self._cached_indicators

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        """自动委托给 generate_signals"""
        indicators = self.populate_indicators(candles)
        entry, _ = self.generate_signals(candles, indicators)
        return entry

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        """自动委托给 generate_signals"""
        indicators = self.populate_indicators(candles)
        _, exit_ = self.generate_signals(candles, indicators)
        return exit_

    # ── 元数据 ──

    def get_risk(self) -> StrategyRisk:
        """返回风险配置"""
        return self.risk_config

    def get_metadata(self) -> dict:
        """返回策略元数据（供 AI 和前端使用）"""
        return {
            "class": self.__class__.__name__,
            "risk": self.risk_config.to_dict(),
            "description": self.__class__.__doc__ or "",
        }

    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        """
        计算最新信号 — 兼容 Strategy.compute 签名
        Returns: (signal_val, indicator_val, prev_indicator_val)
        """
        indicators = self.populate_indicators(candles)
        entry, exit_ = self.generate_signals(candles, indicators)
        last_entry = entry[-1] if entry else Signal.HOLD
        last_exit = exit_[-1] if exit_ else Signal.HOLD

        # 返回 RSI 近似值作为指标值
        rsi = indicators.get("rsi", [50.0] * len(candles))
        current_val = rsi[-1] if rsi else 50.0
        prev_val = rsi[-2] if len(rsi) > 1 else 50.0

        if isinstance(last_exit, Signal) and last_exit == Signal.SELL:
            return Signal.SELL.value, current_val, prev_val
        if isinstance(last_entry, Signal) and last_entry == Signal.BUY:
            return Signal.BUY.value, current_val, prev_val
        return Signal.HOLD.value, current_val, prev_val


# ============================================================
# PandasLayeredStrategy — 基于 DataFrame 的分层策略
# ============================================================

class PandasLayeredStrategy(LayeredStrategy):
    """
    基于 Pandas DataFrame 的分层策略
    
    更接近 QuantDinger IndicatorStrategy 的 API：
      - 接收 pd.DataFrame 而非 list[dict]
      - 指标和信号直接在 DataFrame 上操作
    
    子类实现:
      - compute_indicators_df(df) → df (添加指标列)
      - generate_signals_df(df) → df (添加 buy/sell 列)
    """

    @abstractmethod
    def compute_indicators_df(self, df: "pd.DataFrame") -> "pd.DataFrame":
        """
        指标层 (DataFrame 版本) — 在 df 上添加指标列
        """
        ...

    @abstractmethod
    def generate_signals_df(self, df: "pd.DataFrame") -> "pd.DataFrame":
        """
        信号层 (DataFrame 版本) — 在 df 上添加 buy/sell 列
        """
        ...

    def _candles_to_df(self, candles: List[Dict]) -> "pd.DataFrame":
        import pandas as pd
        df = pd.DataFrame(candles)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def _df_to_signals(self, df: "pd.DataFrame", n: int) -> Tuple[List[int], List[int]]:
        entry = [Signal.HOLD] * n
        exit_ = [Signal.HOLD] * n
        if "buy" in df.columns:
            for i in range(n):
                if df["buy"].iloc[i]:
                    entry[i] = Signal.BUY
        if "sell" in df.columns:
            for i in range(n):
                if df["sell"].iloc[i]:
                    exit_[i] = Signal.SELL
        return entry, exit_

    def _df_to_indicators(self, df: "pd.DataFrame") -> Dict[str, List[float]]:
        indicators = {}
        for col in df.columns:
            if col not in ("timestamp", "open", "high", "low", "close", "volume", "buy", "sell"):
                try:
                    indicators[col] = df[col].fillna(0.0).tolist()
                except Exception:
                    pass
        return indicators

    def compute_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        df = self._candles_to_df(candles)
        df = self.compute_indicators_df(df)
        return self._df_to_indicators(df)

    def generate_signals(self, candles, indicators) -> Tuple[List[int], List[int]]:
        df = self._candles_to_df(candles)
        # 添加指标列
        for name, values in indicators.items():
            df[name] = values
        df = self.generate_signals_df(df)
        return self._df_to_signals(df, len(candles))


# ============================================================
# 示例策略
# ============================================================

class RsiLayeredStrategy(LayeredStrategy):
    """
    RSI 超卖反弹策略 — 分层实现示例
    
    规则：
      - RSI 从超卖区上穿 → BUY
      - RSI 从超买区下穿 → SELL
    """

    risk_config = StrategyRisk(
        stop_loss=0.02,
        take_profit=0.04,
        capital_pct=1.0,
        name="RSI Layered",
        description="RSI 超卖反弹策略（分层实现）",
        suitable_regimes=["downtrend", "ranging"],
    )

    def __init__(self, config=None, rsi_period=14, oversold=28, overbought=65):
        super().__init__(config)
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

    def compute_indicators(self, candles):
        closes = [c["close"] for c in candles]
        return {
            "rsi": self.RSI(closes, self.rsi_period),
            "close": closes,
        }

    def generate_signals(self, candles, indicators):
        rsi = indicators["rsi"]
        n = len(candles)
        entry = [Signal.HOLD] * n
        exit_ = [Signal.HOLD] * n

        for i in range(1, n):
            if rsi[i] == 0 or rsi[i-1] == 0:
                continue
            # RSI 上穿超卖线 → BUY
            if rsi[i-1] <= self.oversold and rsi[i] > self.oversold:
                entry[i] = Signal.BUY
            # RSI 下穿超买线 → SELL
            elif rsi[i-1] >= self.overbought and rsi[i] < self.overbought:
                exit_[i] = Signal.SELL

        return entry, exit_


class EmaCrossLayeredStrategy(LayeredStrategy):
    """
    EMA 金叉/死叉策略 — 分层实现示例
    
    规则：
      - 快线 EMA 上穿慢线 EMA → BUY
      - 快线 EMA 下穿慢线 EMA → SELL
    """

    risk_config = StrategyRisk(
        stop_loss=0.025,
        take_profit=0.05,
        capital_pct=1.0,
        name="EMA Cross Layered",
        description="EMA 金叉死叉策略（分层实现）",
        suitable_regimes=["uptrend", "downtrend"],
    )

    def __init__(self, config=None, fast_period=10, slow_period=30):
        super().__init__(config)
        self.fast_period = fast_period
        self.slow_period = slow_period

    def compute_indicators(self, candles):
        closes = [c["close"] for c in candles]
        return {
            "ema_fast": self.EMA(closes, self.fast_period),
            "ema_slow": self.EMA(closes, self.slow_period),
            "close": closes,
        }

    def generate_signals(self, candles, indicators):
        fast = indicators["ema_fast"]
        slow = indicators["ema_slow"]
        n = len(candles)
        entry = [Signal.HOLD] * n
        exit_ = [Signal.HOLD] * n

        for i in range(1, n):
            if fast[i] == 0 or slow[i] == 0 or fast[i-1] == 0 or slow[i-1] == 0:
                continue
            # 金叉：快线上穿慢线
            if fast[i-1] <= slow[i-1] and fast[i] > slow[i]:
                entry[i] = Signal.BUY
            # 死叉：快线下穿慢线
            elif fast[i-1] >= slow[i-1] and fast[i] < slow[i]:
                exit_[i] = Signal.SELL

        return entry, exit_


# ============================================================
# 注册到策略注册表
# ============================================================

# 将分层策略注册到 STRATEGY_REGISTRY（兼容现有系统）
def register_layered_strategies():
    """动态注册分层策略到现有注册表"""
    try:
        from strategies import STRATEGY_REGISTRY
        STRATEGY_REGISTRY["RSI_LAYERED"] = RsiLayeredStrategy
        STRATEGY_REGISTRY["EMA_CROSS_LAYERED"] = EmaCrossLayeredStrategy
        logger.info("[LayeredStrategy] 已注册到 STRATEGY_REGISTRY")
    except ImportError:
        pass
