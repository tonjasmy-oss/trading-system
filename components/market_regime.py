"""
market_regime.py — 市场状态检测与标注（Agent-S 环境上下文借鉴）
==================================================================

定位：
  - 标注每笔交易发生时的市场状态（趋势/波动率/成交量）
  - 供 TradeHistory 和 Auditor 使用，实现"什么市场环境适合什么策略"

检测方法：
  Trend（趋势）:
    - SMA_50 > SMA_200 → uptrend
    - SMA_50 < SMA_200 → downtrend
    - 50_SMA 距 200_SMA 在 ±2% 以内 → ranging
  Volatility（波动率）:
    - Bollinger Bandwidth / price > 阈值 → high / medium / low
  Volume（成交量）:
    - 当前成交量 > 20MA_volume 的 1.2 倍 → high
    - > 0.8 倍 → medium
    - 否则 → low

使用方式：
  mr = MarketRegime()
  state = mr.get_current_regime("BTC/USDT", timeframe="4h")
  # state = {"trend": "uptrend", "volatility": "medium", "volume": "high", "confidence": 0.78}
"""

import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List
from pathlib import Path
import asyncio

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 市场状态检测
# ─────────────────────────────────────────────────────────────

def detect_trend(sma_fast: float, sma_slow: float, threshold: float = 0.02) -> str:
    """根据 SMA 交叉判断趋势"""
    if sma_slow == 0:
        return "unknown"
    ratio = (sma_fast - sma_slow) / sma_slow
    if ratio > threshold:
        return "uptrend"
    elif ratio < -threshold:
        return "downtrend"
    else:
        return "ranging"


def detect_volatility(bb_width: float, price: float) -> str:
    """根据布林带宽度判断波动率（bb_width = upper - lower）"""
    if price == 0:
        return "unknown"
    bandwidth_ratio = bb_width / price
    if bandwidth_ratio > 0.06:
        return "high"
    elif bandwidth_ratio > 0.03:
        return "medium"
    else:
        return "low"


def detect_volume(volume: float, volume_ma: float) -> str:
    """根据成交量与均线的比值判断"""
    if volume_ma == 0:
        return "unknown"
    ratio = volume / volume_ma
    if ratio > 1.2:
        return "high"
    elif ratio > 0.8:
        return "medium"
    else:
        return "low"


# ─────────────────────────────────────────────────────────────
# 策略推荐引擎 — 基于市场状态推荐最优策略族
# ─────────────────────────────────────────────────────────────

# (trend, volatility) → [(strategy_name, fit_score, reason), ...]
# fit_score: 0-100，表示策略与当前市场的适配度
STRATEGY_RECOMMENDATIONS = {
    ("uptrend", "high"): [
        ("DONCHIAN",   95, "强趋势+高波动，通道突破捕捉主升浪"),
        ("ATRSTOP",    90, "ATR动态止损，跟随趋势防止假突破"),
        ("MULTIFACTOR", 80, "多因子确认，过滤高波动噪音"),
        ("SMA",        60, "均线趋势跟随，但高波动下假信号增多"),
    ],
    ("uptrend", "medium"): [
        ("DONCHIAN",   92, "中等趋势+波动，通道突破策略最优"),
        ("SMA",        85, "均线趋势，中等波动下信号较可靠"),
        ("ATRSTOP",    82, "趋势跟随+动态止损"),
        ("MULTIFACTOR", 75, "多因子综合评分"),
    ],
    ("uptrend", "low"): [
        ("SMA",        90, "低波动趋势市，均线策略简洁高效"),
        ("DONCHIAN",   70, "低波动下突破信号少，但信号质量高"),
        ("ATRSTOP",    65, "低波动ATR止损空间小"),
    ],
    ("downtrend", "high"): [
        ("RSI",        88, "高波动下跌市，超卖反弹机会多"),
        ("DONCHIAN",   75, "通道向下突破做空"),
        ("BOLLINGER",  70, "布林带下轨反弹"),
        ("FUNDING_ARB", 60, "高波动时资金费率套利有空间"),
    ],
    ("downtrend", "medium"): [
        ("RSI",        85, "下跌趋势中捕捉超卖反弹"),
        ("DONCHIAN",   72, "通道向下突破做空"),
        ("KDJ",        68, "摆动指标捕捉底部"),
    ],
    ("downtrend", "low"): [
        ("RSI",        80, "低波动下跌中抓反弹"),
        ("KDJ",        72, "低波动下KDJ信号更精确"),
        ("FUNDING_ARB", 65, "资金费率套利"),
    ],
    ("ranging", "high"): [
        ("BOLLINGER",  92, "震荡市+高波动，布林带均值回归最优"),
        ("ATRSTOP",    78, "ATR动态止损防止假突破"),
        ("STAT_ARB",   65, "统计套利在震荡市中有效"),
    ],
    ("ranging", "medium"): [
        ("BOLLINGER",  90, "震荡市均值回归，布林带首选"),
        ("KDJ",        82, "摆动交易，捕捉区间高低点"),
        ("RSI",        75, "超买超卖区间交易"),
        ("FUNDING_ARB", 65, "资金费率套利"),
    ],
    ("ranging", "low"): [
        ("KDJ",        88, "低波动震荡，摆动指标最优"),
        ("BOLLINGER",  80, "窄幅布林带突破"),
        ("FUNDING_ARB", 72, "低波动时套利稳定"),
    ],
}

# 默认兜底
FALLBACK_RECOMMENDATIONS = [
    ("MACD", 60, "市场状态未知，回退到MACD"),
]


def recommend_strategies(trend: str, volatility: str, top_n: int = 3) -> list:
    """
    根据市场状态推荐策略族。
    
    Args:
        trend: "uptrend" | "downtrend" | "ranging" | "unknown"
        volatility: "high" | "medium" | "low" | "unknown"
        top_n: 返回前 N 个推荐
    
    Returns:
        [{"strategy": "BOLLINGER", "fit_score": 92, "reason": "..."}, ...]
    """
    key = (trend.lower(), volatility.lower())
    recs = STRATEGY_RECOMMENDATIONS.get(key, FALLBACK_RECOMMENDATIONS)
    return [
        {"strategy": s, "fit_score": score, "reason": reason}
        for s, score, reason in recs[:top_n]
    ]


# ─────────────────────────────────────────────────────────────
# 主类
# ─────────────────────────────────────────────────────────────

class MarketRegime:
    """
    市场状态检测与存储

    检测规则：
      - Trend: SMA(50) vs SMA(200)，4h 周期使用 50/200 均线
      - Volatility: Bollinger Bandwidth，20 周期
      - Volume: 当前成交量 vs 20 周期均线
    """

    DB_NAME = "market_regime.db"

    def __init__(self, db_dir: str = "."):
        self.db_path = Path(db_dir) / self.DB_NAME
        self._init_db()
        self._cache: Dict[str, Dict] = {}  # symbol → state (TTL 5min)
        self._cache_ttl = 300  # 5分钟

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_regime_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT    NOT NULL,
                timeframe     TEXT    NOT NULL DEFAULT '4h',
                trend         TEXT    NOT NULL,
                volatility    TEXT    NOT NULL,
                volume        TEXT    NOT NULL,
                sma_fast      REAL,
                sma_slow      REAL,
                bb_width      REAL,
                volume_ratio  REAL,
                confidence    REAL,
                price         REAL,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(symbol, timeframe, created_at)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mr_symbol_time
            ON market_regime_log(symbol, timeframe, created_at DESC)
        """)
        conn.commit()
        conn.close()

    def _compute_regime(
        self,
        closes: List[float],
        volumes: List[float],
        bb_period: int = 20,
        sma_fast: int = 50,
        sma_slow: int = 200,
    ) -> Dict:
        """计算当前市场状态"""
        import statistics

        n = len(closes)
        # 数据不足时自动降级到更短周期
        actual_sma_fast = sma_fast
        actual_sma_slow = sma_slow
        if n < sma_slow:
            if n < 20:
                return {
                    "trend": "unknown",
                    "volatility": "unknown",
                    "volume": "unknown",
                    "confidence": 0.0,
                    "sma_fast": None,
                    "sma_slow": None,
                    "bb_width": None,
                    "volume_ratio": None,
                }
            # 降级：使用 n/3 和 n/5 作为快慢线
            actual_sma_slow = max(10, n // 3)
            actual_sma_fast = max(5, n // 5)

        price = closes[-1]

        # SMA（使用实际可用周期）
        sma_fast_val = sum(closes[-actual_sma_fast:]) / actual_sma_fast
        sma_slow_val = sum(closes[-actual_sma_slow:]) / actual_sma_slow
        trend = detect_trend(sma_fast_val, sma_slow_val)

        # Bollinger Bands Width
        recent = closes[-bb_period:]
        ma = sum(recent) / len(recent)
        std = statistics.stdev(recent) if len(recent) > 1 else 0
        bb_upper = ma + 2 * std
        bb_lower = ma - 2 * std
        bb_width = bb_upper - bb_lower
        volatility = detect_volatility(bb_width, price)

        # Volume
        vol_ma = sum(volumes[-20:]) / min(20, len(volumes))
        volume_state = detect_volume(volumes[-1] if volumes else 0, vol_ma)
        volume_ratio = volumes[-1] / vol_ma if vol_ma > 0 else 1.0

        # Confidence：基于数据质量和一致性
        confidence = 0.5
        if n >= sma_slow * 2:
            confidence += 0.2
        if volume_ratio > 0.5 and volume_ratio < 2.0:  # 正常成交量
            confidence += 0.15
        if trend != "unknown":
            confidence += 0.15
        confidence = min(confidence, 1.0)

        return {
            "trend": trend,
            "volatility": volatility,
            "volume": volume_state,
            "confidence": round(confidence, 2),
            "sma_fast": round(sma_fast_val, 4),
            "sma_slow": round(sma_slow_val, 4),
            "bb_width": round(bb_width, 4),
            "volume_ratio": round(volume_ratio, 3),
        }

    def get_current_regime(
        self,
        symbol: str,
        timeframe: str = "4h",
        closes: Optional[List[float]] = None,
        volumes: Optional[List[float]] = None,
        save: bool = True,
    ) -> Dict:
        """
        获取当前市场状态。
        如果传入 closes/volumes 则计算，否则从 crypto_api 抓取 K线。
        """
        import time
        cache_key = f"{symbol}:{timeframe}"
        now = time.time()

        # 缓存检查（5min TTL）
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if now - cached["_cached_at"] < self._cache_ttl:
                return cached

        # 没有传入 K线？从 Binance 抓取
        if closes is None:
            try:
                from crypto_api import get_ohlcv
                # symbol = "BTC/USDT" → "BTC"
                sym = symbol.split("/")[0]
                candles = get_ohlcv(sym, timeframe=timeframe, limit=250)
                if candles:
                    closes = [c["close"] for c in candles]
                    volumes = [c.get("volume", 0) for c in candles]
            except Exception as e:
                logger.warning(f"[MarketRegime] 获取K线失败 {symbol}: {e}")
                return {"trend": "unknown", "volatility": "unknown",
                        "volume": "unknown", "confidence": 0.0}

        if not closes or len(closes) < 10:
            return {"trend": "unknown", "volatility": "unknown",
                    "volume": "unknown", "confidence": 0.0}

        state = self._compute_regime(closes, volumes or [1.0] * len(closes))
        state["symbol"] = symbol
        state["timeframe"] = timeframe
        state["price"] = closes[-1]
        state["_cached_at"] = now

        # 缓存
        self._cache[cache_key] = state

        # 持久化
        if save:
            self._save_state(symbol, timeframe, state)

        return state

    def _save_state(self, symbol: str, timeframe: str, state: Dict):
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("""
                INSERT OR IGNORE INTO market_regime_log
                (symbol, timeframe, trend, volatility, volume,
                 sma_fast, sma_slow, bb_width, volume_ratio, confidence, price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, timeframe,
                state["trend"], state["volatility"], state["volume"],
                state.get("sma_fast"), state.get("sma_slow"),
                state.get("bb_width"), state.get("volume_ratio"),
                state["confidence"], state.get("price"),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[MarketRegime] 保存状态失败: {e}")

    def get_historical_regime(
        self,
        symbol: str,
        timeframe: str = "4h",
        hours_back: int = 168,  # 默认 7 天
    ) -> List[Dict]:
        """查询历史市场状态"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT trend, volatility, volume, confidence, price, created_at
            FROM market_regime_log
            WHERE symbol = ? AND timeframe = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (symbol, timeframe, hours_back // 4)).fetchall()  # 4h 周期
        conn.close()
        return [dict(r) for r in rows]

    def get_regime_at(self, dt_str: str) -> Optional[Dict]:
        """
        根据时间字符串（如 '2026-05-01 10:00:00'）查询当时市场状态。
        内部将字符串转换为 Unix 时间戳后调用 get_regime_at_time。
        """
        try:
            from datetime import datetime
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            ts = int(dt.timestamp())
            return self.get_regime_at_time(
                symbol="BTC/USDT",
                timestamp=ts,
                timeframe=self.timeframe,
            )
        except Exception:
            return None

    def get_regime_at_time(
        self,
        symbol: str,
        timestamp: int,  # Unix 秒
        timeframe: str = "4h",
    ) -> Dict:
        """查询指定时间点附近的市场状态（最近一条记录）"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT trend, volatility, volume, confidence, price, created_at
            FROM market_regime_log
            WHERE symbol = ? AND timeframe = ?
              AND (strftime('%s', created_at)) <= ?
            ORDER BY created_at DESC LIMIT 1
        """, (symbol, timeframe, timestamp)).fetchone()
        conn.close()
        return dict(row) if row else {
            "trend": "unknown", "volatility": "unknown", "volume": "unknown"
        }


# ─────────────────────────────────────────────────────────────
# 批量标注工具（为 trade_history 中缺少 regime 的历史记录补标注）
# ─────────────────────────────────────────────────────────────

def backfill_regime(db_dir: str = ".", symbol: Optional[str] = None):
    """
    为 trade_history 中 market_trend='unknown' 的记录补充市场状态标注。
    读取对应时间点的 market_regime_log 来填充。
    """
    import trade_history as th_module

    th = th_module.get_history(db_dir)
    mr = MarketRegime(db_dir)

    # 找出 unknown 的记录
    open_trades = th.get_open_trades(symbol=symbol)
    unknown_trades = [t for t in open_trades if t.get("market_trend") == "unknown"]

    # 也检查已关闭的交易（需要查 trade_history 的 exit_time）
    conn = sqlite3.connect(Path(db_dir) / "trade_history.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, symbol, entry_time, market_trend, market_volatility
        FROM trade_history
        WHERE (market_trend = 'unknown' OR market_trend IS NULL)
          AND exit_time IS NOT NULL
    """).fetchall()
    conn.close()

    trades_to_fill = list(rows)
    logger.info(f"[MarketRegime] 发现 {len(trades_to_fill)} 条待标注记录")

    updated = 0
    for row in trades_to_fill:
        entry_time = row["entry_time"]
        sym = row["symbol"]
        regime = mr.get_regime_at_time(sym, entry_time)
        if regime.get("trend") != "unknown":
            # 这里暂时只打日志，实际 UPDATE 逻辑较复杂
            # 因为 trade_history 是 TradeHistory 类管理，这里直接 SQL 操作
            conn2 = sqlite3.connect(Path(db_dir) / "trade_history.db")
            conn2.execute("""
                UPDATE trade_history
                SET market_trend = ?, market_volatility = ?
                WHERE id = ?
            """, (regime["trend"], regime.get("volatility", "unknown"), row["id"]))
            conn2.commit()
            conn2.close()
            updated += 1
            logger.info(f"[MarketRegime] 补标注 trade_id={row['id']}: {regime['trend']}")

    logger.info(f"[MarketRegime] 批量标注完成: {updated} 条")
    return updated


def score_strategy_fit(strategy_name: str, trend: str, volatility: str) -> int:
    """
    评估指定策略对当前市场状态的适配度（0-100）。
    未在推荐列表中的策略默认得分为 30（不推荐）。
    """
    key = (trend.lower(), volatility.lower())
    recs = STRATEGY_RECOMMENDATIONS.get(key, FALLBACK_RECOMMENDATIONS)
    for s, score, _ in recs:
        if s.upper() == strategy_name.upper():
            return score
    return 30  # 不在推荐列表中