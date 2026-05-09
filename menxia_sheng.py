"""
门下省 - 风控审核服务（参考金策智算"三省六部"架构）
=====================================================

定位：所有交易指令必经的门神，所有开仓/平仓请求必须经过门下省审核。
一票否决制：风控规则只要有一条触发，直接拒绝执行。

与旧 GlobalRiskManager 的区别：
  - 旧：内嵌在 TradingAgent 内部，只能被同一个 Agent 调用
  - 新：独立服务，可被多 Agent 共享，支持全局视野的跨 Agent 风控

审核流程：
  中书省生成信号 → 提交门下省审核 → ✅通过 → 尚书省执行
                                   → ❌否决 → 记录刑部日志 → 推送飞书

使用方式：
  menxia = MenxiaSheng(initial_capital=10000.0)
  result = menxia.review_open(symbol="ETH/USDT", entry_price=3200, quantity=0.5)
  if result.approved:
      shangshu.execute_open(symbol="ETH/USDT", ...)
  else:
      logger.warning(f"门下省否决: {result.reason}")
"""

import time
import logging
import sqlite3
from enum import Enum
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """风险等级（对应金策智算的动态风控等级）"""
    NORMAL  = "normal"    # 正常运行
    CAUTION = "caution"   # 谨慎模式（降低仓位）
    WARNING = "warning"   # 预警模式（禁止开仓）
    LOCKED  = "locked"    # 锁仓模式（全系统停止）


@dataclass
class ReviewResult:
    """门下省审核结果"""
    approved: bool               # 是否批准
    reason: str                  # 原因（通过/否决理由）
    risk_level: RiskLevel        # 当前风险等级
    rules_triggered: List[str]   # 触发的风控规则列表
    exposure_pct: float          # 审核后总暴露度（估算）
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class ExecutionOrder:
    """交易指令（尚书省执行的输入）"""
    order_id: str
    agent_id: str               # 来源 Agent ID
    symbol: str                 # 交易对，如 ETH/USDT
    side: str                   # BUY / SELL
    quantity: float              # 数量
    order_type: str             # market / limit
    entry_price: Optional[float]  # 限价单挂单价格
    stop_loss: Optional[float] = None   # 止损价（可选）
    take_profit: Optional[float] = None  # 止盈价（可选）
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))


class XingBuJustice:
    """
    刑部 - 违规记录（参考金策智算）
    所有被门下省否决的交易指令，都记录在刑部留档
    """

    def __init__(self, db_path: str = "trading_system.db"):
        self.db_path = db_path
        self._init_table()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_table(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS xingbu_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                agent_id TEXT,
                symbol TEXT,
                side TEXT,
                quantity REAL,
                entry_price REAL,
                reject_reason TEXT,
                risk_level TEXT,
                rules_triggered TEXT,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS xingbu_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                agent_id TEXT,
                symbol TEXT,
                side TEXT,
                quantity REAL,
                exec_price REAL,
                exec_type TEXT,
                pnl_pct REAL,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            )
        """)
        conn.commit()
        conn.close()

    def record_rejection(self, order: ExecutionOrder, reason: str,
                         risk_level: RiskLevel, rules_triggered: List[str]):
        """记录被否决的交易指令"""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO xingbu_violations
            (order_id, agent_id, symbol, side, quantity, entry_price,
             reject_reason, risk_level, rules_triggered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order.order_id, order.agent_id, order.symbol, order.side,
            order.quantity, order.entry_price, reason, risk_level.value,
            ",".join(rules_triggered)
        ))
        conn.commit()
        conn.close()
        logger.warning(f"[刑部] 否决记录: {order.symbol} {order.side} "
                       f"数量{order.quantity} 理由:{reason}")

    def record_execution(self, order_id: str, agent_id: str, symbol: str,
                         side: str, quantity: float, exec_price: float,
                         exec_type: str = "real", pnl_pct: float = 0.0):
        """记录实际执行的交易"""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO xingbu_trades
            (order_id, agent_id, symbol, side, quantity, exec_price,
             exec_type, pnl_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (order_id, agent_id, symbol, side, quantity, exec_price, exec_type, pnl_pct))
        conn.commit()
        conn.close()

    def get_violations(self, limit: int = 20) -> List[Dict]:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT order_id, agent_id, symbol, side, quantity, reject_reason,
                   risk_level, rules_triggered, created_at
            FROM xingbu_violations
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(zip(
            ["order_id", "agent_id", "symbol", "side", "quantity",
             "reject_reason", "risk_level", "rules_triggered", "created_at"], r
        )) for r in rows]


class MenxiaSheng:
    """
    门下省 - 风控审核服务

    核心职能：
      1. 开仓审核（can_open_position）
      2. 平仓审核（can_close_position）
      3. 持仓超时检查
      4. 全局风险等级自动调整
      5. 否决记录写入刑部

    风控规则：
      R1. 单日亏损 5% → CAUTION（禁止开仓）
      R2. 单日亏损 10% → LOCK（锁定全系统）
      R3. 总暴露度 30% → 禁止新开仓
      R4. 单标的暴露度 15% → 禁止新开该仓
      R5. 单日开仓次数 10次 → 禁止开仓
      R6. 持仓超过 72h → 强制平仓预警
      R7. Equity 从峰值回落 5% → 升级风险等级
      R8. Equity 恢复峰值 3% → 降级风险等级
    """

    # 风控参数
    MAX_DAILY_LOSS_PCT      = 0.05   # R1: 单日亏损 5% → CAUTION
    MAX_DAILY_LOSS_LOCK     = 0.10   # R2: 单日亏损 10% → LOCK
    MAX_TOTAL_EXPOSURE      = 0.30   # R3: 总暴露度 30%
    MAX_POSITION_PER_SYMBOL = 0.15   # R4: 单标的 15%
    MAX_DAILY_TRADES        = 10     # R5: 单日 10 次
    MAX_HOLDING_HOURS        = 72     # R6: 72h 超时预警
    EQUITY_DROP_TRIGGER     = 0.05   # R7: 回落 5% → 升级
    EQUITY_RECOVER_TRIGGER  = 0.03   # R8: 恢复 3% → 降级

    def __init__(self, initial_capital: float = 10000.0,
                 db_path: str = "trading_system.db",
                 risk_alert_callback = None):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital

        # 每日重置状态
        self._day_key: str = ""
        self._daily_loss: float = 0.0
        self._daily_trades: int = 0

        # 持仓记录 {symbol: {entry_price, entry_time, quantity, stop_loss, take_profit}}
        self._positions: Dict[str, Dict] = {}

        # 风险等级
        self._risk_level = RiskLevel.NORMAL
        self._lock_reason: str = ""
        self._peak_equity = initial_capital

        # 飞书告警回调（由 live_trading 注入）
        self._risk_alert_callback = risk_alert_callback

        # 刑部（违规记录）
        self._xingbu = XingBuJustice(db_path)

        self._check_day_reset()
        logger.info(f"[门下省] 初始化完成，初始资金 ${initial_capital:.2f}")

    # ======================== 公开审核 API ========================

    def review_open(self, symbol: str, entry_price: float,
                    quantity: float, agent_id: str = "default",
                    order_id: Optional[str] = None,
                    signal_confidence: float = 0.5,
                    indicators: Dict = None) -> ReviewResult:
        """
        审核开仓请求（所有新开仓必须经过此审核）
        Returns ReviewResult — approved=True 表示可以执行
        """
        self._check_day_reset()
        rules_triggered: List[str] = []

        # R2: 系统锁定检查
        if self._risk_level == RiskLevel.LOCKED:
            rules_triggered.append(f"R2_系统锁定:{self._lock_reason}")
            return self._reject(symbol, quantity, agent_id, order_id,
                               f"系统锁定({self._lock_reason})",
                               RiskLevel.LOCKED, rules_triggered, entry_price)

        # R1: 单日亏损检查
        if self._daily_loss >= self.MAX_DAILY_LOSS_LOCK:
            self._risk_level = RiskLevel.LOCKED
            self._lock_reason = f"单日亏损{self._daily_loss*100:.1f}%超限"
            rules_triggered.append(f"R1_LOCK:{self.MAX_DAILY_LOSS_LOCK*100:.0f}%")
            self._maybe_alert_risk(level="lock", msg=self._lock_reason)
            return self._reject(symbol, quantity, agent_id, order_id,
                               f"系统锁定({self._lock_reason})",
                               RiskLevel.LOCKED, rules_triggered, entry_price)

        if self._daily_loss >= self.MAX_DAILY_LOSS_PCT:
            rules_triggered.append(f"R1_CAUTION:{self.MAX_DAILY_LOSS_PCT*100:.0f}%")
            return self._reject(symbol, quantity, agent_id, order_id,
                               f"单日亏损{self._daily_loss*100:.1f}%超限",
                               RiskLevel.CAUTION, rules_triggered, entry_price)

        # R5: 单日交易次数
        if self._daily_trades >= self.MAX_DAILY_TRADES:
            rules_triggered.append(f"R5_日交易次数:{self._daily_trades}")
            return self._reject(symbol, quantity, agent_id, order_id,
                               f"单日开仓次数{self._daily_trades}次已达上限",
                               self._risk_level, rules_triggered, entry_price)

        # R3: 总暴露度
        total_exp = self._calc_total_exposure(symbol, entry_price, quantity)
        if total_exp > self.MAX_TOTAL_EXPOSURE:
            rules_triggered.append(f"R3_总暴露度:{total_exp*100:.1f}%")
            return self._reject(symbol, quantity, agent_id, order_id,
                               f"总暴露度{total_exp*100:.1f}%超限({self.MAX_TOTAL_EXPOSURE*100:.0f}%)",
                               self._risk_level, rules_triggered, entry_price)

        # R4: 单标的暴露度
        sym_exp = (quantity * entry_price) / self.current_capital
        if sym_exp > self.MAX_POSITION_PER_SYMBOL:
            rules_triggered.append(f"R4_单标的:{sym_exp*100:.1f}%")
            return self._reject(symbol, quantity, agent_id, order_id,
                               f"单标的{symbol}暴露度{sym_exp*100:.1f}%超限",
                               self._risk_level, rules_triggered, entry_price)

        # R7: 动态风险等级
        if self._risk_level in (RiskLevel.WARNING, RiskLevel.LOCKED):
            rules_triggered.append(f"R7_风险等级:{self._risk_level.value}")
            return self._reject(symbol, quantity, agent_id, order_id,
                               f"风险等级{self._risk_level.value}，禁止开仓",
                               self._risk_level, rules_triggered, entry_price)

        # R8: 信号质量/置信度拦截（来自历史复盘的错误模式）
        try:
            from signal_review import get_review
            review = get_review()
            # 提取当前 indicator（如果有传入的话，这里用占位，后续可扩展）
            blocked, block_reason = review.should_block_signal(
                signal_type=1,
                confidence=signal_confidence,
                strategy_name=agent_id,
                indicators=indicators or {},
            )
            if blocked:
                rules_triggered.append(f"R8_信号质量:{block_reason}")
                return self._reject(symbol, quantity, agent_id, order_id,
                                   f"信号质量拦截({block_reason})",
                                   self._risk_level, rules_triggered, entry_price)
        except Exception:
            pass  # 信号复盘模块不可用时跳过

        # 审核通过
        logger.info(f"[门下省] ✅ 审核通过: {symbol} 数量{quantity} "
                    f"@${entry_price:.4f} 暴露度{total_exp*100:.1f}%")
        return ReviewResult(
            approved=True,
            reason="审核通过",
            risk_level=self._risk_level,
            rules_triggered=[],
            exposure_pct=total_exp * 100,
        )

    def review_close(self, symbol: str, current_price: float,
                     pnl_pct: float = 0.0, agent_id: str = "default",
                     reason: str = "signal") -> bool:
        """
        审核平仓请求
        平仓不受交易次数和暴露度限制（释放仓位总是被允许）
        但需要检查是否触发硬止损风控
        """
        if symbol not in self._positions:
            return True  # 无持仓，直接通过（不报错）

        # 5% 硬止损保护（即使策略层面平仓，风控也可一票否决）
        pos = self._positions[symbol]
        loss_pct = (current_price - pos["entry_price"]) / pos["entry_price"]
        if loss_pct <= -0.05:
            logger.warning(f"[门下省] 平仓拦截: {symbol} 亏损{loss_pct*100:.2f}% "
                            "触及5%硬止损")
            return False
        return True

    def review_batch_close(self, symbol: str) -> List[str]:
        """
        批量审核平仓（检查哪些持仓需要强制平仓）
        Returns: 需要强制平仓的 symbol 列表
        """
        force_close = []
        now_ms = int(time.time() * 1000)

        for sym, pos in list(self._positions.items()):
            # R6: 持仓超时
            holding_hours = (now_ms - pos["entry_time"]) / (1000 * 3600)
            if holding_hours > self.MAX_HOLDING_HOURS:
                force_close.append(sym)
                logger.warning(f"[门下省] 持仓超时强制平仓: {sym} 持仓{holding_hours:.0f}h")

        return force_close

    def record_open(self, symbol: str, entry_price: float, quantity: float,
                    stop_loss: float, take_profit: float):
        """记录开仓成功（尚书省执行完毕后回调）"""
        self._check_day_reset()
        self._positions[symbol] = {
            "entry_price": entry_price,
            "entry_time": int(time.time() * 1000),
            "quantity": quantity,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
        self._daily_trades += 1
        logger.info(f"[门下省] 开仓记录: {symbol} ${entry_price:.4f} × {quantity}")

    def record_close(self, symbol: str, pnl_pct: float, capital_fraction: float = 1.0):
        """记录平仓（尚书省执行完毕后回调，更新每日盈亏统计）

        Args:
            symbol: 交易对
            pnl_pct: 单笔交易盈亏百分比 (如 -3.5 表示亏损 3.5%)
            capital_fraction: 该笔交易所用资金占总资金比例 (默认 1.0 = 全仓)
        """
        self._check_day_reset()
        if symbol in self._positions:
            del self._positions[symbol]
        if pnl_pct < 0:
            self._daily_loss += abs(pnl_pct) / 100.0 * capital_fraction
        logger.info(f"[门下省] 平仓记录: {symbol} 盈亏{pnl_pct:+.2f}% "
                    f"资金比例{capital_fraction*100:.0f}% 累计日亏{self._daily_loss*100:.4f}%")

    def update_equity(self, equity: float):
        """更新当前 equity，自动调整风险等级（R7/R8）"""
        prev_peak = self._peak_equity
        self.current_capital = equity

        if equity > self._peak_equity:
            self._peak_equity = equity

        drop_pct = (self._peak_equity - equity) / self._peak_equity

        if drop_pct >= self.EQUITY_DROP_TRIGGER:
            if self._risk_level == RiskLevel.NORMAL:
                self._risk_level = RiskLevel.CAUTION
                logger.warning(f"[门下省] Equity ${prev_peak:.2f} 回落 {drop_pct*100:.1f}% → CAUTION")
                self._maybe_alert_risk(level="caution", msg=f"Equity回落{drop_pct*100:.1f}%")
            elif self._risk_level == RiskLevel.CAUTION and drop_pct >= self.EQUITY_DROP_TRIGGER * 2:
                self._risk_level = RiskLevel.WARNING
                logger.warning(f"[门下省] Equity 继续回落 → WARNING")
                self._maybe_alert_risk(level="warning", msg="Equity继续回落")
        elif self._risk_level != RiskLevel.NORMAL:
            recovery_pct = (equity - prev_peak) / prev_peak
            if recovery_pct >= self.EQUITY_RECOVER_TRIGGER:
                self._downgrade_risk_level()

    def _maybe_alert_risk(self, level: str, msg: str):
        """如有告警回调则触发飞书推送"""
        if self._risk_alert_callback:
            try:
                self._risk_alert_callback(level=level, msg=msg)
            except Exception as e:
                logger.error(f"[门下省] 告警回调失败: {e}")

    def get_status(self) -> Dict:
        """获取门下省当前状态快照"""
        self._check_day_reset()
        total_exp = sum(
            pos["quantity"] * pos.get("current_price", pos["entry_price"])
            for pos in self._positions.values()
        ) / self.current_capital if self._positions else 0.0

        return {
            "risk_level": self._risk_level.value,
            "daily_loss_pct": round(self._daily_loss * 100, 4),
            "total_exposure_pct": round(total_exp * 100, 2),
            "open_positions": len(self._positions),
            "daily_trades": self._daily_trades,
            "can_open": (self._risk_level == RiskLevel.NORMAL and
                         self._daily_loss < self.MAX_DAILY_LOSS_PCT),
            "lock_reason": self._lock_reason,
            "peak_equity": self._peak_equity,
            "current_equity": self.current_capital,
            "positions": [
                {**pos, "symbol": sym}
                for sym, pos in self._positions.items()
            ],
        }

    def get_xingbu(self) -> XingBuJustice:
        """获取刑部实例（查询违规记录）"""
        return self._xingbu

    # ======================== 私有方法 ========================

    def _reject(self, symbol: str, quantity: float, agent_id: str,
                order_id: Optional[str], reason: str,
                risk_level: RiskLevel,
                rules_triggered: List[str],
                entry_price: float) -> ReviewResult:
        """生成否决结果并写入刑部"""
        order = ExecutionOrder(
            order_id=order_id or f"reject_{int(time.time()*1000)}",
            agent_id=agent_id,
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            order_type="market",
            entry_price=entry_price,
            stop_loss=None,
            take_profit=None,
        )
        self._xingbu.record_rejection(order, reason, risk_level, rules_triggered)
        return ReviewResult(
            approved=False,
            reason=reason,
            risk_level=risk_level,
            rules_triggered=rules_triggered,
            exposure_pct=0.0,
        )

    def _check_day_reset(self):
        """UTC 每天重置每日统计"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._day_key != today:
            self._day_key = today
            self._daily_loss = 0.0
            self._daily_trades = 0
            logger.info("[门下省] UTC 新一天，统计已重置")

    def _calc_total_exposure(self, new_symbol: str, new_price: float,
                             new_quantity: float) -> float:
        """计算总持仓暴露度（含计划中的新仓）"""
        total = sum(
            pos["quantity"] * (new_price if sym == new_symbol else pos["entry_price"])
            for sym, pos in self._positions.items()
        )
        if new_symbol not in self._positions:
            total += new_quantity * new_price
        return total / self.current_capital if self.current_capital > 0 else 0.0

    def _downgrade_risk_level(self):
        """降级风险等级"""
        if self._risk_level == RiskLevel.CAUTION:
            self._risk_level = RiskLevel.NORMAL
        elif self._risk_level == RiskLevel.WARNING:
            self._risk_level = RiskLevel.CAUTION
        logger.info(f"[门下省] 风险等级降级 → {self._risk_level.value}")
