"""Initial PostgreSQL schema

Revision ID: 001
Revises:
Create Date: 2026-05-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("timeframe", sa.Text, default="1h"),
        sa.Column("entry_price", sa.Float, nullable=False),
        sa.Column("entry_time", sa.BigInteger),
        sa.Column("stop_loss", sa.Float),
        sa.Column("take_profit", sa.Float),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("status", sa.Text, default="open"),
        sa.Column("exchange", sa.Text, default=""),
        sa.Column("side", sa.Text, default="long"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_positions_symbol", "positions", ["symbol"])

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("timeframe", sa.Text, default="1h"),
        sa.Column("entry_price", sa.Float, nullable=False),
        sa.Column("entry_time", sa.BigInteger),
        sa.Column("exit_price", sa.Float),
        sa.Column("exit_time", sa.BigInteger),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("pnl_pct", sa.Float, default=0),
        sa.Column("pnl_abs", sa.Float, default=0),
        sa.Column("exit_reason", sa.Text),
        sa.Column("ai_verdict", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_trades_symbol", "trades", ["symbol"])
    op.create_index("idx_trades_created", "trades", ["created_at"])

    op.create_table(
        "equity_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Text, default="agent_1"),
        sa.Column("timestamp", sa.BigInteger, nullable=False),
        sa.Column("price", sa.Float),
        sa.Column("equity", sa.Float, nullable=False),
        sa.Column("position_value", sa.Float, default=0),
        sa.Column("in_position", sa.Integer, default=0),
        sa.Column("rsi", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_equity_agent", "equity_log", ["agent_id"])
    op.create_index("idx_equity_ts", "equity_log", ["timestamp"])

    op.create_table(
        "signal_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Text, default="agent_1"),
        sa.Column("signal_type", sa.Text, nullable=False),
        sa.Column("price", sa.Float),
        sa.Column("rsi", sa.Float),
        sa.Column("ai_verdict", sa.Text),
        sa.Column("message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_signal_agent", "signal_log", ["agent_id"])

    op.create_table(
        "param_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("param_name", sa.Text, nullable=False),
        sa.Column("old_value", sa.Float),
        sa.Column("new_value", sa.Float, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("trade_count", sa.Integer),
        sa.Column("win_rate", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_param_symbol", "param_history", ["symbol"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("market", sa.Text, nullable=False),
        sa.Column("alert_type", sa.Text, nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_alerts_created", "alerts", ["created_at"])

    op.create_table(
        "shangshu_executions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Text),
        sa.Column("agent_id", sa.Text),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text),
        sa.Column("quantity", sa.Float),
        sa.Column("exec_price", sa.Float),
        sa.Column("exec_type", sa.Text),
        sa.Column("commission", sa.Float, default=0),
        sa.Column("success", sa.Integer, default=0),
        sa.Column("message", sa.Text),
        sa.Column("exchange", sa.Text),
        sa.Column("is_testnet", sa.Integer, default=0),
        sa.Column("created_at", sa.BigInteger),
    )

    op.create_table(
        "xingbu_trades",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Text),
        sa.Column("agent_id", sa.Text),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text),
        sa.Column("quantity", sa.Float),
        sa.Column("exec_price", sa.Float),
        sa.Column("exec_type", sa.Text),
        sa.Column("pnl_pct", sa.Float, default=0),
        sa.Column("created_at", sa.BigInteger),
    )

    op.create_table(
        "xingbu_violations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Text),
        sa.Column("agent_id", sa.Text),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text),
        sa.Column("quantity", sa.Float),
        sa.Column("entry_price", sa.Float),
        sa.Column("reject_reason", sa.Text),
        sa.Column("risk_level", sa.Text),
        sa.Column("rules_triggered", sa.Text),
        sa.Column("created_at", sa.BigInteger),
    )


def downgrade() -> None:
    op.drop_table("xingbu_violations")
    op.drop_table("xingbu_trades")
    op.drop_table("shangshu_executions")
    op.drop_table("alerts")
    op.drop_table("param_history")
    op.drop_table("signal_log")
    op.drop_table("equity_log")
    op.drop_table("trades")
    op.drop_table("positions")
