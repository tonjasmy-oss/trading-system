"""
OnlineParameterOptimizer — 在线参数自动优化
============================================

实时追踪每笔交易结果，在检测到性能衰减时自动微调策略参数。

调整维度：
  - RSI oversold / overbought 阈值（±1~3 点）
  - Stop-loss 百分比（±0.25%~1%）
  - Take-profit 百分比（±0.25%~1%）

触发条件：
  - 最近 N 笔交易胜率 < 40% 或盈亏比 < 1.0
  - 连续止损次数 ≥ 3
  - 冷却期 ≥ 24 小时（避免频繁抖动）

参考：Grid Search 最优参数作为锚点，微调范围不超过锚点的 ±30%。

用法：
  opt = OnlineParameterOptimizer(symbol="ETH/USDT")
  opt.record_trade(entry_price=2000, exit_price=2100, exit_reason="take_profit")
  changed = opt.maybe_adjust("ETH/USDT")
"""

import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# 参数边界（防止微调跑偏）
# ============================================================

PARAM_BOUNDS = {
    "rsi_period":    (6, 30),
    "oversold":      (10.0, 40.0),
    "overbought":    (55.0, 85.0),
    "stop_loss":     (0.005, 0.10),   # 0.5% ~ 10%
    "take_profit":   (0.01, 0.20),    # 1% ~ 20%
}

# 微调步长
ADJUST_STEP = {
    "rsi_period":    1,
    "oversold":      1.0,
    "overbought":    1.0,
    "stop_loss":     0.0025,   # 0.25%
    "take_profit":   0.005,    # 0.5%
}

# 冷却期（秒）
COOLDOWN_SECONDS = 24 * 3600

# 评估窗口（最近 N 笔交易）
EVAL_WINDOW = 10

# 触发阈值
MIN_WIN_RATE = 0.35       # 胜率低于此值触发
MIN_PROFIT_FACTOR = 0.8   # 盈亏比低于此值触发
MAX_CONSECUTIVE_LOSS = 3  # 连续止损次数


class OnlineParameterOptimizer:
    """在线参数优化器 — 每标的独立实例"""

    def __init__(self, symbol: str, db_path: str = "live_trading.db",
                 anchor_params: Optional[Dict] = None):
        self.symbol = symbol
        self.db_path = db_path
        # 锚点参数（Grid Search 最优，不可偏离超过 30%）
        self.anchor = anchor_params or {}
        # 当前生效参数
        self.current = dict(self.anchor) if self.anchor else {}
        self._init_db()

    # ---------- 数据库 ----------

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS param_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                param_name TEXT NOT NULL,
                old_value REAL,
                new_value REAL,
                reason TEXT,
                trade_count INTEGER,
                win_rate REAL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_param_history_symbol
            ON param_history(symbol, created_at)
        """)
        conn.commit()
        conn.close()

    # ---------- 交易记录 ----------

    def record_trade(self, exit_reason: str, pnl_pct: float):
        """记录一笔已完成的交易（开仓时由 TradingAgent 调用 record_open_context）"""
        # 交易数据从 trades 表读取，这里不需要额外存储
        pass

    # ---------- 核心：性能评估 ----------

    def _get_recent_trades(self, limit: int = EVAL_WINDOW) -> List[Dict]:
        """获取最近 N 笔已完成交易"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT exit_reason, pnl_pct, exit_time, created_at
            FROM trades
            WHERE symbol = ? AND pnl_pct IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (self.symbol, limit)).fetchall()
        conn.close()
        return [
            {"exit_reason": r[0], "pnl_pct": r[1], "exit_time": r[2], "created_at": r[3]}
            for r in rows
        ]

    def _evaluate_performance(self) -> Dict:
        """评估最近交易表现"""
        trades = self._get_recent_trades()
        if len(trades) < 3:
            return {"ready": False, "reason": f"交易不足（{len(trades)}<3）"}

        wins = [t for t in trades if (t["pnl_pct"] or 0) > 0]
        losses = [t for t in trades if (t["pnl_pct"] or 0) <= 0]
        win_rate = len(wins) / len(trades) if trades else 0

        total_profit = sum(t["pnl_pct"] for t in wins)
        total_loss = sum(abs(t["pnl_pct"]) for t in losses)
        profit_factor = total_profit / max(total_loss, 0.001)

        # 连续止损
        consecutive_loss = 0
        for t in trades:
            if (t["pnl_pct"] or 0) <= 0:
                consecutive_loss += 1
            else:
                break

        # 按退出原因分类
        stop_loss_count = sum(1 for t in trades if t["exit_reason"] == "stop_loss")
        take_profit_count = sum(1 for t in trades if t["exit_reason"] == "take_profit")
        signal_count = sum(1 for t in trades if t["exit_reason"] not in ("stop_loss", "take_profit"))

        return {
            "ready": True,
            "trade_count": len(trades),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "consecutive_loss": consecutive_loss,
            "stop_loss_count": stop_loss_count,
            "take_profit_count": take_profit_count,
            "signal_count": signal_count,
            "avg_win": sum(t["pnl_pct"] for t in wins) / max(len(wins), 1),
            "avg_loss": sum(t["pnl_pct"] for t in losses) / max(len(losses), 1),
        }

    # ---------- 冷却检查 ----------

    def _is_cooling_down(self) -> bool:
        """检查是否在冷却期内"""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("""
            SELECT created_at FROM param_history
            WHERE symbol = ?
            ORDER BY id DESC LIMIT 1
        """, (self.symbol,)).fetchone()
        conn.close()

        if not row:
            return False

        last_change = row[0]
        try:
            last_dt = datetime.strptime(last_change, "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.now() - last_dt).total_seconds()
            return elapsed < COOLDOWN_SECONDS
        except ValueError:
            return False

    # ---------- 决策：是否需要调整 ----------

    def _decide_adjustments(self, perf: Dict) -> List[Tuple[str, str, float, float]]:
        """
        根据性能指标决定参数调整方案
        Returns: [(param_name, reason, old_value, new_value), ...]
        """
        if not perf["ready"]:
            return []

        adjustments = []

        # 规则 1：连续止损 → 放宽止损
        if perf["consecutive_loss"] >= MAX_CONSECUTIVE_LOSS:
            old_sl = self.current.get("stop_loss", 0.02)
            new_sl = min(old_sl + ADJUST_STEP["stop_loss"], PARAM_BOUNDS["stop_loss"][1])
            if new_sl != old_sl:
                adjustments.append((
                    "stop_loss",
                    f"连续{perf['consecutive_loss']}次止损，放宽止损",
                    old_sl, round(new_sl, 4),
                ))

        # 规则 2：胜率过低 → 收紧 RSI 阈值 + 放宽止损
        if perf["win_rate"] < MIN_WIN_RATE and perf["trade_count"] >= 5:
            old_os = self.current.get("oversold", 28.0)
            old_ob = self.current.get("overbought", 65.0)
            new_os = max(old_os - ADJUST_STEP["oversold"], PARAM_BOUNDS["oversold"][0])
            new_ob = min(old_ob + ADJUST_STEP["overbought"], PARAM_BOUNDS["overbought"][1])
            if new_os != old_os:
                adjustments.append((
                    "oversold",
                    f"胜率{perf['win_rate']:.0%}<{MIN_WIN_RATE:.0%}，降低超卖阈值",
                    old_os, round(new_os, 1),
                ))
            if new_ob != old_ob:
                adjustments.append((
                    "overbought",
                    f"胜率{perf['win_rate']:.0%}<{MIN_WIN_RATE:.0%}，升高超买阈值",
                    old_ob, round(new_ob, 1),
                ))

        # 规则 3：止盈命中率过高（>60%）→ 扩大止盈
        if perf["trade_count"] >= 5:
            tp_ratio = perf["take_profit_count"] / perf["trade_count"]
            if tp_ratio > 0.6:
                old_tp = self.current.get("take_profit", 0.04)
                new_tp = min(old_tp + ADJUST_STEP["take_profit"], PARAM_BOUNDS["take_profit"][1])
                if new_tp != old_tp:
                    adjustments.append((
                        "take_profit",
                        f"止盈率{tp_ratio:.0%}>60%，扩大止盈目标",
                        old_tp, round(new_tp, 4),
                    ))

        # 规则 4：止损命中率过高（>50%）且无盈利 → 放宽止损
        if perf["trade_count"] >= 5:
            sl_ratio = perf["stop_loss_count"] / perf["trade_count"]
            if sl_ratio > 0.5:
                old_sl = self.current.get("stop_loss", 0.02)
                new_sl = min(old_sl + ADJUST_STEP["stop_loss"] * 2, PARAM_BOUNDS["stop_loss"][1])
                if new_sl != old_sl:
                    adjustments.append((
                        "stop_loss",
                        f"止损率{sl_ratio:.0%}>50%，放宽止损",
                        old_sl, round(new_sl, 4),
                    ))

        # 规则 5：盈亏比过低 → 收紧止损 + 扩大止盈
        if perf["profit_factor"] < MIN_PROFIT_FACTOR and perf["trade_count"] >= 5:
            old_sl = self.current.get("stop_loss", 0.02)
            old_tp = self.current.get("take_profit", 0.04)
            new_sl = max(old_sl - ADJUST_STEP["stop_loss"], PARAM_BOUNDS["stop_loss"][0])
            new_tp = min(old_tp + ADJUST_STEP["take_profit"], PARAM_BOUNDS["take_profit"][1])
            if new_sl != old_sl:
                adjustments.append((
                    "stop_loss",
                    f"盈亏比{perf['profit_factor']:.2f}<{MIN_PROFIT_FACTOR}，收紧止损",
                    old_sl, round(new_sl, 4),
                ))
            if new_tp != old_tp:
                adjustments.append((
                    "take_profit",
                    f"盈亏比{perf['profit_factor']:.2f}<{MIN_PROFIT_FACTOR}，扩大止盈",
                    old_tp, round(new_tp, 4),
                ))

        return adjustments

    # ---------- 执行调整 ----------

    def _apply_adjustment(self, param_name: str, old_value: float, new_value: float, reason: str):
        """应用单次参数调整"""
        # 锚点约束：不超过锚点的 ±30%
        if param_name in self.anchor:
            anchor_val = self.anchor[param_name]
            lower = anchor_val * 0.7
            upper = anchor_val * 1.3
            new_value = max(lower, min(upper, new_value))

        # 全局边界
        if param_name in PARAM_BOUNDS:
            lower, upper = PARAM_BOUNDS[param_name]
            new_value = max(lower, min(upper, new_value))

        new_value = round(new_value, 4)
        self.current[param_name] = new_value

        # 持久化
        conn = sqlite3.connect(self.db_path)
        perf = self._evaluate_performance()
        conn.execute("""
            INSERT INTO param_history (symbol, param_name, old_value, new_value, reason, trade_count, win_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (self.symbol, param_name, old_value, new_value, reason,
              perf.get("trade_count", 0), perf.get("win_rate", 0)))
        conn.commit()
        conn.close()

        logger.info(
            f"[优化器] {self.symbol} {param_name}: {old_value} → {new_value} ({reason})"
        )

    def maybe_adjust(self, symbol: str) -> Dict:
        """
        主入口：评估性能，必要时调整参数。

        Returns:
            dict: {"adjusted": bool, "changes": [...], "perf": {...}}
        """
        if self._is_cooling_down():
            return {"adjusted": False, "reason": "冷却期内"}

        perf = self._evaluate_performance()
        if not perf["ready"]:
            return {"adjusted": False, "perf": perf}

        adjustments = self._decide_adjustments(perf)
        if not adjustments:
            return {"adjusted": False, "perf": perf}

        changes = []
        for param_name, reason, old_val, new_val in adjustments:
            self._apply_adjustment(param_name, old_val, new_val, reason)
            changes.append({"param": param_name, "old": old_val, "new": new_val, "reason": reason})

        logger.info(f"[优化器] {self.symbol} 完成 {len(changes)} 项参数调整")
        return {"adjusted": True, "changes": changes, "perf": perf}

    # ---------- 查询 ----------

    def get_current_params(self) -> Dict:
        return dict(self.current)

    def get_history(self, limit: int = 20) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT param_name, old_value, new_value, reason, trade_count, win_rate, created_at
            FROM param_history
            WHERE symbol = ?
            ORDER BY id DESC LIMIT ?
        """, (self.symbol, limit)).fetchall()
        conn.close()
        return [
            {"param": r[0], "old": r[1], "new": r[2], "reason": r[3],
             "trades": r[4], "win_rate": r[5], "time": r[6]}
            for r in rows
        ]

    def set_current_params(self, params: Dict):
        """从外部同步当前参数（TradingAgent 初始化时调用）"""
        self.current = dict(params)
        if not self.anchor:
            self.anchor = dict(params)
