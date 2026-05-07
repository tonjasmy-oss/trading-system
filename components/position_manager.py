"""
PositionManager — 三省六部: 尚书省（持仓管理）

职责：
  - 仓位生命周期管理（开仓/平仓/记录）
  - 止损/止盈逻辑
  - 与门下省（风控）和尚书省（执行）交互
  - 数据库成交记录
  - 权益曲线记录
"""

from __future__ import annotations

import logging
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    entry_price: float
    quantity: float
    entry_time: int       # unix timestamp (ms)
    side: str = "long"    # "long" or "short"
    stop_loss: float = 0.0
    take_profit: float = 0.0
    agent_id: str = ""
    order_id: str = ""


@dataclass
class PnLResult:
    realized_pnl: float
    realized_pnl_pct: float
    exit_reason: str      # "stop_loss" | "take_profit" | "signal" | "timeout" | "manual"


class PositionManager:
    """
    负责：持仓状态 + 风控检查 + 成交记录
    不负责信号生成（由 SignalEngine 负责）
    """

    def __init__(
        self,
        agent_id: str,
        symbol: str,
        exchange: str,
        initial_capital: float,
        stop_loss_pct: float = 0.025,
        take_profit_pct: float = 0.04,
        menxia=None,      # 门下省（风控审核）
        shangshu=None,    # 尚书省（执行调度）
    ):
        self.agent_id = agent_id
        self.symbol = symbol
        self.exchange = exchange
        self.initial_capital = initial_capital
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.capital = initial_capital
        self.position: Optional[Position] = None
        self.menxia = menxia
        self.shangshu = shangshu

    def _get_equity(self, current_price: float) -> float:
        if self.position:
            return self.capital + self.position.quantity * (current_price - self.position.entry_price)
        return self.capital

    # ─── 开仓 ────────────────────────────────────────────────

    async def open_position(
        self,
        price: float,
        timestamp: int,
        quantity: float,
        order_id: str = "",
    ) -> bool:
        """尝试开仓（需通过门下省审核）"""
        if self.position is not None:
            logger.warning(f"[{self.agent_id}] 已有持仓，跳过开仓")
            return False

        # 门下省审核
        if self.menxia:
            review = self.menxia.review_open(
                symbol=self.symbol,
                entry_price=price,
                quantity=quantity,
                agent_id=self.agent_id,
            )
            if not review.approved:
                logger.warning(f"[{self.agent_id}] 门下省否决开仓: {review.reason}")
                return False

        self.position = Position(
            symbol=self.symbol,
            entry_price=price,
            quantity=quantity,
            entry_time=timestamp,
            stop_loss=price * (1 - self.stop_loss_pct),
            take_profit=price * (1 + self.take_profit_pct),
            agent_id=self.agent_id,
            order_id=order_id or f"{self.agent_id}_{timestamp}",
        )

        # 尚书省执行
        if self.shangshu:
            try:
                await self.shangshu.place_order(self.symbol, "buy", quantity, price)
            except Exception as e:
                logger.error(f"[{self.agent_id}] 尚书省执行失败: {e}")

        # DB 记录
        self._record_open(price, quantity, timestamp)
        logger.info(f"[{self.agent_id}] 开仓: {self.symbol} {quantity} @ {price}")
        return True

    # ─── 平仓 ────────────────────────────────────────────────

    async def close_position(
        self,
        price: float,
        timestamp: int,
        reason: str = "signal",
        rsi: float = 50.0,
    ) -> bool:
        """平仓"""
        if self.position is None:
            return False

        pnl = (price - self.position.entry_price) / self.position.entry_price
        realized = self.capital * pnl
        self.capital += realized

        # 门下省记录
        if self.menxia:
            self.menxia.record_close(self.symbol, pnl * 100)

        # DB 记录
        self._record_close(price, timestamp, reason, pnl, rsi)

        logger.info(
            f"[{self.agent_id}] 平仓: {self.symbol} @ {price} "
            f"原因={reason} PnL={realized:.2f} ({pnl*100:.2f}%)"
        )
        self.position = None
        return True

    # ─── 风控检查（止损/止盈/超时）─────────────────────────────

    async def check_position_risk(
        self, price: float, timestamp: int, rsi: float
    ) -> bool:
        """持仓风控检查，返回是否已触发平仓"""
        if self.position is None:
            return False

        pnl_pct = (price - self.position.entry_price) / self.position.entry_price * 100

        # 止损
        if price <= self.position.stop_loss:
            logger.warning(f"[{self.agent_id}] 触发止损 @ {price}")
            await self.close_position(price, timestamp, "stop_loss", rsi)
            return True

        # 止盈
        if price >= self.position.take_profit:
            logger.warning(f"[{self.agent_id}] 触发止盈 @ {price}")
            await self.close_position(price, timestamp, "take_profit", rsi)
            return True

        # 持仓超时（门下省批量检查）
        if self.menxia:
            timeout_list = self.menxia.review_batch_close(self.symbol)
            if self.symbol in timeout_list:
                logger.warning(f"[{self.agent_id}] 持仓超时，强制平仓")
                await self.close_position(price, timestamp, "timeout", rsi)
                return True

        return False

    # ─── 数据库记录 ──────────────────────────────────────────

    def _record_open(self, price: float, quantity: float, timestamp: int):
        try:
            from database import init_db
            conn = init_db()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO trades (symbol, side, quantity, price, timestamp, status, agent_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (self.symbol, "buy", quantity, price, timestamp, "open", self.agent_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"[{self.agent_id}] 记录开仓失败: {e}")

    def _record_close(
        self, price: float, timestamp: int, reason: str,
        pnl_pct: float, rsi: float
    ):
        try:
            from database import init_db
            conn = init_db()
            cur = conn.cursor()
            # 关闭所有该symbol的open仓位
            cur.execute(
                "UPDATE trades SET status='closed', close_price=?, close_time=? "
                "WHERE symbol=? AND status='open' AND agent_id=?",
                (price, timestamp, self.symbol, self.agent_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"[{self.agent_id}] 记录平仓失败: {e}")

    # ─── 权益记录 ────────────────────────────────────────────

    def log_equity(self, timestamp: int, price: float, equity: float, rsi: float):
        try:
            import sqlite3
            conn = sqlite3.connect("live_trading.db")
            cur = conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO equity_log (timestamp, price, equity, rsi) VALUES (?, ?, ?, ?)",
                (timestamp, price, equity, rsi)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ─── 状态查询 ────────────────────────────────────────────

    def get_status(self) -> Dict:
        return {
            "agent_id": self.agent_id,
            "symbol": self.symbol,
            "position": {
                "entry_price": self.position.entry_price,
                "quantity": self.position.quantity,
                "unrealized_pnl_pct": ((self._get_equity(self.position.entry_price) - self.capital) / self.capital * 100)
                    if self.position else 0.0,
            } if self.position else None,
            "capital": self.capital,
            "equity": self._get_equity(self.position.entry_price if self.position else 0.0),
            "total_return_pct": (self.capital - self.initial_capital) / self.initial_capital * 100,
        }
