"""
OHLCV 历史数据本地缓存模块
使用 SQLite 本地存储 K 线数据，避免重复拉取
表结构: symbol, timeframe, timestamp, open, high, low, close, volume
"""
import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager
import os

logger = logging.getLogger(__name__)

# 默认数据库路径（与 config.py 的 DB_PATH 保持一致）
DB_PATH = os.getenv("DB_PATH", "trading_system.db")

# 缓存目录：与 trading_system.db 同目录的 ohlcv_cache 目录
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ohlcv_cache")
_DB_FILE = os.path.join(_CACHE_DIR, "ohlcv_cache.db")


def _get_db_path() -> str:
    """获取缓存数据库路径，必要时创建目录"""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return _DB_FILE


@contextmanager
def _get_conn():
    """获取数据库连接的上下文管理器"""
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_cache_db():
    """
    初始化 OHLCV 缓存数据库
    建表语句：symbol, timeframe, timestamp, open, high, low, close, volume
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_cache (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                timeframe   TEXT    NOT NULL DEFAULT '1m',
                timestamp   INTEGER NOT NULL,
                open        REAL    NOT NULL,
                high        REAL    NOT NULL,
                low         REAL    NOT NULL,
                close       REAL    NOT NULL,
                volume      REAL    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, timeframe, timestamp)
            )
        """)
        # 为 symbol + timeframe + timestamp 建立唯一索引，加速查询
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ohlcv_key
            ON ohlcv_cache(symbol, timeframe, timestamp)
        """)
        # 为 symbol + timeframe 建立索引，加速范围查询
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf
            ON ohlcv_cache(symbol, timeframe, timestamp DESC)
        """)
        logger.info("OHLCV 缓存数据库初始化完成")


def save_ohlcv(symbol: str, timeframe: str, candles: List[Dict]):
    """
    保存一批 OHLCV 数据到缓存

    Args:
        symbol:     交易对符号，如 'BTC/USDT'
        timeframe:  K线周期，如 '1m', '5m', '1h', '1d'
        candles:    OHLCV 列表，每项包含 open/high/low/close/volume/timestamp
    """
    if not candles:
        return

    rows = []
    for c in candles:
        # ccxt 标准 OHLCV 格式：
        #   list: [timestamp(ms), open, high, low, close, volume]
        #   dict: {timestamp, open, high, low, close, volume}
        if isinstance(c, list):
            ts, o, h, l, cl, v = c[0], c[1], c[2], c[3], c[4], c[5]
        else:
            ts = int(c.get('timestamp', 0))
            o = float(c.get('open', 0))
            h = float(c.get('high', 0))
            l = float(c.get('low', 0))
            cl = float(c.get('close', 0))
            v = float(c.get('volume', 0))
        rows.append((symbol.upper(), timeframe, ts, o, h, l, cl, v))

    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT OR REPLACE INTO ohlcv_cache
                (symbol, timeframe, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        logger.debug(f"已缓存 {len(rows)} 条 {symbol} {timeframe} K线数据")


def get_ohlcv(
    symbol: str,
    timeframe: str,
    since: Optional[int] = None,
    limit: int = 1000
) -> List[Dict]:
    """
    从本地缓存读取 OHLCV 数据

    Args:
        symbol:     交易对符号，如 'BTC/USDT'
        timeframe:  K线周期，如 '1m', '5m', '1h', '1d'
        since:      起始时间戳（毫秒），None 表示不限制
        limit:      最大返回条数

    Returns:
        OHLCV 列表，每项包含 open/high/low/close/volume/timestamp
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        if since:
            cursor.execute("""
                SELECT symbol, timeframe, timestamp, open, high, low, close, volume
                FROM ohlcv_cache
                WHERE symbol=? AND timeframe=? AND timestamp>=?
                ORDER BY timestamp ASC
                LIMIT ?
            """, (symbol.upper(), timeframe, since, limit))
        else:
            cursor.execute("""
                SELECT symbol, timeframe, timestamp, open, high, low, close, volume
                FROM ohlcv_cache
                WHERE symbol=? AND timeframe=?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol.upper(), timeframe, limit))
        rows = cursor.fetchall()
        # 如果是无起始时间的倒序查询，翻转回时间升序
        if not since:
            rows = list(reversed(rows))
        return [
            {
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "timestamp": r["timestamp"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
            }
            for r in rows
        ]


def get_latest_timestamp(symbol: str, timeframe: str) -> Optional[int]:
    """
    获取缓存中某交易对的最新一条 K线时间戳（毫秒）
    用于增量拉取：只需要获取此时间之后的增量数据
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT MAX(timestamp) FROM ohlcv_cache
            WHERE symbol=? AND timeframe=?
        """, (symbol.upper(), timeframe))
        row = cursor.fetchone()
        return row[0] if row and row[0] else None


def clear_cache(symbol: Optional[str] = None, timeframe: Optional[str] = None):
    """
    清理缓存数据

    Args:
        symbol:     只清理指定交易对，None 表示全部
        timeframe:  只清理指定周期，None 表示全部
    """
    with _get_conn() as conn:
        cursor = conn.cursor()
        if symbol and timeframe:
            cursor.execute(
                "DELETE FROM ohlcv_cache WHERE symbol=? AND timeframe=?",
                (symbol.upper(), timeframe)
            )
        elif symbol:
            cursor.execute(
                "DELETE FROM ohlcv_cache WHERE symbol=?", (symbol.upper(),)
            )
        else:
            cursor.execute("DELETE FROM ohlcv_cache")
        logger.info(f"已清理 OHLCV 缓存（symbol={symbol}, timeframe={timeframe}）")


def get_cache_stats() -> Dict:
    """获取缓存统计信息"""
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ohlcv_cache")
        total_rows = cursor.fetchone()[0]
        cursor.execute("""
            SELECT symbol, timeframe, COUNT(*) as cnt,
                   MIN(timestamp) as ts_min, MAX(timestamp) as ts_max
            FROM ohlcv_cache
            GROUP BY symbol, timeframe
            ORDER BY ts_max DESC
            LIMIT 20
        """)
        details = [dict(r) for r in cursor.fetchall()]
    return {"total_rows": total_rows, "pairs": details}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_cache_db()
    print("OHLCV 缓存数据库已初始化")
    print("统计:", get_cache_stats())
