#!/usr/bin/env python3
"""
SQLite → PostgreSQL 数据迁移脚本

用法：
  # 1. 先确保 PostgreSQL 运行（可复用 QuantDinger docker-compose）
  #    docker-compose -f /root/QuantDinger/docker-compose.yml up -d postgres
  #
  # 2. 设置环境变量（如非默认）
  #    export DATABASE_URL=postgresql://quantdinger:quantdinger123@localhost:5432/quantdinger
  #
  # 3. 执行迁移
  #    python3 migrate_sqlite_to_pg.py

特性：
  - 幂等安全（可重复执行）
  - 先创建表结构再迁移数据
  - 输出迁移统计信息
"""

import os
import sys
import sqlite3
import logging
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 加载项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database_pg import init_db, get_cursor


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    table: str,
    columns: list,
    pg_table: str = None,
    transform_row=None,
):
    """
    迁移单个表

    Args:
        sqlite_conn: SQLite 连接
        table: SQLite 表名
        columns: 列名列表
        pg_table: PostgreSQL 表名（默认与 SQLite 相同）
        transform_row: 可选的行转换函数 row -> row
    """
    pg_table = pg_table or table
    pg_columns = [c.split()[0] for c in columns]  # 去掉类型注释

    try:
        cursor = sqlite_conn.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
    except sqlite3.OperationalError as e:
        logger.warning(f"  skip {table}: {e}")
        return 0

    if not rows:
        logger.info(f"  {table}: 0 rows")
        return 0

    # 构建 INSERT（ON CONFLICT DO NOTHING）
    placeholders = ", ".join(["%s"] * len(pg_columns))
    cols_str = ", ".join(pg_columns)

    count = 0
    with get_cursor() as cur:
        for row in rows:
            row_dict = dict(row)
            values = []
            for i, col in enumerate(pg_columns):
                if col == "id" and i < len(row_dict):
                    values.append(row_dict[col] if row_dict[col] is not None else None)
                else:
                    # 尝试匹配 SQLite 列名
                    for key in row_dict:
                        if key.lower() == col.lower():
                            values.append(row_dict[key])
                            break
                    else:
                        values.append(None)

            # 跳过空行
            if all(v is None for v in values[1:]):
                continue

            try:
                cur.execute(
                    f"INSERT INTO {pg_table} ({cols_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                    values,
                )
                count += 1
            except Exception as e:
                logger.debug(f"  skip row in {table}: {e}")

    logger.info(f"  {table}: {count}/{len(rows)} rows migrated")
    return count


def main():
    sqlite_paths = [
        os.path.join(os.path.dirname(__file__), "live_trading.db"),
        os.path.join(os.path.dirname(__file__), "trading_system.db"),
        os.path.join(os.path.dirname(__file__), "trade_history.db"),
    ]

    # Step 1: Initialize PostgreSQL schema
    logger.info("=" * 50)
    logger.info("Step 1: Initializing PostgreSQL schema")
    try:
        init_db()
        logger.info("  Schema ready")
    except Exception as e:
        logger.error(f"  Failed to connect to PostgreSQL: {e}")
        logger.error("  Make sure PostgreSQL is running:")
        logger.error("    docker-compose -f /root/QuantDinger/docker-compose.yml up -d postgres")
        sys.exit(1)

    # Step 2: Migrate from SQLite
    total_rows = 0
    for sqlite_path in sqlite_paths:
        if not os.path.exists(sqlite_path):
            logger.warning(f"  SQLite not found: {sqlite_path}")
            continue

        logger.info("")
        logger.info(f"Migrating from {os.path.basename(sqlite_path)}")

        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row  # 使 row 支持按键访问

        try:
            # live_trading.db tables
            total_rows += migrate_table(
                conn, "positions",
                ["id", "symbol", "timeframe", "entry_price", "entry_time",
                 "stop_loss", "take_profit", "quantity", "status", "exchange",
                 "side", "created_at", "updated_at"]
            )
            total_rows += migrate_table(
                conn, "trades",
                ["id", "symbol", "timeframe", "entry_price", "entry_time",
                 "exit_price", "exit_time", "quantity", "pnl_pct", "pnl_abs",
                 "exit_reason", "ai_verdict", "created_at"]
            )
            total_rows += migrate_table(
                conn, "equity_log",
                ["id", "agent_id", "timestamp", "price", "equity",
                 "position_value", "in_position", "rsi", "created_at"]
            )
            total_rows += migrate_table(
                conn, "signal_log",
                ["id", "agent_id", "signal_type", "price", "rsi",
                 "ai_verdict", "message", "created_at"]
            )
            total_rows += migrate_table(
                conn, "param_history",
                ["id", "symbol", "param_name", "old_value", "new_value",
                 "reason", "trade_count", "win_rate", "created_at"]
            )
            total_rows += migrate_table(
                conn, "shangshu_executions",
                ["id", "order_id", "agent_id", "symbol", "side", "quantity",
                 "exec_price", "exec_type", "commission", "success", "message",
                 "exchange", "is_testnet", "created_at"]
            )
            total_rows += migrate_table(
                conn, "xingbu_trades",
                ["id", "order_id", "agent_id", "symbol", "side", "quantity",
                 "exec_price", "exec_type", "pnl_pct", "created_at"]
            )
            total_rows += migrate_table(
                conn, "xingbu_violations",
                ["id", "order_id", "agent_id", "symbol", "side", "quantity",
                 "entry_price", "reject_reason", "risk_level",
                 "rules_triggered", "created_at"]
            )

            # trading_system.db tables
            total_rows += migrate_table(
                conn, "alerts",
                ["id", "symbol", "market", "alert_type", "price", "threshold",
                 "message", "created_at"]
            )
        finally:
            conn.close()

    logger.info("")
    logger.info("=" * 50)
    logger.info(f"Migration complete: {total_rows} total rows migrated")
    logger.info("Original SQLite databases preserved (no data deleted)")
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Set DATABASE_URL in .env:")
    logger.info("     DATABASE_URL=postgresql://quantdinger:quantdinger123@localhost:5432/quantdinger")
    logger.info("  2. Update config.py to use database_pg instead of database.py")
    logger.info("  3. Run: alembic upgrade head  (to apply future migrations)")


if __name__ == "__main__":
    main()
