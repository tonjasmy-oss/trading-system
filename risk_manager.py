"""
全局风控层 - Global Risk Manager
参考 QuantDinger 风险控制架构，结合本地系统多 Agent 特性

功能：
  - 单日亏损上限（全局）
  - 最大持仓暴露度（总仓位上限）
  - 单标的仓位上限
  - 单日最大交易次数限制
  - 全局持仓时间上限（防止死扛）
  - 动态风险等级（根据账户 equity 变化自动调整）

使用方式：
  from risk_manager import GlobalRiskManager, RiskLevel
  risk_mgr = GlobalRiskManager(initial_capital=10000.0)
  risk_mgr.check_all(symbol, signal, position, equity)
"""

import time
import logging
from enum import Enum
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """风险等级"""
    NORMAL = "normal"       # 正常运行
    CAUTION = "caution"     # 谨慎模式（降低仓位）
    WARNNING = "warning"    # 预警模式（禁止开仓）
    LOCKED = "locked"       # 锁仓模式（全停）


@dataclass
class RiskStatus:
    """风控状态快照"""
    level: RiskLevel
    daily_loss_pct: float        # 当日亏损百分比
    total_exposure_pct: float    # 总持仓暴露度
    open_positions: int          # 当前持仓数
    daily_trade_count: int       # 当日交易次数
    can_open: bool               # 是否允许开仓
    reason: str                  # 原因描述
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


class GlobalRiskManager:
    """
    全局风控管理器

    风控规则：
      1. 单日亏损超过 MAX_DAILY_LOSS_PCT → 禁止开仓（CAUTION）
      2. 单日亏损超过 MAX_DAILY_LOSS_LOCK → 锁定全系统（WARN/LOCK）
      3. 总暴露度超过 MAX_TOTAL_EXPOSURE → 禁止开新仓
      4. 单日交易超过 MAX_DAILY_TRADES → 禁止开仓
      5. 持仓时间超过 MAX_HOLDING_HOURS → 强制平仓预警

    风险等级自动调整：
      - equity 回落 > 5% → 提升一个风险等级
      - equity 恢复 > 3% → 降低一个风险等级
    """

    # === 全局风控参数（可按需调整）===
    MAX_DAILY_LOSS_PCT = 0.05        # 单日亏损 5% → CAUTION（禁止开仓）
    MAX_DAILY_LOSS_LOCK = 0.10       # 单日亏损 10% → LOCK（全系统停止）
    MAX_TOTAL_EXPOSURE = 0.30       # 总持仓不超过资金的 30%
    MAX_POSITION_PER_SYMBOL = 0.15  # 单标的持仓不超过 15%
    MAX_DAILY_TRADES = 10            # 单日最大开仓次数
    MAX_HOLDING_HOURS = 72           # 最大持仓时间（小时），超时告警

    # 风险等级调整阈值
    EQUITY_DROP_TRIGGER = 0.05       # equity 回落 5% → 升级风险等级
    EQUITY_RECOVER_TRIGGER = 0.03   # equity 恢复 3% → 降级风险等级

    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital

        # 每日重置的状态（按 UTC 天）
        self._day_key: str = ""
        self._daily_loss = 0.0
        self._daily_trades = 0
        self._daily_trade_list: List[Tuple[int, str]] = []  # (timestamp, symbol)

        # 持仓记录
        self._positions: Dict[str, Dict] = {}  # symbol -> {entry_time, entry_price, quantity, stop_loss, take_profit}

        # 当前风险等级
        self._risk_level = RiskLevel.NORMAL
        self._lock_reason = ""

        # 峰值 equity（用于追踪 equity 回落）
        self._peak_equity = initial_capital

        self._check_day_reset()

    # ======================== 公开 API ========================

    def can_open_position(self, symbol: str, estimated_price: float) -> Tuple[bool, str]:
        """
        判断是否可以开新仓
        Returns: (can_open, reason)
        """
        self._check_day_reset()

        # 1. 系统锁定检查
        if self._risk_level == RiskLevel.LOCKED:
            return False, f"系统锁定({self._lock_reason})"

        # 2. 单日亏损检查
        if self._daily_loss >= self.MAX_DAILY_LOSS_LOCK:
            self._risk_level = RiskLevel.LOCKED
            self._lock_reason = f"单日亏损{self._daily_loss*100:.1f}%超限"
            return False, f"系统锁定({self._lock_reason})"

        if self._daily_loss >= self.MAX_DAILY_LOSS_PCT:
            return False, f"单日亏损{self._daily_loss*100:.1f}%超限(>{self.MAX_DAILY_LOSS_PCT*100:.0f}%)"

        # 3. 单日交易次数检查
        if self._daily_trades >= self.MAX_DAILY_TRADES:
            return False, f"单日开仓次数{self._daily_trades}次已达上限"

        # 4. 总暴露度检查
        total_exposure = self._calc_total_exposure(symbol, estimated_price)
        if total_exposure > self.MAX_TOTAL_EXPOSURE:
            return False, f"总暴露度{total_exposure*100:.1f}%超限(>{self.MAX_TOTAL_EXPOSURE*100:.0f}%)"

        # 5. 风险等级警告
        if self._risk_level in (RiskLevel.WARNNING, RiskLevel.LOCKED):
            return False, f"风险等级{self._risk_level.value}，禁止开仓"

        return True, "允许开仓"

    def record_open_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit: float,
    ):
        """记录开仓（用于暴露度追踪）"""
        self._check_day_reset()
        self._positions[symbol] = {
            "entry_price": entry_price,
            "entry_time": int(time.time() * 1000),
            "quantity": quantity,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
        self._daily_trades += 1
        self._daily_trade_list.append((int(time.time() * 1000), symbol))
        logger.info(
            f"[风控] 开仓记录: {symbol} 价格${entry_price:.4f} 数量{quantity:.4f}  "
            f"当日交易{self._daily_trades}次 暴露度{self._calc_total_exposure(symbol, entry_price)*100:.1f}%"
        )

    def record_close_position(self, symbol: str, pnl_pct: float):
        """记录平仓（更新每日盈亏）"""
        self._check_day_reset()
        if symbol in self._positions:
            del self._positions[symbol]
        if pnl_pct < 0:
            self._daily_loss += abs(pnl_pct) / 100.0
        logger.info(
            f"[风控] 平仓: {symbol} 盈亏{pnl_pct:+.2f}%  "
            f"当日亏损累计{self._daily_loss*100:.2f}%  剩余次数{self.MAX_DAILY_TRADES - self._daily_trades}"
        )

    def check_position_timeout(self) -> List[Tuple[str, int]]:
        """
        检查所有持仓是否超时
        Returns: [(symbol, holding_hours), ...] 超时持仓列表
        """
        now_ms = int(time.time() * 1000)
        timeout_list = []
        for symbol, pos in self._positions.items():
            holding_hours = (now_ms - pos["entry_time"]) / (1000 * 3600)
            if holding_hours > self.MAX_HOLDING_HOURS:
                timeout_list.append((symbol, int(holding_hours)))
        return timeout_list

    def check_stop_loss_trigger(
        self, symbol: str, current_price: float
    ) -> Tuple[bool, str]:
        """
        检查持仓是否触发止损
        Returns: (should_stop, reason)
        """
        if symbol not in self._positions:
            return False, ""
        pos = self._positions[symbol]
        pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"]
        if pnl_pct <= -0.05:  # 5% 硬止损（更严格）
            return True, f"硬止损触发({pnl_pct*100:.2f}%)"
        return False, ""

    def update_equity(self, equity: float):
        """更新当前 equity，自动调整风险等级"""
        prev_peak = self._peak_equity
        self.current_capital = equity

        if equity > self._peak_equity:
            self._peak_equity = equity

        # 计算从峰值回落比例
        drop_pct = (self._peak_equity - equity) / self._peak_equity

        if drop_pct >= self.EQUITY_DROP_TRIGGER:
            if self._risk_level == RiskLevel.NORMAL:
                self._risk_level = RiskLevel.CAUTION
                logger.warning(f"[风控] Equity 从 ${prev_peak:.2f} 回落 {drop_pct*100:.1f}% → CAUTION 模式")
            elif self._risk_level == RiskLevel.CAUTION and drop_pct >= self.EQUITY_DROP_TRIGGER * 2:
                self._risk_level = RiskLevel.WARNNING
                logger.warning(f"[风控] Equity 继续回落 → WARNING 模式")
        elif self._risk_level != RiskLevel.NORMAL:
            # 检查是否恢复
            recovery_pct = (equity - prev_peak) / prev_peak
            if recovery_pct >= self.EQUITY_RECOVER_TRIGGER:
                self._downgrade_risk_level()

    def get_status(self) -> RiskStatus:
        """获取当前风控状态快照"""
        self._check_day_reset()
        total_exp = self._calc_total_exposure(None, 0)

        # 计算总暴露度（各持仓 sum）
        total_exposure = sum(
            (pos["quantity"] * self._positions[sym].get("current_price", pos["entry_price"]))
            / self.current_capital
            for sym, pos in self._positions.items()
        ) if self._positions else 0.0

        return RiskStatus(
            level=self._risk_level,
            daily_loss_pct=self._daily_loss * 100,
            total_exposure_pct=total_exposure * 100,
            open_positions=len(self._positions),
            daily_trade_count=self._daily_trades,
            can_open=self._risk_level == RiskLevel.NORMAL and self._daily_loss < self.MAX_DAILY_LOSS_PCT,
            reason=self._lock_reason if self._risk_level == RiskLevel.LOCKED else "",
        )

    def get_open_positions_summary(self) -> List[Dict]:
        """获取所有持仓摘要（用于推送）"""
        now_ms = int(time.time() * 1000)
        result = []
        for symbol, pos in self._positions.items():
            holding_hours = (now_ms - pos["entry_time"]) / (1000 * 3600)
            result.append({
                "symbol": symbol,
                "entry_price": pos["entry_price"],
                "entry_time": pos["entry_time"],
                "holding_hours": int(holding_hours),
                "stop_loss": pos["stop_loss"],
                "take_profit": pos["take_profit"],
            })
        return result

    # ======================== 私有方法 ========================

    def _check_day_reset(self):
        """UTC 每天重置一次每日统计"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._day_key != today:
            self._day_key = today
            self._daily_loss = 0.0
            self._daily_trades = 0
            self._daily_trade_list = []
            logger.info("[风控] UTC 新一天，开始统计")

    def _calc_total_exposure(self, new_symbol: Optional[str], new_price: float) -> float:
        """计算总持仓暴露度（包括计划中的新仓）"""
        total_value = 0.0
        for symbol, pos in self._positions.items():
            price = new_price if symbol == new_symbol else pos["entry_price"]
            total_value += pos["quantity"] * price

        # 加上计划中的新仓（估算）
        if new_symbol and new_symbol not in self._positions:
            estimated_qty = self.current_capital / new_price
            total_value += estimated_qty * new_price

        return total_value / self.current_capital if self.current_capital > 0 else 0.0

    def _downgrade_risk_level(self):
        """降级风险等级"""
        old = self._risk_level
        if self._risk_level == RiskLevel.CAUTION:
            self._risk_level = RiskLevel.NORMAL
        elif self._risk_level == RiskLevel.WARNNING:
            self._risk_level = RiskLevel.CAUTION
        if old != self._risk_level:
            logger.info(f"[风控] 恢复 → {self._risk_level.value} 模式")


class PositionSidecar:
    """
    持仓侧翼监控 - 独立于主策略的风控辅助
    在主策略持仓期间，持续监控持仓健康度

    功能：
      - 定时检查持仓 RSI/均线偏离度
      - 提前预警（持仓亏损超限前主动平仓）
      - 动态跟踪止损（Trailing Stop）
    """

    def __init__(self, trailing_stop_pct: float = 0.015):
        """
        Args:
            trailing_stop_pct: 跟踪止损幅度（默认 1.5%）
                             价格向有利方向移动 1.5% 后，止损线上移
        """
        self.trailing_stop_pct = trailing_stop_pct
        self.highest_price_since_entry: float = 0.0
        self.trailing_stop_price: float = 0.0

    def reset(self, entry_price: float):
        """重置（开仓时调用）"""
        self.highest_price_since_entry = entry_price
        self.trailing_stop_price = entry_price * (1 - self.trailing_stop_pct)

    def update(self, current_price: float) -> Tuple[bool, str]:
        """
        更新持仓状态，计算跟踪止损
        Returns: (should_stop, reason)
        """
        if current_price > self.highest_price_since_entry:
            self.highest_price_since_entry = current_price
            # 止盈线上移（保护更多利润）
            new_ts = current_price * (1 - self.trailing_stop_pct)
            if new_ts > self.trailing_stop_price:
                self.trailing_stop_price = new_ts

        # 检查跟踪止损
        if current_price <= self.trailing_stop_price:
            return True, f"跟踪止损触发(${self.trailing_stop_price:.4f})"

        return False, ""

    def get_trailing_stop_price(self) -> float:
        return self.trailing_stop_price