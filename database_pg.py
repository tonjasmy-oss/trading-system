"""
PostgreSQL 数据库模块 - 连接池 + 事务管理
参考 QuantDinger 的 app/utils/db_postgres.py

特性：
  - 基于 psycopg2 的连接池（ThreadedConnectionPool）
  - 上下文管理器自动提交/回滚
  - 线程安全的 get_db() 接口
  - 兼容原 SQLite database.py 的接口语义
"""

import os
import logging
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool, sql

logger = logging.getLogger(__name__)

# ── 配置 ──
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://quantdinger:quantdinger123@localhost:5432/quantdinger"
)

# 连接池设置
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# 全局连接池（延迟初始化）
_connection_pool: Optional[pool.ThreadedConnectionPool] = None


def _parse_dsn(url: str) -> dict:
    """解析 DATABASE_URL 为 psycopg2 连接参数"""
    # postgresql://user:pass@host:port/dbname
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    params = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "dbname": (parsed.path or "/quantdinger").lstrip("/"),
        "user": parsed.username or "quantdinger",
        "password": parsed.password or "",
    }
    # 额外参数
    qs = parse_qs(parsed.query)
    if "sslmode" in qs:
        params["sslmode"] = qs["sslmode"][0]
    return params


def _get_pool() -> pool.ThreadedConnectionPool:
    """获取或创建连接池"""
    global _connection_pool
    if _connection_pool is None:
        dsn = _parse_dsn(DATABASE_URL)
        try:
            _connection_pool = pool.ThreadedConnectionPool(
                DB_POOL_MIN, DB_POOL_MAX, **dsn
            )
            logger.info(
                "[DB-PG] Connected to %s:%s/%s (pool: %d-%d)",
                dsn["host"], dsn["port"], dsn["dbname"],
                DB_POOL_MIN, DB_POOL_MAX,
            )
        except Exception as e:
            logger.error("[DB-PG] Connection failed: %s", e)
            raise
    return _connection_pool


@contextmanager
def get_db():
    """
    获取数据库连接（上下文管理器）

    使用方式：
      with get_db() as conn:
          cur = conn.cursor()
          cur.execute("SELECT ...")
    """
    p = _get_pool()
    conn = None
    try:
        conn = p.getconn()
        yield conn
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            p.putconn(conn)


@contextmanager
def get_cursor(commit: bool = True):
    """
    获取游标（自动提交/回滚）

    使用方式：
      with get_cursor() as cur:
          cur.execute("INSERT INTO ...")
    """
    with get_db() as conn:
        cur = conn.cursor()
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise


# ── 初始化/迁移 ──

def init_db():
    """初始化数据库表（幂等）"""
    with get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '1h',
                entry_price REAL NOT NULL,
                entry_time BIGINT,
                stop_loss REAL,
                take_profit REAL,
                quantity REAL NOT NULL,
                status TEXT DEFAULT 'open',
                exchange TEXT DEFAULT '',
                side TEXT DEFAULT 'long',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '1h',
                entry_price REAL NOT NULL,
                entry_time BIGINT,
                exit_price REAL,
                exit_time BIGINT,
                quantity REAL NOT NULL,
                pnl_pct REAL DEFAULT 0,
                pnl_abs REAL DEFAULT 0,
                exit_reason TEXT,
                ai_verdict TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS equity_log (
                id SERIAL PRIMARY KEY,
                agent_id TEXT DEFAULT 'agent_1',
                timestamp BIGINT NOT NULL,
                price REAL,
                equity REAL NOT NULL,
                position_value REAL DEFAULT 0,
                in_position INTEGER DEFAULT 0,
                rsi REAL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS signal_log (
                id SERIAL PRIMARY KEY,
                agent_id TEXT DEFAULT 'agent_1',
                signal_type TEXT NOT NULL,
                price REAL,
                rsi REAL,
                ai_verdict TEXT,
                message TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS param_history (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                param_name TEXT NOT NULL,
                old_value REAL,
                new_value REAL NOT NULL,
                reason TEXT,
                trade_count INTEGER,
                win_rate REAL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                price REAL NOT NULL,
                threshold REAL NOT NULL,
                message TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shangshu_executions (
                id SERIAL PRIMARY KEY,
                order_id TEXT,
                agent_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT,
                quantity REAL,
                exec_price REAL,
                exec_type TEXT,
                commission REAL DEFAULT 0,
                success INTEGER DEFAULT 0,
                message TEXT,
                exchange TEXT,
                is_testnet INTEGER DEFAULT 0,
                created_at BIGINT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS xingbu_trades (
                id SERIAL PRIMARY KEY,
                order_id TEXT,
                agent_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT,
                quantity REAL,
                exec_price REAL,
                exec_type TEXT,
                pnl_pct REAL DEFAULT 0,
                created_at BIGINT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS xingbu_violations (
                id SERIAL PRIMARY KEY,
                order_id TEXT,
                agent_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT,
                quantity REAL,
                entry_price REAL,
                reject_reason TEXT,
                risk_level TEXT,
                rules_triggered TEXT,
                created_at BIGINT
            )
        """)
        # 索引
        cur.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_equity_agent ON equity_log(agent_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_log(timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_signal_agent ON signal_log(agent_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_param_symbol ON param_history(symbol)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at)")

        logger.info("[DB-PG] Schema initialized")


# ── 高级查询接口（兼容原 database.py） ──

def get_positions(status: str = "open") -> list:
    """获取当前持仓"""
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT * FROM positions WHERE status = %s ORDER BY created_at DESC",
            (status,)
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_trades(limit: int = 50) -> list:
    """获取最近交易记录"""
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT %s",
            (limit,)
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_alerts(limit: int = 20) -> list:
    """获取最近告警"""
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT %s",
            (limit,)
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


# ── 健康检查 ──

def check_health() -> bool:
    """检查数据库连接是否正常"""
    try:
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT 1")
            return True
    except Exception:
        return False
