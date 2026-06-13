"""
BTC Supertrend 多周期共振策略（BTC/USDT 专用优化）

基于 ATR 的 Supertrend 超级趋势指标，配合 1h/4h/1d 三周期共振过滤假信号。

核心设计：
  - Supertrend 单周期：ATR(10) → avg=(H+L+C)/3 → 上轨/下轨 → 方向切换
  - 多周期共振：至少 2/3 周期同向才开仓（alignment_min=2）
  - 风险管理：每笔风险 1%，止损 8%，止盈 15%，移动止损 6%
  - 仓位动态：按 ATR 波动率反比调整仓位大小

使用方式：
  # 独立回测
  python eth_supertrend_strategy.py --symbol ETH/USDT --capital 10000

  # 通过策略注册表接入主回测引擎
  from strategies import STRATEGY_REGISTRY
  StratClass = STRATEGY_REGISTRY["ETH_SUPERTREND"]
"""

import os
import sys
import json
import math
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("eth_supertrend")

# ── 兼容路径（在 trading-system 根目录运行时能导入 strategies.py）──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies import (
    Strategy, StrategyConfig, Signal,
    compute_atr,
)

# ============================================================
# 枚举与数据结构
# ============================================================

class Direction(Enum):
    """Supertrend 方向"""
    LONG  =  1   # 看多（上轨有效）
    SHORT = -1   # 看空（下轨有效）
    NONE  =  0   # 无方向（数据不足）


class TradeSide(Enum):
    """交易方向"""
    LONG  = "long"
    SHORT = "short"


class ExitReason(Enum):
    """平仓原因"""
    STOP_LOSS     = "SL"
    TAKE_PROFIT   = "TP"
    TRAILING_STOP = "TS"
    SIGNAL_EXIT   = "signal_exit"


@dataclass
class SupertrendResult:
    """单个周期的 Supertrend 计算结果"""
    direction: List[int]          # 1=LONG, -1=SHORT, 0=NONE（与 candles 等长）
    st_value:  List[float]        # Supertrend 值（趋势线，与 candles 等长）
    upper:     List[float]        # 上轨
    lower:     List[float]        # 下轨
    atr:       List[float]        # ATR 值


@dataclass
class MultiTFSignal:
    """多周期共振信号"""
    side:       TradeSide          # 交易方向
    confidence: float              # 共振强度 0~1（同向周期数 / 总周期数）
    triggers:   List[str]          # 触发共振的周期列表，如 ["30m", "4h"]
    direction_counts: Dict[str, int]  # {"long": N, "short": M}


@dataclass
class Trade:
    """单笔交易记录"""
    entry_time:   datetime
    exit_time:    Optional[datetime] = None
    side:         TradeSide = TradeSide.LONG
    entry_price:  float = 0.0
    exit_price:   float = 0.0
    quantity:     float = 0.0
    pnl_pct:      float = 0.0
    pnl_abs:      float = 0.0
    exit_reason:  Optional[ExitReason] = None
    stop_loss:    float = 0.0
    take_profit:  float = 0.0
    trailing_stop: float = 0.0
    entry_atr:    float = 0.0


# ============================================================
# 一、Supertrend 指标计算（单周期）
# ============================================================

def compute_supertrend(
    candles: List[Dict],
    period: int = 10,
    multiplier: float = 3.0,
) -> SupertrendResult:
    """
    计算单周期的 Supertrend 指标。

    算法：
      1. ATR = TR 的 rolling mean（period 周期）
         TR = max(H-L, |H-close[-1]|, |L-close[-1]|)
      2. avg = (H + L + C) / 3
      3. upper = avg + multiplier × ATR
      4. lower = avg - multiplier × ATR
      5. 方向切换：
         - 原多 → 收盘跌破下轨 → 转空
         - 原空 → 收盘突破上轨 → 转多
         - st_value：多时取下轨，空时取上轨

    Args:
        candles:  OHLCV 列表
        period:   ATR 周期（默认 10）
        multiplier: 乘数（默认 3.0）

    Returns:
        SupertrendResult（所有序列与 candles 等长）
    """
    n = len(candles)

    # ── 提取价格序列 ──
    highs   = [c["high"]  for c in candles]
    lows    = [c["low"]   for c in candles]
    closes  = [c["close"] for c in candles]
    opens   = [c["open"]  for c in candles]

    # ── 计算 ATR ──
    atr = compute_atr(candles, period)

    # ── 计算 avg = (H + L + C) / 3 ──
    avg = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]

    # ── 计算上下轨 ──
    upper = [0.0] * n
    lower = [0.0] * n
    for i in range(n):
        if atr[i] > 0:
            upper[i] = avg[i] + multiplier * atr[i]
            lower[i] = avg[i] - multiplier * atr[i]

    # ── 方向判定（经典 Supertrend 逻辑）──
    direction = [0] * n   # 0=NONE, 1=LONG, -1=SHORT
    st_value  = [0.0] * n

    # 找到第一个有效 ATR 的位置
    first_valid = period
    while first_valid < n and atr[first_valid] == 0:
        first_valid += 1

    if first_valid >= n:
        return SupertrendResult(
            direction=direction, st_value=st_value,
            upper=upper, lower=lower, atr=atr,
        )

    # 初始方向：取收盘价更靠近上轨还是下轨（避免固定偏向空头）
    mid = (upper[first_valid] + lower[first_valid]) / 2.0
    if closes[first_valid] >= mid:
        direction[first_valid] = Direction.LONG.value
        st_value[first_valid]  = lower[first_valid]
    else:
        direction[first_valid] = Direction.SHORT.value
        st_value[first_valid]  = upper[first_valid]

    for i in range(first_valid + 1, n):
        prev_dir = direction[i - 1]
        prev_st  = st_value[i - 1]

        if upper[i] == 0 or lower[i] == 0:
            direction[i] = prev_dir
            st_value[i]  = prev_st
            continue

        if prev_dir == Direction.LONG.value:
            # 原趋势为多 → 收盘跌破上一根 Supertrend 值转空（标准 Pine Script 逻辑）
            if closes[i] < prev_st:
                direction[i] = Direction.SHORT.value
                st_value[i]  = upper[i]
            else:
                direction[i] = Direction.LONG.value
                st_value[i]  = max(lower[i], prev_st)
        else:
            # 原趋势为空 → 收盘突破上一根 Supertrend 值转多（标准 Pine Script 逻辑）
            if closes[i] > prev_st:
                direction[i] = Direction.LONG.value
                st_value[i]  = lower[i]
            else:
                direction[i] = Direction.SHORT.value
                st_value[i]  = min(upper[i], prev_st)

    return SupertrendResult(
        direction=direction, st_value=st_value,
        upper=upper, lower=lower, atr=atr,
    )


# ============================================================
# 二、多周期共振机制（MultiTF）
# ============================================================

class MultiTFSupertrend:
    """
    多周期 Supertrend 共振信号生成器。

    参数：
      - timeframes: 周期配置列表 [{"name": "30m", "period": 10, "multiplier": 2.5}, ...]
      - alignment_min: 最少需要多少周期同向（默认 2）
    """

    # 默认三周期配置（基于 BTC/USDT 回测优化，BTC 波动更温和，multiplier 更小）
    DEFAULT_TIMEFRAMES = [
        {"name": "1h", "period": 10, "multiplier": 0.5},    # 短周期灵敏（BTC 1h 波动特性）
        {"name": "4h",  "period": 10, "multiplier": 0.7},   # 中期平衡
        {"name": "1d",  "period": 20, "multiplier": 1.0},   # 长周期稳健
    ]

    def __init__(
        self,
        timeframes: Optional[List[Dict]] = None,
        alignment_min: int = 2,
    ):
        self.timeframes = timeframes or self.DEFAULT_TIMEFRAMES
        self.alignment_min = alignment_min
        self._results: Dict[str, SupertrendResult] = {}

    def compute_all(self, candles_map: Dict[str, List[Dict]]) -> Dict[str, SupertrendResult]:
        """
        计算所有周期的 Supertrend。

        Args:
            candles_map: {tf_name: candles_list}，如 {"30m": [...], "4h": [...], "1d": [...]}

        Returns:
            {tf_name: SupertrendResult}
        """
        self._results = {}
        for tf_cfg in self.timeframes:
            tf_name = tf_cfg["name"]
            if tf_name not in candles_map:
                logger.warning(f"[MultiTF] 缺少 {tf_name} 的K线数据，跳过")
                continue
            self._results[tf_name] = compute_supertrend(
                candles_map[tf_name],
                period=tf_cfg["period"],
                multiplier=tf_cfg["multiplier"],
            )
        return self._results

    def get_signal(self, index_map: Dict[str, int]) -> Optional[MultiTFSignal]:
        """
        获取指定索引位置的多周期共振信号。

        Args:
            index_map: {tf_name: candle_index}，每个周期对应的当前K线索引

        Returns:
            MultiTFSignal 或 None（无法判定时返回 None）
        """
        bulls = 0
        bears = 0
        triggers: List[str] = []

        for tf_cfg in self.timeframes:
            tf_name = tf_cfg["name"]
            if tf_name not in self._results or tf_name not in index_map:
                continue
            idx = index_map[tf_name]
            st = self._results[tf_name]
            if idx < 0 or idx >= len(st.direction):
                continue
            d = st.direction[idx]
            if d == Direction.LONG.value:
                bulls += 1
                triggers.append(tf_name)
            elif d == Direction.SHORT.value:
                bears += 1
                triggers.append(tf_name)
            # d == 0 时忽略

        total_aligned = bulls + bears
        if total_aligned < self.alignment_min:
            return None

        if bulls > bears:
            confidence = bulls / len(self.timeframes)
            return MultiTFSignal(
                side=TradeSide.LONG,
                confidence=confidence,
                triggers=triggers,
                direction_counts={"long": bulls, "short": bears},
            )
        elif bears > bulls:
            confidence = bears / len(self.timeframes)
            return MultiTFSignal(
                side=TradeSide.SHORT,
                confidence=confidence,
                triggers=triggers,
                direction_counts={"long": bulls, "short": bears},
            )
        else:
            # 多空相等（如 1:1 且 alignment_min=2，但 total_aligned=2 满足条件却同票）
            return None


# ============================================================
# 三、风险管理
# ============================================================

class RiskManager:
    """
    仓位与风控计算。

    参数：
      - risk_pct_per_trade: 每笔交易风险占账户比例（默认 2%）
      - stop_loss_pct: 硬止损比例（默认 5%）
      - take_profit_pct: 止盈比例（默认 15%）
      - trailing_stop_pct: 移动止损比例（默认 3%）
      - max_dd_pct: 最大回撤停止阈值（默认 25%）
      - atr_stop_mult: ATR 止损倍数（默认 2.0）
    """

    def __init__(
        self,
        risk_pct_per_trade: float = 0.01,
        stop_loss_pct: float = 0.08,
        take_profit_pct: float = 0.15,
        trailing_stop_pct: float = 0.06,
        max_dd_pct: float = 0.35,
        atr_stop_mult: float = 2.0,
    ):
        self.risk_pct_per_trade = risk_pct_per_trade
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.max_dd_pct = max_dd_pct
        self.atr_stop_mult = atr_stop_mult

    def calc_position_size(
        self,
        capital: float,
        entry_price: float,
        atr: float,
    ) -> float:
        """
        按 ATR 动态计算仓位。

        止损幅度 = min(ATR/入场价 × atr_stop_mult, stop_loss_pct)
        风险金额 = capital × risk_pct_per_trade
        仓位 = 风险金额 / 止损幅度

        Args:
            capital:      当前账户权益
            entry_price:  入场价格
            atr:          当前 ATR 值

        Returns:
            建议仓位（数量，非金额）
        """
        if entry_price <= 0 or capital <= 0:
            return 0.0

        risk_amount = capital * self.risk_pct_per_trade

        # ATR 止损幅度 vs 硬止损，取较小值
        if atr > 0:
            atr_stop_pct = (atr / entry_price) * self.atr_stop_mult
            stop_pct = min(atr_stop_pct, self.stop_loss_pct)
        else:
            stop_pct = self.stop_loss_pct

        if stop_pct <= 0:
            return 0.0

        position = risk_amount / (entry_price * stop_pct)
        return position

    def calc_stop(self, entry_price: float, side: TradeSide) -> float:
        """计算止损价"""
        if side == TradeSide.LONG:
            return entry_price * (1 - self.stop_loss_pct)
        else:
            return entry_price * (1 + self.stop_loss_pct)

    def calc_target(self, entry_price: float, side: TradeSide) -> float:
        """计算止盈价"""
        if side == TradeSide.LONG:
            return entry_price * (1 + self.take_profit_pct)
        else:
            return entry_price * (1 - self.take_profit_pct)

    def calc_trailing_stop(self, high_price: float, side: TradeSide) -> float:
        """计算移动止损价（基于已实现的最高/最低价）"""
        if side == TradeSide.LONG:
            return high_price * (1 - self.trailing_stop_pct)
        else:
            return high_price * (1 + self.trailing_stop_pct)


# ============================================================
# 四、回测引擎
# ============================================================

class SupertrendBacktest:
    """
    Supertrend 多周期共振回测引擎。

    主循环以最短周期（30m）为基准遍历：
      1. 平仓检查（优先级: SL → TP → TS → 更新TS）
      2. 开仓检查（无持仓时，获取多周期信号）
      3. 更新权益和回撤
      4. 最大回撤 25% 停止
    """

    def __init__(
        self,
        candles_map: Dict[str, List[Dict]],
        initial_capital: float = 10000.0,
        risk_manager: Optional[RiskManager] = None,
        multi_tf: Optional[MultiTFSupertrend] = None,
        leverage: float = 3.0,
    ):
        self.candles_map = candles_map
        self.initial_capital = initial_capital
        self.risk_manager = risk_manager or RiskManager()
        self.multi_tf = multi_tf or MultiTFSupertrend()
        self.leverage = leverage

        # ── 基准周期：使用 multi_tf 配置的第一个（最短）周期 ──
        base_tf = self.multi_tf.timeframes[0]["name"]
        if base_tf not in candles_map:
            available = list(candles_map.keys())
            raise ValueError(
                f"candles_map 缺少基准周期 '{base_tf}'，当前: {available}"
            )

        self.base_candles = candles_map[base_tf]
        self.base_n = len(self.base_candles)

        # ── 回测状态 ──
        self.capital: float = initial_capital
        self.peak_capital: float = initial_capital
        self.max_dd: float = 0.0
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = []
        self.current_trade: Optional[Trade] = None
        self.extreme_price: float = 0.0  # 持仓期间的最优价格（多=最高，空=最低）

        # ── 预计算所有周期的 Supertrend ──
        logger.info("[回测] 计算多周期 Supertrend 指标...")
        self.multi_tf.compute_all(candles_map)

        # ── 构建时间映射（30m K线 → 各周期的当前索引）──
        self._build_time_index_map()

    def _build_time_index_map(self):
        """
        为每个 30m K 线建立到其他周期的索引映射。

        规则：对于 30m 的第 i 根K线（时间戳 t），
        其他周期的索引 = 该周期中时间戳 ≤ t 的最大索引。
        """
        self._index_maps: List[Dict[str, int]] = []

        # 预处理各周期的时间戳列表
        tf_timestamps: Dict[str, List[int]] = {}
        for tf_name, candles in self.candles_map.items():
            tf_timestamps[tf_name] = [c.get("timestamp", 0) for c in candles]

        for i in range(self.base_n):
            t = self.base_candles[i].get("timestamp", 0)
            idx_map = {}
            for tf_name, ts_list in tf_timestamps.items():
                # 二分查找 ≤ t 的最大索引
                lo, hi = 0, len(ts_list) - 1
                best = -1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if ts_list[mid] <= t:
                        best = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1
                idx_map[tf_name] = best
            self._index_maps.append(idx_map)

    def run(self) -> Dict:
        """
        执行回测。

        Returns:
            回测统计结果字典
        """
        logger.info(f"[回测] 开始 — 基准K线: {self.base_n} 根, 初始资金: ${self.initial_capital:,.2f}")

        for i in range(self.base_n):
            candle = self.base_candles[i]
            price = candle["close"]

            # ── 步骤1：平仓检查 ──
            if self.current_trade is not None:
                exit_reason = self._check_exit(i, price)
                if exit_reason is not None:
                    self._close_trade(i, price, exit_reason)

            # ── 步骤2：开仓检查 ──
            if self.current_trade is None:
                signal = self.multi_tf.get_signal(self._index_maps[i])
                if signal is not None:
                    self._open_trade(i, price, signal)

            # ── 步骤3：更新权益与回撤 ──
            equity = self._calc_equity(price)
            self.equity_curve.append(equity)
            if equity > self.peak_capital:
                self.peak_capital = equity
            dd = (self.peak_capital - equity) / self.peak_capital if self.peak_capital > 0 else 0
            if dd > self.max_dd:
                self.max_dd = dd

            # ── 步骤4：最大回撤停止 ──
            if dd >= self.risk_manager.max_dd_pct:
                logger.warning(
                    f"[回测] ⛔ 最大回撤 {dd:.1%} 达到阈值 {self.risk_manager.max_dd_pct:.0%}，停止交易"
                )
                if self.current_trade is not None:
                    self._close_trade(i, price, ExitReason.STOP_LOSS)
                break

        # ── 强制平仓（回测结束时仍有持仓）──
        if self.current_trade is not None:
            self._close_trade(self.base_n - 1, self.base_candles[-1]["close"], ExitReason.SIGNAL_EXIT)

        return self._stats()

    def _check_exit(self, i: int, price: float) -> Optional[ExitReason]:
        """按优先级检查平仓条件"""
        trade = self.current_trade

        # 更新极端价格
        if trade.side == TradeSide.LONG:
            if price > self.extreme_price:
                self.extreme_price = price
        else:
            if price < self.extreme_price:
                self.extreme_price = price

        # SL → TP → TS
        if trade.side == TradeSide.LONG:
            if price <= trade.stop_loss:
                return ExitReason.STOP_LOSS
            if price >= trade.take_profit:
                return ExitReason.TAKE_PROFIT
            ts = self.risk_manager.calc_trailing_stop(self.extreme_price, trade.side)
            if price <= ts:
                return ExitReason.TRAILING_STOP
        else:
            if price >= trade.stop_loss:
                return ExitReason.STOP_LOSS
            if price <= trade.take_profit:
                return ExitReason.TAKE_PROFIT
            ts = self.risk_manager.calc_trailing_stop(self.extreme_price, trade.side)
            if price >= ts:
                return ExitReason.TRAILING_STOP

        return None

    def _open_trade(self, i: int, price: float, signal: MultiTFSignal):
        """开仓"""
        # 获取当前 30m 的 ATR
        st_30m = self.multi_tf._results.get("30m")
        atr = 0.0
        if st_30m is not None and i < len(st_30m.atr):
            atr = st_30m.atr[i]

        quantity = self.risk_manager.calc_position_size(self.capital, price, atr)
        if quantity <= 0:
            return

        side = signal.side
        sl = self.risk_manager.calc_stop(price, side)
        tp = self.risk_manager.calc_target(price, side)

        trade = Trade(
            entry_time=self._ts_to_datetime(self.base_candles[i].get("timestamp", 0)),
            side=side,
            entry_price=price,
            quantity=quantity,
            stop_loss=sl,
            take_profit=tp,
            trailing_stop=0.0,
            entry_atr=atr,
        )
        self.current_trade = trade
        self.extreme_price = price

        logger.debug(
            f"[回测] 🔵 开{side.value.upper()} @ {price:.4f} "
            f"qty={quantity:.4f} SL={sl:.4f} TP={tp:.4f} "
            f"触发周期={signal.triggers} 置信度={signal.confidence:.0%}"
        )

    def _close_trade(self, i: int, price: float, reason: ExitReason):
        """平仓并记录"""
        trade = self.current_trade

        if trade.side == TradeSide.LONG:
            pnl_pct = (price - trade.entry_price) / trade.entry_price
        else:
            pnl_pct = (trade.entry_price - price) / trade.entry_price

        pnl_pct *= self.leverage
        pnl_abs = trade.quantity * trade.entry_price * pnl_pct

        trade.exit_time  = self._ts_to_datetime(self.base_candles[i].get("timestamp", 0))
        trade.exit_price = price
        trade.pnl_pct    = pnl_pct * 100
        trade.pnl_abs    = pnl_abs
        trade.exit_reason = reason

        self.capital += pnl_abs
        self.trades.append(trade)
        self.current_trade = None
        self.extreme_price = 0.0

        logger.debug(
            f"[回测] 🔴 平{reason.value} @ {price:.4f} "
            f"pnl={pnl_pct*100:+.2f}% (${pnl_abs:+.2f}) 权益=${self.capital:,.2f}"
        )

    def _calc_equity(self, current_price: float) -> float:
        """计算当前权益（含浮动盈亏）"""
        if self.current_trade is None:
            return self.capital
        trade = self.current_trade
        if trade.side == TradeSide.LONG:
            upnl_pct = (current_price - trade.entry_price) / trade.entry_price * self.leverage
        else:
            upnl_pct = (trade.entry_price - current_price) / trade.entry_price * self.leverage
        return self.capital + trade.quantity * trade.entry_price * upnl_pct

    @staticmethod
    def _ts_to_datetime(ts: int) -> datetime:
        """时间戳 → datetime"""
        if ts > 1e12:  # 毫秒
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    def _stats(self) -> Dict:
        """生成回测统计"""
        n = len(self.trades)
        if n == 0:
            return {"total_trades": 0, "total_return": 0.0, "sharpe_ratio": 0.0}

        wins       = [t for t in self.trades if t.pnl_pct > 0]
        losses     = [t for t in self.trades if t.pnl_pct <= 0]
        win_rate   = len(wins) / n * 100 if n else 0
        total_pnl  = sum(t.pnl_abs for t in self.trades)
        total_return = (self.capital - self.initial_capital) / self.initial_capital * 100

        avg_win  = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(t.pnl_pct for t in wins)) / abs(sum(t.pnl_pct for t in losses)) if losses and sum(t.pnl_pct for t in losses) != 0 else float("inf")

        # 夏普比率（简化：年化）
        returns = [t.pnl_pct for t in self.trades]
        if len(returns) > 1:
            mean_r = sum(returns) / len(returns)
            var_r  = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1) if len(returns) > 1 else 0
            std_r  = math.sqrt(var_r) if var_r > 0 else 0
            sharpe = (mean_r / std_r * math.sqrt(n)) if std_r > 0 else 0
        else:
            sharpe = 0.0

        # 按平仓原因统计
        exit_reasons = {}
        for t in self.trades:
            r = t.exit_reason.value if t.exit_reason else "unknown"
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

        return {
            "initial_capital": self.initial_capital,
            "final_capital": round(self.capital, 2),
            "total_return_pct": round(total_return, 2),
            "total_trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(self.max_dd * 100, 2),
            "exit_reasons": exit_reasons,
            "trades": self.trades,
        }


# ============================================================
# 五、兼容现有 Strategy 基类（接入主回测引擎）
# ============================================================

class BTCSupertrendStrategy(Strategy):
    """
    BTC Supertrend 策略（BTC/USDT 专用） — 兼容 strategies.py 的 Strategy 基类。

    注意：由于基类的回测引擎以单一周期运行，此适配器使用主周期（4h）
    的 Supertrend 单周期信号。完整的多周期共振回测请使用 SupertrendBacktest。

    参数：
      - period:     ATR 周期（默认 10）
      - multiplier: 乘数（默认 3.0）
    """

    def __init__(self, config=None, period=10, multiplier=3.0):
        super().__init__(config)
        self.period = period
        self.multiplier = multiplier

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        st = compute_supertrend(candles, self.period, self.multiplier)
        self._indicators = {
            "direction": [float(d) for d in st.direction],
            "st_value":  st.st_value,
            "upper":     st.upper,
            "lower":     st.lower,
            "atr":       st.atr,
        }
        return self._indicators

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        direction = self._indicators.get("direction", [])
        if not direction:
            self.populate_indicators(candles)
            direction = self._indicators["direction"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            # 由空转多 → 买入
            if direction[i] == Direction.LONG.value and direction[i - 1] != Direction.LONG.value:
                signals[i] = Signal.BUY
        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        direction = self._indicators.get("direction", [])
        if not direction:
            self.populate_indicators(candles)
            direction = self._indicators["direction"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            # 由多转空 → 卖出
            if direction[i] == Direction.SHORT.value and direction[i - 1] != Direction.SHORT.value:
                signals[i] = Signal.SELL
        return signals


# ============================================================
# 六、CLI 入口（独立回测）
# ============================================================

def load_candles_from_cache(symbol: str, timeframes: List[str]) -> Dict[str, List[Dict]]:
    """
    从 ohlcv_cache.db 加载多周期K线数据。

    Args:
        symbol:     交易对，如 "ETH/USDT"
        timeframes: 周期列表，如 ["30m", "4h", "1d"]

    Returns:
        {tf_name: candles_list}
    """
    import sqlite3

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ohlcv_cache", "ohlcv_cache.db")
    if not os.path.exists(db_path):
        logger.error(f"ohlcv_cache.db 不存在: {db_path}")
        return {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    candles_map = {}
    for tf in timeframes:
        cur.execute(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM ohlcv_cache "
            "WHERE symbol = ? AND timeframe = ? "
            "ORDER BY timestamp ASC",
            (symbol, tf),
        )
        rows = cur.fetchall()
        if not rows:
            logger.warning(f"未找到 {symbol} {tf} 数据")
            continue

        candles_map[tf] = [
            {
                "timestamp": row["timestamp"] // 1000 if row["timestamp"] > 1e12 else row["timestamp"],
                "open":  row["open"],
                "high":  row["high"],
                "low":   row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
            for row in rows
        ]
        logger.info(f"  加载 {symbol} {tf}: {len(candles_map[tf])} 条")

    conn.close()
    return candles_map


def print_stats(stats: Dict):
    """打印回测统计"""
    print("\n" + "=" * 60)
    print("  BTC Supertrend 多周期共振策略（BTC/USDT 专用优化） — 回测报告")
    print("=" * 60)
    print(f"  初始资金:      ${stats['initial_capital']:>12,.2f}")
    print(f"  最终资金:      ${stats['final_capital']:>12,.2f}")
    print(f"  总收益率:      {stats['total_return_pct']:>11.2f}%")
    print(f"  总交易数:      {stats['total_trades']:>12}")
    print(f"  胜率:          {stats['win_rate']:>11.1f}%")
    print(f"  平均盈利:      {stats['avg_win_pct']:>11.2f}%")
    print(f"  平均亏损:      {stats['avg_loss_pct']:>11.2f}%")
    print(f"  盈亏比:        {stats['profit_factor']:>12.2f}")
    print(f"  夏普比率:      {stats['sharpe_ratio']:>12.2f}")
    print(f"  最大回撤:      {stats['max_drawdown_pct']:>11.2f}%")
    print(f"  平仓原因:      {stats['exit_reasons']}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BTC Supertrend 多周期共振策略（BTC/USDT 专用优化）回测")
    parser.add_argument("--symbol", default="BTC/USDT", help="交易对")
    parser.add_argument("--capital", type=float, default=10000.0, help="初始资金")
    parser.add_argument("--risk", type=float, default=0.02, help="每笔风险比例")
    parser.add_argument("--leverage", type=float, default=3.0, help="杠杆倍数")
    args = parser.parse_args()

    # ── 加载数据 ──
    tf_config = MultiTFSupertrend.DEFAULT_TIMEFRAMES
    timeframes = [t["name"] for t in tf_config]
    candles_map = load_candles_from_cache(args.symbol, timeframes)

    base_tf = tf_config[0]["name"]
    if base_tf not in candles_map:
        logger.error(f"缺少 {base_tf} 数据，无法回测")
        sys.exit(1)

    # ── 回测 ──
    rm = RiskManager(risk_pct_per_trade=args.risk)
    bt = SupertrendBacktest(
        candles_map=candles_map,
        initial_capital=args.capital,
        risk_manager=rm,
        leverage=args.leverage,
    )
    stats = bt.run()
    print_stats(stats)
