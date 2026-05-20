"""
数据库工厂模块 — 统一 SQLite / PostgreSQL 访问层

根据 DB_TYPE 环境变量选择后端：
  DB_TYPE=sqlite      → 使用 database.py (SQLite, 默认)
  DB_TYPE=postgresql  → 使用 database_pg.py (PostgreSQL)

用法：
  from db_factory import get_db_conn
  with get_db_conn() as conn:
      rows = conn.execute("SELECT * FROM positions").fetchall()
"""

import os
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_TYPE = os.getenv("DB_TYPE", "sqlite").lower()


@contextmanager
def get_db_conn():
    """
    获取数据库连接（上下文管理器）
    根据 DB_TYPE 自动选择 SQLite 或 PostgreSQL
    """
    if DB_TYPE == "postgresql":
        from database_pg import get_db as _pg_get_db
        with _pg_get_db() as conn:
            yield conn
    else:
        import sqlite3
        db_path = os.getenv("DB_PATH", "trading_system.db")
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.path.dirname(__file__), db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def get_db_type() -> str:
    return DB_TYPE


def get_db_info() -> dict:
    if DB_TYPE == "postgresql":
        from database_pg import DATABASE_URL, DB_POOL_MIN, DB_POOL_MAX
        return {
            "type": "postgresql",
            "url": DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL,
            "pool": {"min": DB_POOL_MIN, "max": DB_POOL_MAX},
        }
    return {"type": "sqlite", "path": os.getenv("DB_PATH", "trading_system.db")}


def query_one(sql: str, params: tuple = None):
    with get_db_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        row = cur.fetchone()
        if row is None:
            return None
        if hasattr(row, "keys"):
            return dict(row)
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def query_all(sql: str, params: tuple = None):
    with get_db_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]


def execute(sql: str, params: tuple = None):
    with get_db_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
        return cur.rowcount


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    info = get_db_info()
    print(f"数据库类型: {info['type']}")
    if info["type"] == "postgresql":
        print(f"PG 地址: {info['url']}")
    else:
        print(f"SQLite 路径: {info['path']}")
    row = query_one("SELECT COUNT(*) as cnt FROM equity_log")
    if row:
        print(f"equity_log 行数: {row.get('cnt', 'N/A')}")
