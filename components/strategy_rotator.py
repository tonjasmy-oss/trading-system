"""
strategy_rotator.py — 市场状态感知策略轮动 (v2: 业绩感知)
========================================================

根据 MarketRegime 检测的市场状态 + 实盘表现，自动选择最优策略：

  状态                 → 推荐策略
  ──────────────────────────────────────────
  uptrend + high_vol   → DONCHIAN (通道突破最强)
  uptrend + medium_vol → DONCHIAN
  uptrend + low_vol    → SMA      (简单均线趋势)
  downtrend + high_vol → DONCHIAN (通道向下突破做空)
  downtrend + medium_vol→ RSI      (超卖做多)
  downtrend + low_vol  → RSI      (超卖做多)
  ranging  + high_vol  → ATRSTOP  (震荡+高波动，动态止损防假突破)
  ranging  + medium_vol → BOLLINGER(布林带均值回归)
  ranging  + low_vol   → KDJ      (摆动交易)
  unknown              → MACD     (默认稳健)

v2 新增:
  - strategy_performance 表追踪每策略每市场状态的实盘表现
  - 轮动时融合 regime fit_score + actual performance → 综合决策
  - record_outcome() 在每次平仓后更新业绩数据
"""

import os
import sqlite3
import logging
logger = logging.getLogger(__name__)

REGIME_STRATEGY_MAP = {
    ("uptrend", "high"):    ("DONCHIAN", "上升趋势+高波动，通道突破捕捉主升浪", {"channel_period": 20, "trend_ema_period": 30}),
    ("uptrend", "medium"):  ("DONCHIAN", "上升趋势+中等波动，通道突破策略", {"channel_period": 20, "trend_ema_period": 40}),
    ("uptrend", "low"):     ("SMA",      "上升趋势+低波动，均线趋势", {"fast_period": 10, "slow_period": 30}),
    ("downtrend", "high"):  ("DONCHIAN", "下降趋势+高波动，通道向下突破做空", {"channel_period": 20, "trend_ema_period": 30}),
    ("downtrend", "medium"):("RSI",      "下降趋势+中等波动，超卖做多", {"rsi_period": 14, "oversold": 28, "overbought": 55}),
    ("downtrend", "low"):   ("RSI",      "下降趋势+低波动，超卖做多", {"rsi_period": 14, "oversold": 28, "overbought": 55}),
    ("ranging", "high"):    ("ATRSTOP",  "震荡市+高波动，ATR动态止损(回测最优)", {"ema_period": 20, "atr_multiplier": 2.0}),
    ("ranging", "medium"):  ("BOLLINGER","震荡市+中等波动，布林带均值回归", {"period": 20, "std_dev": 2.0}),
    ("ranging", "low"):     ("KDJ",      "震荡市+低波动，KDJ摆动交易", {}),
}
FALLBACK_STRATEGY = ("MACD", "市场状态未知，回退到MACD", {})

# 策略适配度评分表（基于 market_regime.STRATEGY_RECOMMENDATIONS 同步）
_STRATEGY_FIT_SCORE = {
    ("DONCHIAN", "uptrend", "high"): 95,
    ("DONCHIAN", "uptrend", "medium"): 92,
    ("DONCHIAN", "uptrend", "low"): 70,
    ("DONCHIAN", "downtrend", "high"): 75,
    ("DONCHIAN", "downtrend", "medium"): 72,
    ("DONCHIAN", "ranging", "high"): 40,
    ("DONCHIAN", "ranging", "medium"): 30,
    ("DONCHIAN", "ranging", "low"): 25,
    ("BOLLINGER", "ranging", "high"): 92,
    ("BOLLINGER", "ranging", "medium"): 90,
    ("BOLLINGER", "ranging", "low"): 80,
    ("BOLLINGER", "downtrend", "high"): 70,
    ("RSI", "downtrend", "high"): 88,
    ("RSI", "downtrend", "medium"): 85,
    ("RSI", "downtrend", "low"): 80,
    ("RSI", "ranging", "medium"): 75,
    ("KDJ", "ranging", "low"): 88,
    ("KDJ", "ranging", "medium"): 82,
    ("KDJ", "downtrend", "low"): 72,
    ("SMA", "uptrend", "low"): 90,
    ("SMA", "uptrend", "medium"): 85,
    ("ATRSTOP", "uptrend", "high"): 90,
    ("ATRSTOP", "uptrend", "medium"): 82,
    ("ATRSTOP", "ranging", "high"): 78,
    ("MACD", "unknown", "unknown"): 60,
}


class StrategyRotator:
    """市场状态感知策略轮动器（v2: 业绩感知）"""

    def __init__(self, symbol: str = "", timeframe: str = "",
                 config_map: dict = None, stability: int = 2,
                 db_path: str = "live_trading.db"):
        self.symbol = symbol
        self.timeframe = timeframe
        self.db_path = db_path
        self.config_map = config_map or {}
        self._last_strategy: str = ""
        self._last_regime_key: tuple = ("", "")
        self._stability_counter: int = 0
        self._min_stability: int = stability
        self._init_db()

    # ---------- 数据库 ----------

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS strategy_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy TEXT NOT NULL,
                regime_trend TEXT NOT NULL,
                regime_volatility TEXT NOT NULL,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                total_pnl_pct REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                avg_pnl_pct REAL DEFAULT 0,
                last_trade_at TEXT,
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(symbol, timeframe, strategy, regime_trend, regime_volatility)
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_perf_symbol
                ON strategy_performance(symbol, timeframe);
        """)
        conn.commit()
        conn.close()

    # ---------- 业绩记录 ----------

    def record_outcome(self, strategy: str, regime_trend: str,
                       regime_volatility: str, pnl_pct: float):
        """
        每次平仓后调用，更新该策略在当前市场状态下的表现。
        """
        if not self.symbol or not self.timeframe:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            existing = conn.execute(
                "SELECT total_trades, wins, total_pnl_pct FROM strategy_performance "
                "WHERE symbol=? AND timeframe=? AND strategy=? AND regime_trend=? AND regime_volatility=?",
                (self.symbol, self.timeframe, strategy, regime_trend, regime_volatility)
            ).fetchone()

            if existing:
                total, wins, total_pnl = existing
                total += 1
                if pnl_pct > 0:
                    wins += 1
                total_pnl += pnl_pct
            else:
                total = 1
                wins = 1 if pnl_pct > 0 else 0
                total_pnl = pnl_pct

            profit_factor = 1.0
            # 从 trades 表计算更准确的盈亏比
            loss_sum = conn.execute(
                "SELECT COALESCE(SUM(ABS(pnl_pct)), 0) FROM trades WHERE symbol=? AND pnl_pct<0 AND exit_reason IS NOT NULL",
                (self.symbol,)
            ).fetchone()[0]
            win_sum = conn.execute(
                "SELECT COALESCE(SUM(pnl_pct), 0) FROM trades WHERE symbol=? AND pnl_pct>0",
                (self.symbol,)
            ).fetchone()[0]
            if loss_sum > 0:
                profit_factor = round(win_sum / loss_sum, 2)

            avg_pnl = round(total_pnl / total, 2)

            conn.execute(
                "INSERT OR REPLACE INTO strategy_performance "
                "(symbol, timeframe, strategy, regime_trend, regime_volatility, "
                " total_trades, wins, total_pnl_pct, profit_factor, avg_pnl_pct, last_trade_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))",
                (self.symbol, self.timeframe, strategy, regime_trend, regime_volatility,
                 total, wins, round(total_pnl, 2), profit_factor, avg_pnl)
            )
            conn.commit()
            conn.close()
            logger.debug(f"[策略轮动] {self.symbol} {strategy} @ {regime_trend}/{regime_volatility}: "
                        f"{total}笔 胜率={wins/total:.0%} PF={profit_factor}")
        except Exception as e:
            logger.debug(f"[策略轮动] record_outcome 异常: {e}")

    # ---------- 业绩查询 ----------

    def _get_performance(self, strategy: str, trend: str, volatility: str) -> dict:
        """查询某策略在某市场状态下的实盘表现"""
        try:
            conn = sqlite3.connect(self.db_path)
            # 优先精确匹配
            row = conn.execute(
                "SELECT total_trades, wins, total_pnl_pct, profit_factor, avg_pnl_pct "
                "FROM strategy_performance "
                "WHERE symbol=? AND timeframe=? AND strategy=? AND regime_trend=? AND regime_volatility=?",
                (self.symbol, self.timeframe, strategy, trend, volatility)
            ).fetchone()
            # 回退：跨 regime 查找（回测数据可能存储在不同 regime 下）
            if not row:
                row = conn.execute(
                    "SELECT total_trades, wins, total_pnl_pct, profit_factor, avg_pnl_pct "
                    "FROM strategy_performance "
                    "WHERE symbol=? AND timeframe=? AND strategy=? "
                    "ORDER BY total_trades DESC LIMIT 1",
                    (self.symbol, self.timeframe, strategy)
                ).fetchone()
            conn.close()
            if row and row[0] > 0:
                return {
                    "total_trades": row[0], "wins": row[1],
                    "total_pnl_pct": row[2], "profit_factor": row[3],
                    "avg_pnl_pct": row[4],
                    "win_rate": row[1] / row[0],
                }
        except Exception:
            pass
        return {}

    def _apply_performance_penalty(self, strategy: str, fit_score: int,
                                    trend: str, volatility: str) -> int:
        """
        根据实盘表现在 fit_score 上加调整分。
        表现差 → 降权；表现好 → 加权重。
        """
        perf = self._get_performance(strategy, trend, volatility)
        if not perf:
            return fit_score  # 无实盘数据，保持原分

        trades = perf["total_trades"]
        if trades < 3:
            return fit_score  # 样本太小，不调整

        pf = perf["profit_factor"]
        wr = perf["win_rate"]
        avg_pnl = perf["avg_pnl_pct"]

        adjustment = 0

        # 总收益极端负值 → 重度惩罚（覆盖单个 per-trade 平均值不足的问题）
        total_pnl = perf["total_pnl_pct"]
        if total_pnl < -50.0 and trades >= 10:
            adjustment -= 60
        elif total_pnl < -20.0 and trades >= 10:
            adjustment -= 40
        elif total_pnl < -5.0 and trades >= 10:
            adjustment -= 20

        # 强负面信号：盈亏比极差或大幅亏损
        if pf < 0.3 or avg_pnl < -3.0:
            adjustment -= 40
        elif pf < 0.5 or wr < 0.25:
            adjustment -= 25
        elif pf < 0.8 or wr < 0.35:
            adjustment -= 10

        # 正面信号：表现优秀
        if pf > 2.0 or wr > 0.6:
            adjustment += 15
        elif pf > 1.2 and wr > 0.45:
            adjustment += 5

        new_score = max(0, min(100, fit_score + adjustment))
        if adjustment != 0:
            logger.info(
                f"[策略轮动] {self.symbol} {strategy} @ {trend}/{volatility}: "
                f"fit={fit_score} + perf_adj={adjustment:+d} = {new_score} "
                f"(trades={trades} wr={wr:.0%} pf={pf} avgPnl={avg_pnl}%)"
            )
        return new_score

    # ---------- 轮动核心 ----------

    def pick(self, regime: dict) -> dict:
        trend = (regime.get("trend") or "unknown").lower()
        vol = (regime.get("volatility") or "unknown").lower()
        confidence = regime.get("confidence", 0.5)
        key = (trend, vol)
        strategy_name, reason, kwargs = REGIME_STRATEGY_MAP.get(key, FALLBACK_STRATEGY)

        # ── 业绩感知：检查推荐策略的实盘表现 ──
        perf_adjusted_score = self._apply_performance_penalty(
            strategy_name, 100, trend, vol)  # 基分 100 作为参考

        # 如果推荐策略被大幅降权(<30)，尝试找替代
        if perf_adjusted_score < 30:
            alternatives = self._find_better_alternative(strategy_name, trend, vol)
            if alternatives:
                alt_strategy, alt_score, alt_reason = alternatives
                logger.warning(
                    f"[策略轮动] {self.symbol} {trend}+{vol}: "
                    f"推荐{strategy_name}(业绩差，得分{perf_adjusted_score}) "
                    f"→ 替代{alternatives[0]}(得分{alt_score})"
                )
                strategy_name = alt_strategy
                reason = f"{reason} (业绩替代: {alt_reason})"
                # 用集合安全检查（dict(REGIME_STRATEGY_MAP.values()) 对 3 元组会报错）
                _strategy_names = {v[0] for v in REGIME_STRATEGY_MAP.values()}
                kwargs = REGIME_STRATEGY_MAP.get(
                    next((k for k in REGIME_STRATEGY_MAP
                          if REGIME_STRATEGY_MAP[k][0] == alt_strategy),
                         ("", "", {})),
                    ({},)
                )[2] if alt_strategy in _strategy_names else {}

        # 稳定期检查
        if key == self._last_regime_key:
            self._stability_counter += 1
        else:
            self._stability_counter = 0
        self._last_regime_key = key

        if self._stability_counter < self._min_stability and self._last_strategy:
            reason = f"待确认: {reason} ({self._stability_counter+1}/{self._min_stability})"
            strategy_name = self._last_strategy
            kwargs = {}

        if self._stability_counter >= self._min_stability:
            self._last_strategy = strategy_name

        symbol_overrides = self.config_map.get(self.symbol, {})
        strategy_overrides = symbol_overrides.get(strategy_name, {})
        final_kwargs = {**kwargs, **strategy_overrides}

        logger.info(f"[策略轮动] {self.symbol} {trend}+{vol} → {strategy_name} "
                    f"({reason}) 置信度={confidence:.2f}")
        return {
            "strategy": strategy_name,
            "reason": reason,
            "kwargs": final_kwargs,
            "confidence": confidence,
            "regime_trend": trend,
            "regime_volatility": vol,
        }

    def _find_better_alternative(self, excluded: str, trend: str, volatility: str):
        """在当前市场状态下，找除 excluded 外表现最好的策略"""
        candidates = []
        for st in ["DONCHIAN", "ATRSTOP", "BOLLINGER", "RSI", "SMA", "KDJ", "MACD"]:
            if st == excluded:
                continue
            fit = self.score_fit(st, trend, volatility)
            adj_fit = self._apply_performance_penalty(st, fit, trend, volatility)
            if adj_fit >= 25:
                candidates.append((st, adj_fit))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best = candidates[0]
            return (best[0], best[1], f"regime_fit={self.score_fit(best[0], trend, volatility)}")
        return None

    def get_better_strategies(self, current_strategy: str, regime: dict, top_n: int = 3) -> list:
        """
        获取比当前策略更适合的策略推荐列表。
        原方法，保持兼容；新增业绩调整。
        """
        trend = (regime.get("trend") or "unknown").lower()
        vol = (regime.get("volatility") or "unknown").lower()
        current_fit = self.score_fit(current_strategy, trend, vol)

        candidates = []
        for st in ["DONCHIAN", "ATRSTOP", "BOLLINGER", "RSI", "SMA", "KDJ", "MACD"]:
            if st == current_strategy:
                continue
            fit = self.score_fit(st, trend, volatility=vol)
            adj_fit = self._apply_performance_penalty(st, fit, trend, vol)
            if adj_fit > current_fit:
                candidates.append({
                    "strategy": st,
                    "fit_score": adj_fit,
                    "reason": f"市场适配度={fit}" + (f" 实盘调整后={adj_fit}" if adj_fit != fit else ""),
                })

        candidates.sort(key=lambda x: x["fit_score"], reverse=True)
        return candidates[:top_n]

    def get_current_strategy(self) -> str:
        return self._last_strategy or "MACD"

    def reset(self):
        self._last_strategy = ""
        self._last_regime_key = ("", "")
        self._stability_counter = 0

    def score_fit(self, strategy_name: str, trend: str, volatility: str) -> int:
        key = (strategy_name.upper(), trend.lower(), volatility.lower())
        if key in _STRATEGY_FIT_SCORE:
            return _STRATEGY_FIT_SCORE[key]
        try:
            from components.market_regime import recommend_strategies
            recs = recommend_strategies(trend, volatility, top_n=10)
            for rec in recs:
                if rec["strategy"].upper() == strategy_name.upper():
                    return rec["fit_score"]
        except ImportError:
            pass
        return 30

    def is_current_strategy_appropriate(self, regime: dict, threshold: int = 50) -> bool:
        if not self._last_strategy:
            return True
        trend = (regime.get("trend") or "unknown").lower()
        vol = (regime.get("volatility") or "unknown").lower()
        return self.score_fit(self._last_strategy, trend, vol) >= threshold