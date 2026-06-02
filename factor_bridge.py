"""
factor_bridge.py — Vibe-Trading 因子库桥接模块
================================================

将 Vibe-Trading 的 Alpha Zoo 因子库（~456 个因子）适配到本地交易系统的
Strategy 接口，使得本地 Agent 可以直接使用学术级因子信号。

因子来源：
  - alpha101:   102 个 WorldQuant Alpha101 因子
  - gtja191:    192 个国泰君安 191 因子
  - qlib158:    155 个微软 Qlib 因子
  - academic:    7 个 Fama-French/Carhart 因子

使用方式：
  from factor_bridge import FactorSignalStrategy, list_available_factors

  # 列出所有可用因子
  factors = list_available_factors()

  # 创建因子策略（选择 3~5 个因子做多因子投票）
  strategy = FactorSignalStrategy(
      config=StrategyConfig(symbol="BTC/USDT", timeframe="4h"),
      factor_ids=["alpha101.alpha_001", "gtja191.alpha_006", "qlib158.alpha_003"],
      threshold=0.5,   # 因子信号阈值
  )
"""

from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# Vibe-Trading 源路径设置
# ══════════════════════════════════════════════════════════════

_VT_ROOT = Path("/root/Vibe-Trading/agent")
if str(_VT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VT_ROOT))

_FACTOR_AVAILABLE = False
try:
    import pandas as pd
    from src.factors.base import AlphaCompute, Market as VTMarket
    from src.factors.registry import Registry, get_default_registry
    _FACTOR_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Vibe-Trading 因子库加载失败: {e}。因子策略将不可用。")

# ══════════════════════════════════════════════════════════════
# 本地策略接口（延迟导入，避免循环引用）
# ══════════════════════════════════════════════════════════════

from strategies import Strategy, Signal, StrategyConfig


# ══════════════════════════════════════════════════════════════
# K线适配器：List[Dict] → pd.DataFrame
# ══════════════════════════════════════════════════════════════

def candles_to_dataframe(
    candles: List[Dict],
    symbol: str = "",
) -> pd.DataFrame:
    """
    将本地系统的 K线数据转换为 Vibe-Trading 因子库期望的宽格式 DataFrame。

    Args:
        candles: 本地系统 K线列表，每项含 open/high/low/close/volume
        symbol: 标的名称（用于列名）

    Returns:
        以时间为索引、标的为列名的宽格式 DataFrame（单列）。
        包含 open/high/low/close/volume/vwap 面板。
    """
    if not _FACTOR_AVAILABLE:
        raise RuntimeError("因子库不可用（缺少 pandas 或 Vibe-Trading 因子模块）")

    df = pd.DataFrame(candles)

    # 时间戳处理
    if "timestamp" in df.columns:
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    elif "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
    else:
        df["datetime"] = pd.date_range(
            start="2020-01-01", periods=len(df), freq="4h", tz="UTC"
        )

    df = df.set_index("datetime").sort_index()

    # 构建 OHLCV 面板：每个字段是一个 DataFrame（index=时间, columns=标的）
    col_name = symbol or "ASSET"
    panels: Dict[str, pd.DataFrame] = {}
    for field in ["open", "high", "low", "close", "volume"]:
        if field in df.columns:
            panels[field] = df[[field]].rename(columns={field: col_name})
        else:
            # 回退：用 close 近似
            if "close" in df.columns and field != "volume":
                panels[field] = df[["close"]].rename(columns={"close": col_name})
            elif field == "volume":
                panels[field] = pd.DataFrame(
                    0.0, index=df.index, columns=[col_name]
                )

    # vwap = (high+low+close)/3
    if all(f in panels for f in ["high", "low", "close"]):
        panels["vwap"] = (
            panels["high"] + panels["low"] + panels["close"]
        ) / 3.0
    else:
        panels["vwap"] = panels.get("close", pd.DataFrame(0.0, index=df.index, columns=[col_name]))

    return panels


def factor_to_signal_series(
    factor_values: pd.DataFrame,
    threshold: float = 0.5,
    lookback: int = 3,
) -> List[int]:
    """
    将因子值时间序列转换为交易信号。

    逻辑：
      1. 对因子值做 z-score 标准化
      2. z > +threshold → BUY（1）
      3. z < -threshold → SELL（-1）
      4. 中间 → HOLD（0）
      5. 加入 lookback 平滑：连续 N 根同向才触发

    Args:
        factor_values: 因子值 DataFrame（单列）
        threshold: z-score 阈值
        lookback: 连续确认根数

    Returns:
        List[int]: 与输入等长的 [-1, 0, 1] 信号列表
    """
    vals = factor_values.iloc[:, 0].values.astype(float)
    n = len(vals)

    if n < lookback + 5:
        return [Signal.HOLD] * n

    # z-score 标准化（用滚动窗口避免未来信息泄露）
    rolling_mean = pd.Series(vals).rolling(window=20, min_periods=5).mean().values
    rolling_std = pd.Series(vals).rolling(window=20, min_periods=5).std().values
    # 避免除零
    rolling_std = np.where(rolling_std < 1e-8, 1e-8, rolling_std)
    z_scores = (vals - rolling_mean) / rolling_std

    # 将 z-score NaN 的前段设为 0
    z_scores = np.nan_to_num(z_scores, nan=0.0)

    # 生成原始信号
    raw_signals = np.where(z_scores > threshold, Signal.BUY,
                  np.where(z_scores < -threshold, Signal.SELL, Signal.HOLD))

    # lookback 平滑：连续 N 根同向且最后一根是同向才确认
    signals = [Signal.HOLD] * n
    for i in range(lookback - 1, n):
        window = raw_signals[i - lookback + 1 : i + 1]
        if all(s == Signal.BUY for s in window):
            signals[i] = Signal.BUY
        elif all(s == Signal.SELL for s in window):
            signals[i] = Signal.SELL
        else:
            signals[i] = Signal.HOLD

    return signals


# ══════════════════════════════════════════════════════════════
# 因子注册表缓存
# ══════════════════════════════════════════════════════════════

_factor_cache: Dict[str, AlphaCompute] = {}


_registry_singleton: Optional["Registry"] = None


def _get_registry() -> "Registry":
    """获取因子注册表单例"""
    global _registry_singleton
    if _registry_singleton is None and _FACTOR_AVAILABLE:
        _registry_singleton = get_default_registry()
    return _registry_singleton


def _load_factor(factor_id: str) -> AlphaCompute:
    """加载单个因子计算模块（带缓存）"""
    if factor_id in _factor_cache:
        return _factor_cache[factor_id]

    if not _FACTOR_AVAILABLE:
        raise RuntimeError("因子库不可用")

    try:
        registry = _get_registry()
        alpha = registry.get(factor_id)
        if alpha is None:
            raise ValueError(f"因子 {factor_id} 未找到")
        _factor_cache[factor_id] = alpha
        return alpha
    except Exception as e:
        logger.error(f"加载因子 {factor_id} 失败: {e}")
        raise


def list_available_factors() -> List[Dict[str, str]]:
    """列出所有可用因子"""
    if not _FACTOR_AVAILABLE:
        return []
    try:
        registry = _get_registry()
        alpha_ids = registry.list()
        result = []
        for aid in alpha_ids[:100]:
            try:
                alpha = registry.get(aid)
                result.append({
                    "id": aid,
                    "zoo": alpha.zoo if alpha else "",
                    "module": "",
                    "description": str(alpha.meta.get("description", "")) if alpha and alpha.meta else "",
                })
            except Exception:
                result.append({"id": aid, "zoo": "", "module": "", "description": ""})
        return result
    except Exception as e:
        logger.warning(f"列出因子失败: {e}")
        return []


# ══════════════════════════════════════════════════════════════
# FactorSignalStrategy — 因子信号策略（继承本地 Strategy）
# ══════════════════════════════════════════════════════════════

class FactorSignalStrategy(Strategy):
    """
    多因子信号策略。

    使用 Vibe-Trading Alpha Zoo 中的多个因子计算信号，
    然后加权投票聚合为最终买卖信号。

    参数：
      - factor_ids: 因子 ID 列表，如 ["alpha101.alpha_042", "gtja191.alpha_006"]
      - threshold:  z-score 信号阈值（默认 0.5）
      - lookback:   连续确认 K线数（默认 3）
      - weights:    各因子权重（默认等权）
    """

    def __init__(
        self,
        config: Optional[StrategyConfig] = None,
        factor_ids: Optional[List[str]] = None,
        threshold: float = 0.5,
        lookback: int = 3,
        weights: Optional[List[float]] = None,
    ):
        super().__init__(config)
        self.factor_ids = factor_ids or ["alpha101.alpha_001"]
        self.threshold = threshold
        self.lookback = lookback

        # 权重归一化
        if weights and len(weights) == len(self.factor_ids):
            total = sum(weights)
            self.weights = [w / total for w in weights]
        else:
            n = len(self.factor_ids)
            self.weights = [1.0 / n] * n

        # 预加载因子
        if _FACTOR_AVAILABLE:
            for fid in self.factor_ids:
                try:
                    _load_factor(fid)
                except Exception:
                    logger.warning(f"因子 {fid} 预加载失败，将在运行时动态加载")

    # ── 指标填充 ────────────────────────────────────────────

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        """
        计算所有选中的因子值。

        Returns:
            Dict，key 为因子 ID，value 为与 candles 等长的因子值列
        """
        if not _FACTOR_AVAILABLE or len(candles) < 20:
            return {}

        result: Dict[str, List[float]] = {}
        try:
            panel = candles_to_dataframe(candles, self.config.symbol)
        except Exception as e:
            logger.warning(f"K线转 DataFrame 失败: {e}")
            return {}

        for fid in self.factor_ids:
            try:
                registry = _get_registry()
                factor_df = registry.compute(fid, panel)
                if isinstance(factor_df, pd.DataFrame) and len(factor_df.columns) > 0:
                    vals = factor_df.iloc[:, 0].tolist()
                    # 前向填充 NaN（计算窗口不够时）
                    vals = pd.Series(vals).ffill().fillna(0.0).tolist()
                    # 对齐长度
                    if len(vals) < len(candles):
                        vals = [0.0] * (len(candles) - len(vals)) + vals
                    elif len(vals) > len(candles):
                        vals = vals[-len(candles):]
                    result[fid] = vals
            except Exception as e:
                logger.warning(f"计算因子 {fid} 失败: {e}")
                result[fid] = [0.0] * len(candles)

        return result

    # ── 入场信号 ────────────────────────────────────────────

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        """加权投票买入信号"""
        if not _FACTOR_AVAILABLE or len(candles) < 20:
            return [Signal.HOLD] * len(candles)

        n = len(candles)
        votes = [0.0] * n

        try:
            panel = candles_to_dataframe(candles, self.config.symbol)
        except Exception:
            return [Signal.HOLD] * n

        for fid, weight in zip(self.factor_ids, self.weights):
            try:
                registry = _get_registry()
                factor_df = registry.compute(fid, panel)
                signals = factor_to_signal_series(
                    factor_df, self.threshold, max(1, self.lookback)
                )
                # 对齐长度
                if len(signals) < n:
                    signals = [Signal.HOLD] * (n - len(signals)) + signals
                elif len(signals) > n:
                    signals = signals[-n:]
                for i in range(n):
                    votes[i] += signals[i] * weight
            except Exception as e:
                logger.debug(f"因子 {fid} 信号计算跳过: {e}")

        # 信号阈值判定
        return [
            Signal.BUY if v > 0.25 else (Signal.SELL if v < -0.25 else Signal.HOLD)
            for v in votes
        ]

    # ── 出场信号 ────────────────────────────────────────────

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        """
        出场信号——取入场信号的反向。
        注意：因子策略的入场和出场对称为佳。
        """
        entry = self.populate_entry_trend(candles)
        return [-s for s in entry]
