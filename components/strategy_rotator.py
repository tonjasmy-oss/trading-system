"""
strategy_rotator.py — 市场状态感知策略轮动 (v3: 回测+业绩融合)
========================================================

根据 MarketRegime 检测的市场状态 + 回测数据 + 实盘表现，自动选择最优策略。

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

v3 新增:
  - 回测数据预加载，作为初始策略评分基线
  - 融合: fit_score + backtest_score * (1-w) + live_perf_score * w
  - w = min(live_trades / 20, 1.0) — 实盘交易越多，实盘权重越高
"""

import os
import json
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
    """市场状态感知策略轮动器（v3: 回测+业绩融合）"""

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
        self._bt_results = self._load_backtest_results()

    # ---------- 回测数据加载 ----------

    def _load_backtest_results(self) -> dict:
        """从 backtest_results/ 加载所有回测数据"""
        bt_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "backtest_results"
        )
        results = {}
        if not os.path.isdir(bt_dir):
            return results

        strategy_alias = {
            "RSIStrategy": "RSI", "SMAcrossStrategy": "SMA", "MACDStrategy": "MACD",
            "BollingerBandsStrategy": "BOLLINGER", "KDJStrategy": "KDJ",
            "ATRStopStrategy": "ATRSTOP", "DonchianChannelStrategy": "DONCHIAN",
            "MultiFactorTrendStrategy": "MULTIFACTOR",
        }

        for fname in sorted(os.listdir(bt_dir)):
            if not fname.endswith('.json'):
                continue
            try:
                with open(os.path.join(bt_dir, fname)) as f:
                    data = json.load(f)
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                sym = item.get("symbol", "")
                strat_raw = item.get("strategy", "")
                tf = item.get("timeframe", "")
                if not sym or not strat_raw or not tf:
                    continue
                alias = strategy_alias.get(strat_raw, strat_raw)
                # Ignore custom BNB strategy names (they get mapped via BNB prefix)
                # Store under the standard alias
                key = (sym, alias, tf)
                # Later files override earlier ones (they're sorted by name ~ time)
                results[key] = {
                    "return_pct": item.get("total_return_pct", 0),
                    "win_rate": item.get("win_rate_pct", 0) / 100.0 if item.get("win_rate_pct", 0) > 1 else item.get("win_rate_pct", 0),
                    "profit_factor": item.get("profit_factor", 0),
                    "sharpe": item.get("sharpe_ratio", 0),
                    "max_dd": item.get("max_drawdown_pct", 0),
                    "trades": item.get("total_trades", 0),
                }
        return results

    def _get_backtest_score(self, strategy: str) -> int:
        """根据回测数据评分: -25 ~ +25"""
        key = (self.symbol, strategy.upper(), self.timeframe)
        bt = self._bt_results.get(key)
        if not bt or bt["trades"] < 5:
            return 0

        score = 0
        ret = bt["return_pct"]
        pf = bt["profit_factor"]
        wr = bt["win_rate"]

        if ret > 80:       score += 20
        elif ret > 40:     score += 15
        elif ret > 15:     score += 10
        elif ret > 0:      score += 5
        elif ret < -50:    score -= 25
        elif ret < -20:    score -= 15
        elif ret < -5:     score -= 5

        if pf > 2.0:       score += 10
        elif pf > 1.5:     score += 5
        elif pf < 0.5:     score -= 10

        if wr > 0.6:       score += 5
        elif wr < 0.3:     score -= 5

        if bt["trades"] < 20:
            score = int(score * 0.5)

        return max(-25, min(25, score))

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
        except Exception as e:
            logger.debug(f"[策略轮动] record_outcome exception: {e}")

    # ---------- 业绩查询 ----------

    def _get_performance(self, strategy: str, trend: str, volatility: str) -> dict:
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT total_trades, wins, total_pnl_pct, profit_factor, avg_pnl_pct "
                "FROM strategy_performance "
                "WHERE symbol=? AND timeframe=? AND strategy=? AND regime_trend=? AND regime_volatility=?",
                (self.symbol, self.timeframe, strategy, trend, volatility)
            ).fetchone()
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

    # ---------- 综合评分 (v3: 回测+实盘融合) ----------

    def _compute_combined_score(self, strategy: str, fit_score: int,
                                 trend: str, volatility: str) -> int:
        """
        融合评分 = fit_score + backtest_adj * (1-w) + live_adj * w
        w = min(live_trades / 20, 1.0)
        """
        perf = self._get_performance(strategy, trend, volatility)
        live_trades = perf.get("total_trades", 0)
        w = min(live_trades / 20.0, 1.0)

        # 回测评分
        bt_score = self._get_backtest_score(strategy)

        # 实盘评分 (same logic as before)
        live_score = 0
        if live_trades >= 3:
            pf = perf["profit_factor"]
            wr = perf["win_rate"]
            avg_pnl = perf["avg_pnl_pct"]
            total_pnl = perf["total_pnl_pct"]
            if total_pnl < -50.0 and live_trades >= 10:
                live_score -= 60
            elif total_pnl < -20.0 and live_trades >= 10:
                live_score -= 40
            elif total_pnl < -5.0 and live_trades >= 10:
                live_score -= 20
            if pf < 0.3 or avg_pnl < -3.0:
                live_score -= 40
            elif pf < 0.5 or wr < 0.25:
                live_score -= 25
            elif pf < 0.8 or wr < 0.35:
                live_score -= 10
            if pf > 2.0 or wr > 0.6:
                live_score += 15
            elif pf > 1.2 and wr > 0.45:
                live_score += 5

        # 融合
        combined = int(fit_score + bt_score * (1 - w) + live_score * w)
        combined = max(0, min(100, combined))

        if live_trades == 0 and bt_score != 0:
            logger.info(
                f"[策略轮动] {self.symbol} {strategy} @ {trend}/{volatility}: "
                f"fit={fit_score} + bt={bt_score:+d} = {combined} (回测权重 100%)"
            )
        elif live_trades > 0:
            logger.info(
                f"[策略轮动] {self.symbol} {strategy} @ {trend}/{volatility}: "
                f"fit={fit_score} + bt={bt_score:+d}*{1-w:.0%} + live={live_score:+d}*{w:.0%} = {combined} "
                f"(trades={live_trades} wr={perf.get('win_rate',0):.0%} pf={perf.get('profit_factor',0)})"
            )

        return combined

    # ---------- 轮动核心 ----------

    def pick(self, regime: dict) -> dict:
        trend = (regime.get("trend") or "unknown").lower()
        vol = (regime.get("volatility") or "unknown").lower()
        confidence = regime.get("confidence", 0.5)
        key = (trend, vol)
        strategy_name, reason, kwargs = REGIME_STRATEGY_MAP.get(key, FALLBACK_STRATEGY)

        # 用融合评分评估推荐策略
        combined_score = self._compute_combined_score(strategy_name, 100, trend, vol)

        # 如果推荐策略得分过低，找更好的替代
        if combined_score < 40:
            best_alt = self._find_best_by_combined(trend, vol)
            if best_alt and best_alt[0] != strategy_name:
                alt_name, alt_score = best_alt
                msg = (f"推荐{strategy_name}(融合得分{combined_score}) -> 替代{alt_name}(得分{alt_score})")
                logger.warning(f"[策略轮动] {self.symbol} {trend}+{vol}: {msg}")
                strategy_name = alt_name
                reason = f"{reason} (回测替代: {alt_name})"
                _strategy_names = {v[0] for v in REGIME_STRATEGY_MAP.values()}
                kwargs = REGIME_STRATEGY_MAP.get(
                    next((k for k in REGIME_STRATEGY_MAP
                          if REGIME_STRATEGY_MAP[k][0] == alt_name),
                         ("", "", {})),
                    ({},)
                )[2] if alt_name in _strategy_names else {}

        # 稳定期检查
        if key == self._last_regime_key:
            self._stability_counter += 1
        else:
            self._stability_counter = 0
        self._last_regime_key = key
        self._last_strategy = strategy_name

        return {
            "strategy": strategy_name,
            "reason": reason,
            "kwargs": kwargs,
            "regime": {"trend": trend, "volatility": vol},
            "combined_score": combined_score,
        }

    def _find_best_by_combined(self, trend: str, volatility: str) -> tuple:
        """在所有候选策略中找融合得分最高的"""
        candidates = []
        for (s_trend, s_vol), (name, _, _) in REGIME_STRATEGY_MAP.items():
            if s_trend == trend and s_vol == volatility:
                fit = _STRATEGY_FIT_SCORE.get((name, trend, volatility), 60)
                combined = self._compute_combined_score(name, fit, trend, volatility)
                candidates.append((name, combined))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0] if candidates else None

    def get_better_strategies(self, current_strategy: str, regime: dict, top_n: int = 3) -> list:
        trend = (regime.get("trend") or "unknown").lower()
        vol = (regime.get("volatility") or "unknown").lower()
        current_fit = _STRATEGY_FIT_SCORE.get(
            (current_strategy.upper(), trend, vol), 60
        )
        current_combined = self._compute_combined_score(current_strategy, current_fit, trend, vol)
        result = []
        for (s_trend, s_vol), (name, reason, _) in REGIME_STRATEGY_MAP.items():
            if name.upper() == current_strategy.upper():
                continue
            if s_trend != trend and s_trend != "unknown":
                continue
            fit = _STRATEGY_FIT_SCORE.get((name, trend, vol), 60)
            combined = self._compute_combined_score(name, fit, trend, vol)
            if combined > current_combined:
                result.append({
                    "strategy": name, "reason": reason,
                    "fit_score": fit, "combined_score": combined,
                })
        result.sort(key=lambda x: x["combined_score"], reverse=True)
        return result[:top_n]

    def score_fit(self, strategy_name: str, trend: str, volatility: str) -> int:
        key = (strategy_name.upper(), trend.lower(), volatility.lower())
        if key in _STRATEGY_FIT_SCORE:
            return _STRATEGY_FIT_SCORE[key]
        # 跨regime fallback
        for (s, t, v), score in _STRATEGY_FIT_SCORE.items():
            if s == strategy_name.upper() and t == trend.lower():
                return int(score * 0.5)
        return 30
