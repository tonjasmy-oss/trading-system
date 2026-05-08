"""
trade_history.py — 交易结果记忆（参考 Agent-S Outcome Memory 设计）
==========================================================================

定位：完整记录每次开仓/平仓的全生命周期数据，供事后复盘和策略学习。

核心价值（Agent-S 借鉴）：
  - Agent-S 用 Trajectory 记住过去动作和结果，用于下次决策
  - 本模块记录"这次交易赚钱了还是亏钱了"，让系统学会什么市场环境适合什么策略

存储：
  - trade_history.db — 交易历史记忆（持久化，不随进程重启丢失）
  - 索引按 symbol / exit_reason / market_regime 分类，支持快速复盘查询

使用方式：
  th = TradeHistory()

  # 开仓时记录信号上下文
  th.record_open(
      symbol="BTC/USDT", timeframe="4h", agent_id="agent_1",
      signal_price=67000, exec_price=67050,       # ← 滑点从此计算
      entry_time=ts, stop_loss=65000, take_profit=71000,
      quantity=0.1, strategy="RSI",
      signal_confidence=0.72, ai_verdict="BULLISH",
      market_trend="uptrend", market_volatility="medium",
      exchange="gateio"
  )

  # 平仓时补充结果数据
  th.record_close(
      trade_id=1, exit_price=69500, exit_time=ts2,
      exit_reason="take_profit",
      pnl_pct=3.66, pnl_abs=365.0,
      holding_hours=18.5,
      max_adverse_excursion=-0.8,   # 最大逆势偏离（%）
      max_favorable_excursion=5.2,  # 最大顺势偏离（%）
  )

  # 复盘查询
  stats = th.get_performance_stats(symbol="BTC/USDT", min_trades=5)
  recent = th.get_trades(limit=20, symbol="BTC/USDT")
  by_reason = th.get_pnl_by_exit_reason()
"""

import sqlite3
import logging
import time as _time
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class OpenTradeRecord:
    """开仓记录（信号上下文）"""
    trade_id: int                    # 返回的自增 ID
    symbol: str
    timeframe: str
    agent_id: str
    exchange: str

    # 价格
    signal_price: float               # 中书省发出信号时的价格
    exec_price: float                # 实际成交价
    slippage_pct: float              # 滑点 = (exec - signal) / signal * 100

    # 仓位
    quantity: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float          # 止盈/止损比

    # 策略
    strategy: str                     # RSI / MACD / BB / VOTE / ma_cross / rsi
    signal_confidence: float           # 信号置信度 0~1
    ai_verdict: str                   # AI 宏观验证结果

    # 市场状态（来自 market_regime 标注）
    market_trend: str                 # uptrend / downtrend / ranging
    market_volatility: str            # low / medium / high

    # 时序
    entry_time: int                  # Unix 秒时间戳
    exit_time: Optional[int] = None   # 平仓时填充
    exit_reason: Optional[str] = None  # stop_loss / take_profit / signal_reversal / manual / time_limit
    exit_price: Optional[float] = None

    # 盈亏
    pnl_pct: Optional[float] = None
    pnl_abs: Optional[float] = None
    holding_hours: Optional[float] = None

    # 执行质量
    max_adverse_excursion: Optional[float] = None   # MAE（%）
    max_favorable_excursion: Optional[float] = None # MFE（%）
    # MAE = 开仓后最大浮亏幅度（绝对值越小越好）
    # MFE = 开仓后最大浮盈幅度（越大说明趋势越强）

    # 关联
    order_id: Optional[str] = None
    notes: str = ""


# ─────────────────────────────────────────────────────────────
# 主类
# ─────────────────────────────────────────────────────────────

class TradeHistory:
    """
    交易结果记忆 — 全生命周期记录

    表结构：
      trade_history
        id                自增主键
        trade_uid         唯一标识（uuid），关联开仓和平仓
        symbol            交易对
        timeframe         周期
        agent_id          来源 Agent
        exchange          交易所

        # 开仓
        signal_price      信号发出时价格
        exec_price        成交价
        slippage_pct      滑点 %
        entry_time        开仓时间（Unix 秒）
        quantity           数量
        stop_loss          止损价
        take_profit        止盈价
        risk_reward_ratio  盈亏比
        strategy           策略名
        signal_confidence  置信度
        ai_verdict         AI 验证结果
        market_trend       市场趋势
        market_volatility  波动率
        order_id           订单号
        is_live            是否实盘

        # 平仓（初值为 NULL，close 后填充）
        exit_time          平仓时间
        exit_reason         stop_loss / take_profit / signal_reversal / manual / time_limit
        exit_price          平仓价
        pnl_pct             收益率 %
        pnl_abs             绝对收益（USDT）
        holding_hours        持仓时长（小时）
        max_adverse_excursion 最大逆势偏离 %
        max_favorable_excursion 最大顺势偏离 %
        notes              备注

        created_at         记录创建时间
        closed_at          平仓时间（方便按日期索引）
    """

    DB_NAME = "trade_history.db"

    def __init__(self, db_dir: str = "."):
        self.db_path = Path(db_dir) / self.DB_NAME
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_uid         TEXT    NOT NULL,
                symbol            TEXT    NOT NULL,
                timeframe         TEXT    NOT NULL DEFAULT '4h',
                agent_id          TEXT    NOT NULL DEFAULT 'agent_1',
                exchange          TEXT    NOT NULL DEFAULT 'binance',

                -- 开仓
                signal_price      REAL    NOT NULL,
                exec_price        REAL    NOT NULL,
                slippage_pct      REAL    NOT NULL,
                entry_time        INTEGER NOT NULL,
                quantity          REAL    NOT NULL,
                stop_loss         REAL,
                take_profit       REAL,
                risk_reward_ratio REAL    NOT NULL,
                strategy          TEXT    NOT NULL,
                signal_confidence REAL    NOT NULL,
                ai_verdict        TEXT    NOT NULL DEFAULT '',
                market_trend      TEXT    NOT NULL DEFAULT 'unknown',
                market_volatility TEXT    NOT NULL DEFAULT 'unknown',
                order_id          TEXT,
                is_live           INTEGER NOT NULL DEFAULT 0,

                -- 平仓（初值 NULL）
                exit_time         INTEGER,
                exit_reason       TEXT,
                exit_price        REAL,
                pnl_pct           REAL,
                pnl_abs           REAL,
                holding_hours     REAL,
                max_adverse_excursion REAL,
                max_favorable_excursion REAL,
                notes             TEXT    NOT NULL DEFAULT '',

                -- 元
                created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
                closed_at         TEXT,
                UNIQUE(trade_uid)
            )
        """)

        # 索引：常用查询字段
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_th_symbol
            ON trade_history(symbol, exit_time DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_th_exit_reason
            ON trade_history(exit_reason)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_th_market_regime
            ON trade_history(market_trend, market_volatility)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_th_strategy
            ON trade_history(strategy)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_th_is_live
            ON trade_history(is_live)
        """)
        conn.commit()
        conn.close()
        logger.info(f"[TradeHistory] 数据库初始化完成: {self.db_path}")

    # ── 写入 API ─────────────────────────────────────────────

    def record_open(
        self,
        symbol: str,
        signal_price: float,
        exec_price: float,
        entry_time: int,
        quantity: float,
        strategy: str,
        timeframe: str = "4h",
        agent_id: str = "agent_1",
        exchange: str = "binance",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        signal_confidence: float = 0.5,
        ai_verdict: str = "",
        market_trend: str = "unknown",
        market_volatility: str = "unknown",
        order_id: Optional[str] = None,
        is_live: bool = False,
    ) -> int:
        """
        记录开仓。
        Returns: trade_uid（用于后续 close 关联）
        """
        import uuid
        trade_uid = uuid.uuid4().hex

        slippage_pct = (exec_price - signal_price) / signal_price * 100 if signal_price else 0.0

        # 计算盈亏比
        if stop_loss and take_profit and signal_price:
            loss_pct  = abs(signal_price - stop_loss)  / signal_price * 100
            gain_pct  = abs(take_profit - signal_price) / signal_price * 100
            rr = gain_pct / loss_pct if loss_pct > 0 else 0.0
        else:
            rr = 0.0

        conn = self._get_conn()
        cursor = conn.execute("""
            INSERT INTO trade_history (
                trade_uid, symbol, timeframe, agent_id, exchange,
                signal_price, exec_price, slippage_pct, entry_time,
                quantity, stop_loss, take_profit, risk_reward_ratio,
                strategy, signal_confidence, ai_verdict,
                market_trend, market_volatility, order_id, is_live
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_uid, symbol, timeframe, agent_id, exchange,
            signal_price, exec_price, slippage_pct, entry_time,
            quantity, stop_loss, take_profit, rr,
            strategy, signal_confidence, ai_verdict,
            market_trend, market_volatility,
            order_id, 1 if is_live else 0,
        ))
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info(
            f"[TradeHistory] 开仓记录 trade_id={trade_id} "
            f"{symbol} 信号={signal_price} 成交={exec_price} "
            f"滑点={slippage_pct:+.3f}% 策略={strategy}"
        )
        return trade_id

    def record_close(
        self,
        trade_id: int,
        exit_price: float,
        exit_time: int,
        exit_reason: str,
        pnl_pct: float,
        pnl_abs: float,
        holding_hours: float,
        max_adverse_excursion: Optional[float] = None,
        max_favorable_excursion: Optional[float] = None,
        notes: str = "",
    ):
        """补充平仓数据，完成一条完整交易记录"""
        conn = self._get_conn()
        conn.execute("""
            UPDATE trade_history SET
                exit_time        = ?,
                exit_reason      = ?,
                exit_price       = ?,
                pnl_pct          = ?,
                pnl_abs          = ?,
                holding_hours    = ?,
                max_adverse_excursion = ?,
                max_favorable_excursion = ?,
                notes            = ?,
                closed_at         = datetime('now')
            WHERE id = ? AND exit_time IS NULL
        """, (
            exit_time, exit_reason, exit_price,
            pnl_pct, pnl_abs, holding_hours,
            max_adverse_excursion, max_favorable_excursion,
            notes, trade_id,
        ))
        conn.commit()
        conn.close()

        logger.info(
            f"[TradeHistory] 平仓更新 trade_id={trade_id} "
            f"{exit_reason} 盈亏={pnl_pct:+.2f}% 持仓={holding_hours:.1f}h"
        )

    # ── 查询 API ─────────────────────────────────────────────

    def get_trades(
        self,
        limit: int = 50,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        exit_reason: Optional[str] = None,
        is_live: Optional[bool] = None,
    ) -> List[Dict]:
        """查询历史交易"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM trade_history WHERE exit_time IS NOT NULL"
        params: List[Any] = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if strategy:
            sql += " AND strategy = ?"
            params.append(strategy)
        if exit_reason:
            sql += " AND exit_reason = ?"
            params.append(exit_reason)
        if is_live is not None:
            sql += " AND is_live = ?"
            params.append(1 if is_live else 0)
        sql += " ORDER BY exit_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_open_trades(self, symbol: Optional[str] = None) -> List[Dict]:
        """查询未平仓交易"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM trade_history WHERE exit_time IS NULL"
        params: List[Any] = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_performance_stats(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        market_trend: Optional[str] = None,
        market_volatility: Optional[str] = None,
        min_trades: int = 3,
    ) -> Dict[str, Any]:
        """
        计算绩效统计（供复盘面板使用）
        类似 Backtrader 的 stats 输出
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        conditions = ["exit_time IS NOT NULL"]
        params: List[Any] = []
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if strategy:
            conditions.append("strategy = ?")
            params.append(strategy)
        if market_trend:
            conditions.append("market_trend = ?")
            params.append(market_trend)
        if market_volatility:
            conditions.append("market_volatility = ?")
            params.append(market_volatility)

        where = " AND ".join(conditions)
        rows = conn.execute(
            f"SELECT pnl_pct, pnl_abs, holding_hours, slippage_pct, exit_reason, strategy FROM trade_history WHERE {where}",
            params
        ).fetchall()
        conn.close()

        if len(rows) < min_trades:
            return {
                "enough_data": False,
                "trade_count": len(rows),
                "min_required": min_trades,
            }

        pnls    = [r["pnl_pct"] for r in rows]
        slipages = [r["slippage_pct"] for r in rows]
        hours   = [r["holding_hours"] for r in rows if r["holding_hours"]]

        wins    = [p for p in pnls if p > 0]
        losses  = [p for p in pnls if p <= 0]

        # ── 核心指标 ──
        total_trades = len(pnls)
        win_rate = len(wins) / total_trades if total_trades else 0

        avg_win  = sum(wins) / len(wins)  if wins  else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")

        # 期望值
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss))

        # 最大连续亏损
        sorted_pnls = sorted([r["pnl_pct"] for r in rows])
        max_drawdown = abs(min(sorted_pnls)) if sorted_pnls else 0

        # 按 exit_reason 分类
        by_reason: Dict[str, Dict] = {}
        for r in rows:
            k = r["exit_reason"]
            if k not in by_reason:
                by_reason[k] = {"count": 0, "total_pnl": 0.0, "wins": 0}
            by_reason[k]["count"] += 1
            by_reason[k]["total_pnl"] += r["pnl_pct"]
            if r["pnl_pct"] > 0:
                by_reason[k]["wins"] += 1

        # 按策略分类
        by_strategy: Dict[str, Dict] = {}
        for r in rows:
            k = r["strategy"]
            if k not in by_strategy:
                by_strategy[k] = {"count": 0, "total_pnl": 0.0, "wins": 0}
            by_strategy[k]["count"] += 1
            by_strategy[k]["total_pnl"] += r["pnl_pct"]
            if r["pnl_pct"] > 0:
                by_strategy[k]["wins"] += 1

        return {
            # ── 基础统计
            "enough_data": True,
            "trade_count": total_trades,
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": win_rate,

            # ── 盈亏
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "expectancy_pct": expectancy,
            "total_pnl_pct": sum(pnls),
            "total_pnl_abs": sum(r["pnl_abs"] for r in rows),
            "max_drawdown_pct": max_drawdown,

            # ── 执行质量
            "avg_slippage_pct": sum(slipages) / len(slipages),
            "avg_holding_hours": sum(hours) / len(hours) if hours else 0,

            # ── 分类统计
            "by_exit_reason": by_reason,
            "by_strategy": by_strategy,
        }

    def get_pnl_by_exit_reason(self) -> Dict[str, Dict]:
        """按平仓原因分组统计盈亏（用于复盘：止损频繁说明策略问题）"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT exit_reason,
                   COUNT(*) as count,
                   SUM(pnl_pct) as total_pnl,
                   AVG(pnl_pct) as avg_pnl,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins
            FROM trade_history
            WHERE exit_reason IS NOT NULL
            GROUP BY exit_reason
        """).fetchall()
        conn.close()
        return {r["exit_reason"]: dict(r) for r in rows}

    def get_pnl_by_market_regime(self) -> Dict[str, Dict]:
        """按市场状态分组统计盈亏（核心：什么市场环境适合什么策略）"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT market_trend, market_volatility,
                   COUNT(*) as count,
                   SUM(pnl_pct) as total_pnl,
                   AVG(pnl_pct) as avg_pnl,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                   strategy
            FROM trade_history
            WHERE exit_reason IS NOT NULL
            GROUP BY market_trend, market_volatility, strategy
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_recent_signals(self, limit: int = 20) -> List[Dict]:
        """查询最近信号（包含未平仓的），供 Dashboard 信号盘使用"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, symbol, strategy, signal_price, exec_price,
                   slippage_pct, entry_time, pnl_pct, exit_reason,
                   market_trend, market_volatility, is_live
            FROM trade_history
            ORDER BY entry_time DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# 全局单例
# ─────────────────────────────────────────────────────────────

_th: Optional[TradeHistory] = None

def get_history(db_dir: str = ".") -> TradeHistory:
    global _th
    if _th is None:
        _th = TradeHistory(db_dir)
    return _th
