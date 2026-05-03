"""
数据库模块 - SQLite 持仓与交易记录
"""
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import contextmanager
from config import DB_PATH

def init_db():
    """初始化数据库"""
    with get_conn() as conn:
        c = conn.cursor()
        # 持仓表
        c.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 交易历史表
        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                total REAL NOT NULL,
                traded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 告警记录表
        c.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                price REAL NOT NULL,
                threshold REAL NOT NULL,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# 持仓管理
def add_position(symbol: str, market: str, quantity: float, avg_price: float):
    with get_conn() as conn:
        c = conn.cursor()
        # 检查是否已存在
        c.execute("SELECT id, quantity, avg_price FROM positions WHERE symbol=? AND market=?",
                  (symbol, market))
        row = c.fetchone()
        if row:
            # 更新均价
            total_qty = row['quantity'] + quantity
            total_cost = row['quantity'] * row['avg_price'] + quantity * avg_price
            new_avg = total_cost / total_qty
            c.execute("UPDATE positions SET quantity=?, avg_price=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                      (total_qty, new_avg, row['id']))
        else:
            c.execute("INSERT INTO positions (symbol, market, quantity, avg_price) VALUES (?, ?, ?, ?)",
                      (symbol, market, quantity, avg_price))
        conn.commit()

def remove_position(symbol: str, market: str, quantity: float):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id, quantity FROM positions WHERE symbol=? AND market=?",
                  (symbol, market))
        row = c.fetchone()
        if row and row['quantity'] >= quantity:
            new_qty = row['quantity'] - quantity
            if new_qty > 0:
                c.execute("UPDATE positions SET quantity=? WHERE id=?", (new_qty, row['id']))
            else:
                c.execute("DELETE FROM positions WHERE id=?", (row['id'],))
            conn.commit()
            return True
        return False

def get_positions() -> List[Dict]:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM positions ORDER BY created_at DESC")
        return [dict(r) for r in c.fetchall()]

# 交易记录
def record_trade(symbol: str, market: str, trade_type: str, quantity: float, price: float):
    total = quantity * price
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""INSERT INTO trades (symbol, market, trade_type, quantity, price, total)
                      VALUES (?, ?, ?, ?, ?, ?)""",
                  (symbol, market, trade_type, quantity, price, total))
        conn.commit()

def get_trades(limit: int = 50) -> List[Dict]:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM trades ORDER BY traded_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in c.fetchall()]

# 告警记录
def record_alert(symbol: str, market: str, alert_type: str, price: float, threshold: float, message: str = ""):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""INSERT INTO alerts (symbol, market, alert_type, price, threshold, message)
                      VALUES (?, ?, ?, ?, ?, ?)""",
                  (symbol, market, alert_type, price, threshold, message))
        conn.commit()

def get_alerts(limit: int = 20) -> List[Dict]:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in c.fetchall()]

if __name__ == "__main__":
    init_db()
    print("数据库初始化完成")
