"""
OnlineParameterOptimizer — 在线参数自动优化
============================================

v2: 策略感知 + 参数持久化
  - 参数按策略分域（RSI调RSI参数，ATRSTOP调ATR参数）
  - optimizer_state 表持久化，重启不丢
  - 策略轮动时自动切换参数集

实时追踪每笔交易结果，在检测到性能衰减时自动微调策略参数。

用法：
  opt = OnlineParameterOptimizer(symbol="ETH/USDT", strategy="RSI")
  opt.maybe_adjust("ETH/USDT")    # 平仓后调用
  opt.switch_strategy("ATRSTOP")  # 策略轮动时调用
"""

import logging
import sqlite3
import time
from datetime import datetime
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# 参数边界（防止微调跑偏）
# ============================================================

PARAM_BOUNDS = {
    "rsi_period":      (6, 30),
    "oversold":        (10.0, 40.0),
    "overbought":      (55.0, 85.0),
    "stop_loss":       (0.005, 0.10),
    "take_profit":     (0.01, 0.20),
    "ema_period":      (5, 50),
    "atr_period":      (7, 42),
    "atr_multiplier":  (1.0, 5.0),
    "channel_period":  (10, 60),
    "trend_ema_period":(20, 100),
    "period":          (10, 50),       # BOLLINGER
    "std_dev":         (1.0, 4.0),     # BOLLINGER
    "fast_period":     (5, 30),        # SMA
    "slow_period":     (15, 60),       # SMA
}

ADJUST_STEP = {
    "rsi_period":      1,
    "oversold":        1.0,
    "overbought":      1.0,
    "stop_loss":       0.0025,
    "take_profit":     0.005,
    "ema_period":      2,
    "atr_period":      2,
    "atr_multiplier":  0.25,
    "channel_period":  2,
    "trend_ema_period": 5,
    "period":          2,
    "std_dev":         0.25,
    "fast_period":     2,
    "slow_period":     5,
}

# 各策略可调参数域
STRATEGY_PARAMS = {
    "RSI":       ["rsi_period", "oversold", "overbought", "stop_loss", "take_profit"],
    "ATRSTOP":   ["ema_period", "atr_period", "atr_multiplier", "stop_loss", "take_profit"],
    "DONCHIAN":  ["channel_period", "trend_ema_period", "stop_loss", "take_profit"],
    "BOLLINGER": ["period", "std_dev", "stop_loss", "take_profit"],
    "KDJ":       ["stop_loss", "take_profit"],
    "SMA":       ["fast_period", "slow_period", "stop_loss", "take_profit"],
    "MACD":      ["stop_loss", "take_profit"],
    "MULTIFACTOR": ["stop_loss", "take_profit"],
}

COOLDOWN_SECONDS = 24 * 3600
EVAL_WINDOW = 10
MIN_WIN_RATE = 0.35
MIN_PROFIT_FACTOR = 0.8
MAX_CONSECUTIVE_LOSS = 3


class OnlineParameterOptimizer:
    """在线参数优化器 — 每标的每策略独立实例（v2 策略感知 + 持久化）"""

    def __init__(self, symbol: str, db_path: str = "live_trading.db",
                 anchor_params: Optional[Dict] = None,
                 strategy: str = "RSI"):
        self.symbol = symbol
        self.db_path = db_path
        self.strategy = strategy
        # 锚点参数（Grid Search 最优，不可偏离超过 ±30%）
        self.anchor = anchor_params or {}
        self._init_db()
        # 当前生效参数（优先从 DB 恢复，否则用锚点）
        saved = self._load_state()
        if saved:
            self.current = saved
            logger.debug(f"[优化器] {symbol}/{strategy} 从DB恢复参数: {saved}")
        else:
            self.current = dict(self.anchor) if self.anchor else {}
            self._save_state()

    # ---------- 数据库 ----------

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS param_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'RSI',
                param_name TEXT NOT NULL,
                old_value REAL,
                new_value REAL,
                reason TEXT,
                trade_count INTEGER,
                win_rate REAL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_param_history_symbol
                ON param_history(symbol, strategy, created_at);
            CREATE TABLE IF NOT EXISTS optimizer_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                param_name TEXT NOT NULL,
                current_value REAL NOT NULL,
                anchor_value REAL,
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(symbol, strategy, param_name)
            );
        """)
        conn.commit()
        conn.close()

    def _load_state(self) -> Optional[Dict]:
        """从 DB 加载当前策略的参数状态"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT param_name, current_value FROM optimizer_state WHERE symbol=? AND strategy=?",
            (self.symbol, self.strategy)
        ).fetchall()
        conn.close()
        if rows:
            return {r[0]: r[1] for r in rows}
        return None

    def _save_state(self):
        """将当前参数持久化到 DB"""
        conn = sqlite3.connect(self.db_path)
        for param, val in self.current.items():
            anchor_val = self.anchor.get(param)
            conn.execute(
                "INSERT OR REPLACE INTO optimizer_state (symbol, strategy, param_name, current_value, anchor_value) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.symbol, self.strategy, param, float(val), anchor_val)
            )
        conn.commit()
        conn.close()

    # ---------- 策略轮动时切换参数集 ----------

    def switch_strategy(self, new_strategy: str, anchor_params: Optional[Dict] = None):
        """
        策略轮动时调用：保存当前策略参数 → 切换到新策略参数集。
        """
        if new_strategy == self.strategy:
            return
        self._save_state()
        logger.info(f"[优化器] {self.symbol} 策略切换: {self.strategy} → {new_strategy}")
        self.strategy = new_strategy
        if anchor_params:
            self.anchor = anchor_params
        saved = self._load_state()
        if saved:
            self.current = saved
        else:
            self.current = dict(self.anchor) if self.anchor else {}
            self._save_state()

    # ---------- 交易记录 ----------

    def record_trade(self, exit_reason: str, pnl_pct: float):
        pass

    # ---------- 核心：性能评估 ----------

    def _get_recent_trades(self, limit: int = EVAL_WINDOW) -> List[Dict]:
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
        trades = self._get_recent_trades()
        if len(trades) < 3:
            return {"ready": False, "reason": f"交易不足（{len(trades)}<3）"}

        wins = [t for t in trades if (t["pnl_pct"] or 0) > 0]
        losses = [t for t in trades if (t["pnl_pct"] or 0) <= 0]
        win_rate = len(wins) / len(trades) if trades else 0

        total_profit = sum(t["pnl_pct"] for t in wins)
        total_loss = sum(abs(t["pnl_pct"]) for t in losses)
        profit_factor = total_profit / max(total_loss, 0.001)

        consecutive_loss = 0
        for t in trades:
            if (t["pnl_pct"] or 0) <= 0:
                consecutive_loss += 1
            else:
                break

        stop_loss_count = sum(1 for t in trades if t["exit_reason"] == "stop_loss")
        take_profit_count = sum(1 for t in trades if t["exit_reason"] == "take_profit")

        return {
            "ready": True,
            "trade_count": len(trades),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "consecutive_loss": consecutive_loss,
            "stop_loss_count": stop_loss_count,
            "take_profit_count": take_profit_count,
            "avg_win": sum(t["pnl_pct"] for t in wins) / max(len(wins), 1),
            "avg_loss": sum(t["pnl_pct"] for t in losses) / max(len(losses), 1),
        }

    # ---------- 冷却检查 ----------

    def _is_cooling_down(self) -> bool:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("""
            SELECT created_at FROM param_history
            WHERE symbol = ? AND strategy = ?
            ORDER BY id DESC LIMIT 1
        """, (self.symbol, self.strategy)).fetchone()
        conn.close()
        if not row:
            return False
        try:
            last_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - last_dt).total_seconds() < COOLDOWN_SECONDS
        except ValueError:
            return False

    # ---------- 决策：投票制 ----------

    def _is_strategy_param(self, param: str) -> bool:
        """检查参数是否属于当前策略的可调域"""
        allowed = STRATEGY_PARAMS.get(self.strategy, ["stop_loss", "take_profit"])
        return param in allowed

    def _decide_adjustments(self, perf: Dict) -> List[Tuple[str, str, float, float]]:
        """
        投票制参数调整决策。每个参数取净方向 + 最大步长。
        """
        if not perf["ready"]:
            return []

        votes: Dict[str, dict] = {}

        def _vote(param: str, direction: int, reason: str, step: float):
            if not self._is_strategy_param(param):
                return  # 只调整当前策略的参数
            if param not in votes:
                votes[param] = {"net": 0, "reasons": [], "step": 0.0}
            votes[param]["net"] += direction
            votes[param]["reasons"].append(reason)
            if step > votes[param]["step"]:
                votes[param]["step"] = step

        # ── 通用规则（stop_loss / take_profit）──

        # 规则 1：连续止损 → 放宽止损
        if perf["consecutive_loss"] >= MAX_CONSECUTIVE_LOSS:
            _vote("stop_loss", +1,
                  f"连续{perf['consecutive_loss']}次止损，放宽",
                  ADJUST_STEP["stop_loss"])

        # 规则 2：胜率过低 → 策略专属 + 通用
        if perf["win_rate"] < MIN_WIN_RATE and perf["trade_count"] >= 5:
            if self.strategy == "RSI":
                _vote("oversold", -1, f"胜率{perf['win_rate']:.0%}", ADJUST_STEP["oversold"])
                _vote("overbought", +1, f"胜率{perf['win_rate']:.0%}", ADJUST_STEP["overbought"])
            elif self.strategy == "ATRSTOP":
                _vote("atr_multiplier", +1,
                      f"胜率{perf['win_rate']:.0%}，放大ATR给更多空间",
                      ADJUST_STEP["atr_multiplier"])
            elif self.strategy == "DONCHIAN":
                _vote("channel_period", +1,
                      f"胜率{perf['win_rate']:.0%}，加大通道周期减假突破",
                      ADJUST_STEP["channel_period"])

        # 规则 3：止盈率过高 → 扩大止盈
        if perf["trade_count"] >= 5:
            tp_ratio = perf["take_profit_count"] / perf["trade_count"]
            if tp_ratio > 0.6:
                _vote("take_profit", +1,
                      f"止盈率{tp_ratio:.0%}>60%，扩大止盈",
                      ADJUST_STEP["take_profit"])

        # 规则 4：止损率过高 → 放宽止损
        if perf["trade_count"] >= 5:
            sl_ratio = perf["stop_loss_count"] / perf["trade_count"]
            if sl_ratio > 0.5:
                _vote("stop_loss", +2, f"止损率{sl_ratio:.0%}>50%",
                      ADJUST_STEP["stop_loss"] * 2)

        # 规则 5：盈亏比过低
        if perf["profit_factor"] < MIN_PROFIT_FACTOR and perf["trade_count"] >= 5:
            _vote("stop_loss", -1, f"盈亏比{perf['profit_factor']:.2f}", ADJUST_STEP["stop_loss"])
            _vote("take_profit", +1, f"盈亏比{perf['profit_factor']:.2f}", ADJUST_STEP["take_profit"])

        # ── 策略专属规则 ──

        # ATRSTOP：止损率过高 → 放大 atr_multiplier（给趋势更多呼吸空间）
        if self.strategy == "ATRSTOP" and perf["trade_count"] >= 5:
            sl_ratio = perf["stop_loss_count"] / perf["trade_count"]
            if sl_ratio > 0.6:
                _vote("atr_multiplier", +1, f"止损率{sl_ratio:.0%}>60%，放大ATR乘数",
                      ADJUST_STEP["atr_multiplier"])

        # DONCHIAN：假突破多（连续止损）→ 加大通道周期
        if self.strategy == "DONCHIAN" and perf["consecutive_loss"] >= MAX_CONSECUTIVE_LOSS:
            _vote("channel_period", +1,
                  f"连续{perf['consecutive_loss']}次止损，加大通道周期减假突破",
                  ADJUST_STEP["channel_period"])

        # BOLLINGER：频繁触发 → 放宽带宽
        if self.strategy == "BOLLINGER" and perf["trade_count"] >= 5:
            if perf["stop_loss_count"] / perf["trade_count"] > 0.5:
                _vote("std_dev", +1, "止损率>50%，放宽布林带",
                      ADJUST_STEP["std_dev"])

        # ── 投票结果 → 调整列表 ──
        adjustments = []
        for param, v in votes.items():
            if v["net"] == 0:
                continue
            old_val = self.current.get(param, PARAM_BOUNDS.get(param, (0, 1))[0])
            step_dir = 1 if v["net"] > 0 else -1
            new_val = old_val + v["step"] * step_dir
            if param in PARAM_BOUNDS:
                lower, upper = PARAM_BOUNDS[param]
                new_val = max(lower, min(upper, new_val))
            new_val = round(new_val, 4)
            if new_val == old_val:
                continue
            reason_text = " + ".join(v["reasons"])
            adjustments.append((param, reason_text, old_val, new_val))

        return adjustments

    # ---------- 执行调整 ----------

    def _apply_adjustment(self, param_name: str, old_value: float, new_value: float, reason: str):
        if param_name in self.anchor:
            anchor_val = self.anchor[param_name]
            new_value = max(anchor_val * 0.7, min(anchor_val * 1.3, new_value))
        if param_name in PARAM_BOUNDS:
            lower, upper = PARAM_BOUNDS[param_name]
            new_value = max(lower, min(upper, new_value))
        new_value = round(new_value, 4)
        self.current[param_name] = new_value
        self._save_state()

        conn = sqlite3.connect(self.db_path)
        perf = self._evaluate_performance()
        conn.execute("""
            INSERT INTO param_history (symbol, strategy, param_name, old_value, new_value, reason, trade_count, win_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.symbol, self.strategy, param_name, old_value, new_value, reason,
              perf.get("trade_count", 0), perf.get("win_rate", 0)))
        conn.commit()
        conn.close()

        logger.info(f"[优化器] {self.symbol}/{self.strategy} {param_name}: {old_value} → {new_value} ({reason})")

    def maybe_adjust(self, symbol: str) -> Dict:
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

        logger.info(f"[优化器] {self.symbol}/{self.strategy} 完成 {len(changes)} 项参数调整")
        return {"adjusted": True, "changes": changes, "perf": perf}

    # ---------- 查询 ----------

    def get_current_params(self) -> Dict:
        return dict(self.current)

    def get_history(self, limit: int = 20) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT param_name, old_value, new_value, reason, trade_count, win_rate, created_at
            FROM param_history
            WHERE symbol = ? AND strategy = ?
            ORDER BY id DESC LIMIT ?
        """, (self.symbol, self.strategy, limit)).fetchall()
        conn.close()
        return [
            {"param": r[0], "old": r[1], "new": r[2], "reason": r[3],
             "trades": r[4], "win_rate": r[5], "time": r[6]}
            for r in rows
        ]

    def set_current_params(self, params: Dict):
        for k, v in params.items():
            if k in PARAM_BOUNDS:
                lower, upper = PARAM_BOUNDS[k]
                self.current[k] = max(lower, min(upper, float(v)))
            else:
                self.current[k] = v
        self._save_state()
