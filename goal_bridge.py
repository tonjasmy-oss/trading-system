"""
goal_bridge.py — Vibe-Trading Research Goal 运行时桥接
======================================================

将 Vibe-Trading 的研究目标（Research Goal）运行时概念适配到本地交易系统，
提供长期策略回测优化和参数搜索的审计追踪能力。

核心功能：
  1. GoalRuntime — 研究目标生命周期管理器
  2. BacktestGoal — 回测优化目标（参数搜索 + 结果审计）
  3. 证据链追踪 — 每次回测结果自动存档为 Goal 证据
  4. 完成审计 — 目标完成时自动核查所有标准是否达标

与 Vibe-Trading GoalStore 的关系：
  本模块使用本地 SQLite 数据库（goal_runtime.db），复刻了 Vibe-Trading
  GoalStore 的数据模型（Goal/Criterion/Claim/Evidence），但采用轻量化实现，
  无需依赖 LangChain/LangGraph。

使用方式：
  from goal_bridge import GoalRuntime, BacktestGoal

  # 创建回测优化目标
  runtime = GoalRuntime()
  goal = runtime.create_backtest_goal(
      symbol="BTC/USDT",
      strategy="RSI",
      target_metric="sharpe_ratio",
      target_value=1.5,
      search_space={"rsi_period": (8, 20, 2), "oversold": (20, 35, 5)},
  )

  # 记录回测结果作为证据
  runtime.add_backtest_evidence(
      goal_id=goal.goal_id,
      params={"rsi_period": 10, "oversold": 28},
      metrics={"sharpe_ratio": 1.62, "total_return": 0.18, "max_drawdown": -0.05},
  )

  # 检查是否达标
  if runtime.check_completion(goal.goal_id):
      runtime.complete_goal(goal.goal_id)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    BUDGET_LIMITED = "budget_limited"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RiskTier(str, Enum):
    RESEARCH_GENERAL = "research_general"
    MARKET_SPECIFIC = "market_specific_short_term"
    BACKTEST_OPTIMIZATION = "backtest_optimization"


@dataclass
class GoalRecord:
    goal_id: str
    session_id: str
    status: GoalStatus
    objective: str
    ui_summary: str
    risk_tier: RiskTier
    strategy: str = ""
    symbol: str = ""
    target_metric: str = ""
    target_value: float = 0.0
    token_budget: int = 0
    tokens_used: int = 0
    turn_budget: int = 0
    turns_used: int = 0
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""
    recap: str = ""


@dataclass
class CriterionRecord:
    criterion_id: str
    goal_id: str
    text: str
    required: bool = True
    status: str = "pending"
    created_at: str = ""


@dataclass
class EvidenceRecord:
    evidence_id: str
    goal_id: str
    text: str
    criterion_id: str = ""
    evidence_type: str = "evidence"
    params: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    confidence: str = ""
    created_at: str = ""


# ══════════════════════════════════════════════════════════════
# 默认回测标准
# ══════════════════════════════════════════════════════════════

BACKTEST_CRITERIA = [
    "Define the optimization objective (metric + target value)",
    "Execute parameter search over the defined search space",
    "Record top-N parameter sets with performance metrics",
    "Verify best parameters on out-of-sample data",
    "Document the final recommendation with caveats",
]


# ══════════════════════════════════════════════════════════════
# GoalRuntime — 研究目标运行时
# ══════════════════════════════════════════════════════════════

class GoalRuntime:
    """
    研究目标运行时管理器。

    管理回测优化目标的完整生命周期：创建 → 证据追踪 → 审计 → 完成。

    使用 SQLite 持久化，支持跨进程/跨重启的目标状态追踪。
    """

    def __init__(self, db_path: str = ""):
        self._db_path = Path(db_path or str(Path(__file__).parent / "goal_runtime.db"))
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS goals (
                    goal_id       TEXT PRIMARY KEY,
                    session_id    TEXT NOT NULL DEFAULT '',
                    status        TEXT NOT NULL DEFAULT 'active',
                    objective     TEXT NOT NULL DEFAULT '',
                    ui_summary    TEXT NOT NULL DEFAULT '',
                    risk_tier     TEXT NOT NULL DEFAULT 'backtest_optimization',
                    strategy      TEXT NOT NULL DEFAULT '',
                    symbol        TEXT NOT NULL DEFAULT '',
                    target_metric TEXT NOT NULL DEFAULT '',
                    target_value  REAL NOT NULL DEFAULT 0.0,
                    token_budget  INTEGER NOT NULL DEFAULT 0,
                    tokens_used   INTEGER NOT NULL DEFAULT 0,
                    turn_budget   INTEGER NOT NULL DEFAULT 0,
                    turns_used    INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT NOT NULL DEFAULT '',
                    updated_at    TEXT NOT NULL DEFAULT '',
                    completed_at  TEXT NOT NULL DEFAULT '',
                    recap         TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS criteria (
                    criterion_id TEXT PRIMARY KEY,
                    goal_id      TEXT NOT NULL,
                    text         TEXT NOT NULL DEFAULT '',
                    required     INTEGER NOT NULL DEFAULT 1,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    created_at   TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (goal_id) REFERENCES goals(goal_id)
                );

                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id   TEXT PRIMARY KEY,
                    goal_id       TEXT NOT NULL,
                    text          TEXT NOT NULL DEFAULT '',
                    criterion_id  TEXT NOT NULL DEFAULT '',
                    evidence_type TEXT NOT NULL DEFAULT 'evidence',
                    params        TEXT NOT NULL DEFAULT '{}',
                    metrics       TEXT NOT NULL DEFAULT '{}',
                    confidence    TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (goal_id) REFERENCES goals(goal_id)
                );

                CREATE INDEX IF NOT EXISTS idx_criteria_goal ON criteria(goal_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_goal ON evidence(goal_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_criterion ON evidence(criterion_id);
            """)

    # ── 创建目标 ────────────────────────────────────────────

    def create_backtest_goal(
        self,
        symbol: str,
        strategy: str,
        target_metric: str = "sharpe_ratio",
        target_value: float = 1.0,
        search_space: Optional[Dict[str, Tuple]] = None,
        turn_budget: int = 50,
    ) -> GoalRecord:
        """
        创建回测优化目标。

        Args:
            symbol:        标的（如 "BTC/USDT"）
            strategy:      策略名（如 "RSI", "VOTE", "SWARM:crypto_trading_desk"）
            target_metric: 优化目标指标（sharpe_ratio / total_return / max_drawdown 等）
            target_value:  目标值（如 sharpe_ratio >= 1.5）
            search_space:  参数搜索空间，如 {"rsi_period": (8, 20, 2), "oversold": (20, 35, 5)}
            turn_budget:   最大回测轮数

        Returns:
            GoalRecord
        """
        now = datetime.now(timezone.utc).isoformat()
        goal_id = f"goal_{uuid.uuid4().hex[:12]}"
        session_id = f"bt_{uuid.uuid4().hex[:8]}"

        objective = (
            f"优化 {symbol} 的 {strategy} 策略参数，"
            f"目标 {target_metric} >= {target_value}，"
            f"搜索空间: {json.dumps(search_space or {}, ensure_ascii=False)}"
        )
        ui_summary = f"[{symbol}] {strategy} 参数优化 → {target_metric} ≥ {target_value}"

        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO goals (goal_id, session_id, status, objective, ui_summary,
                   risk_tier, strategy, symbol, target_metric, target_value,
                   turn_budget, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    goal_id, session_id, GoalStatus.ACTIVE.value,
                    objective, ui_summary,
                    RiskTier.BACKTEST_OPTIMIZATION.value,
                    strategy, symbol, target_metric, target_value,
                    turn_budget, now, now,
                ),
            )

            # 插入默认标准
            for i, text in enumerate(BACKTEST_CRITERIA):
                criterion_id = f"crit_{goal_id}_{i}"
                conn.execute(
                    """INSERT INTO criteria (criterion_id, goal_id, text, required, status, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (criterion_id, goal_id, text, 1, "pending", now),
                )

        return self.get_goal(goal_id)

    # ── 读取目标 ────────────────────────────────────────────

    def get_goal(self, goal_id: str) -> GoalRecord:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM goals WHERE goal_id=?", (goal_id,)).fetchone()
            if not row:
                raise ValueError(f"目标 {goal_id} 不存在")
            return GoalRecord(
                goal_id=row["goal_id"],
                session_id=row["session_id"],
                status=GoalStatus(row["status"]),
                objective=row["objective"],
                ui_summary=row["ui_summary"],
                risk_tier=RiskTier(row["risk_tier"]),
                strategy=row["strategy"],
                symbol=row["symbol"],
                target_metric=row["target_metric"],
                target_value=row["target_value"],
                token_budget=row["token_budget"],
                tokens_used=row["tokens_used"],
                turn_budget=row["turn_budget"],
                turns_used=row["turns_used"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                recap=row["recap"],
            )

    def list_active_goals(self) -> List[GoalRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status='active' ORDER BY created_at DESC"
            ).fetchall()
        return [self.get_goal(r["goal_id"]) for r in rows]

    # ── 证据添加 ────────────────────────────────────────────

    def add_backtest_evidence(
        self,
        goal_id: str,
        params: Dict[str, Any],
        metrics: Dict[str, Any],
        criterion_id: str = "",
        confidence: str = "",
    ) -> EvidenceRecord:
        """
        记录一次回测结果作为目标证据。

        Args:
            goal_id:      目标 ID
            params:       回测参数，如 {"rsi_period": 10, "oversold": 28}
            metrics:      回测指标，如 {"sharpe_ratio": 1.62, "total_return": 0.18}
            criterion_id: 关联的标准 ID（空则自动匹配第一个 pending 标准）
            confidence:   置信度（如 "high", "medium", "low"）

        Returns:
            EvidenceRecord
        """
        now = datetime.now(timezone.utc).isoformat()
        evidence_id = f"ev_{uuid.uuid4().hex[:12]}"

        # 构建可读文本
        params_str = ", ".join(f"{k}={v}" for k, v in params.items())
        metrics_str = ", ".join(f"{k}={v}" for k, v in metrics.items())
        # 加目标指标判定
        goal = self.get_goal(goal_id)
        metric_val = metrics.get(goal.target_metric, "N/A")
        verdict = "✅ 达标" if isinstance(metric_val, (int, float)) and metric_val >= goal.target_value else "🔍 未达标"
        text = f"[回测 #{self._next_evidence_num(goal_id)}] 参数: {params_str} | 指标: {metrics_str} | {verdict}"

        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO evidence (evidence_id, goal_id, text, criterion_id,
                   evidence_type, params, metrics, confidence, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id, goal_id, text, criterion_id,
                    "backtest_result",
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(metrics, ensure_ascii=False),
                    confidence, now,
                ),
            )
            conn.execute(
                "UPDATE goals SET turns_used=turns_used+1, updated_at=? WHERE goal_id=?",
                (now, goal_id),
            )

        return self.get_evidence(evidence_id)

    def _next_evidence_num(self, goal_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM evidence WHERE goal_id=?", (goal_id,)
            ).fetchone()
        return (row["cnt"] or 0) + 1

    def get_evidence(self, evidence_id: str) -> EvidenceRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"证据 {evidence_id} 不存在")
        return EvidenceRecord(
            evidence_id=row["evidence_id"],
            goal_id=row["goal_id"],
            text=row["text"],
            criterion_id=row["criterion_id"],
            evidence_type=row["evidence_type"],
            params=json.loads(row["params"] or "{}"),
            metrics=json.loads(row["metrics"] or "{}"),
            confidence=row["confidence"],
            created_at=row["created_at"],
        )

    def list_evidence(self, goal_id: str) -> List[EvidenceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence WHERE goal_id=? ORDER BY created_at ASC",
                (goal_id,),
            ).fetchall()
        return [
            EvidenceRecord(
                evidence_id=r["evidence_id"],
                goal_id=r["goal_id"],
                text=r["text"],
                criterion_id=r["criterion_id"],
                evidence_type=r["evidence_type"],
                params=json.loads(r["params"] or "{}"),
                metrics=json.loads(r["metrics"] or "{}"),
                confidence=r["confidence"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ── 标准管理 ────────────────────────────────────────────

    def get_criteria(self, goal_id: str) -> List[CriterionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM criteria WHERE goal_id=? ORDER BY created_at ASC",
                (goal_id,),
            ).fetchall()
        return [
            CriterionRecord(
                criterion_id=r["criterion_id"],
                goal_id=r["goal_id"],
                text=r["text"],
                required=bool(r["required"]),
                status=r["status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def update_criterion(self, criterion_id: str, status: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE criteria SET status=?, updated_at=? WHERE criterion_id=?",
                (status, now, criterion_id),
            )

    # ── 完成审计 ────────────────────────────────────────────

    def check_completion(self, goal_id: str) -> bool:
        """检查所有必需标准是否已满足"""
        criteria = self.get_criteria(goal_id)
        required = [c for c in criteria if c.required]
        if not required:
            return False
        return all(c.status in ("satisfied", "satisfied_with_caveat") for c in required)

    def auto_audit(self, goal_id: str) -> Dict[str, Any]:
        """
        自动审计目标完成度。

        检查:
          1. 是否每个必需标准都有至少一条证据
          2. 是否有达标记录（metrics 中 target_metric >= target_value）
          3. 是否有 OOS 验证记录

        Returns:
            审计报告 dict
        """
        goal = self.get_goal(goal_id)
        criteria = self.get_criteria(goal_id)
        evidence = self.list_evidence(goal_id)

        report = {
            "goal_id": goal_id,
            "objective": goal.objective,
            "criteria_total": len(criteria),
            "criteria_covered": 0,
            "evidence_count": len(evidence),
            "best_metrics": {},
            "best_params": {},
            "target_met": False,
            "oos_verified": False,
            "verdict": "insufficient_evidence",
        }

        # 检查是否有达标记录
        best_value = -float("inf")
        for ev in evidence:
            metric_val = ev.metrics.get(goal.target_metric, None)
            if isinstance(metric_val, (int, float)) and metric_val > best_value:
                best_value = metric_val
                report["best_metrics"] = ev.metrics
                report["best_params"] = ev.params

        if best_value >= goal.target_value:
            report["target_met"] = True

        # 检查 OOS 验证
        for ev in evidence:
            if "oos" in ev.evidence_type.lower() or "验证" in ev.text:
                report["oos_verified"] = True
                break

        # 覆盖标准
        for crit in criteria:
            has_evidence = any(
                ev.criterion_id == crit.criterion_id for ev in evidence
            )
            if has_evidence:
                report["criteria_covered"] += 1
                self.update_criterion(crit.criterion_id, "satisfied")

        if report["target_met"] and report["oos_verified"]:
            report["verdict"] = "complete"
        elif report["target_met"]:
            report["verdict"] = "needs_oos_verification"

        return report

    def complete_goal(self, goal_id: str, recap: str = "") -> GoalRecord:
        """标记目标为完成"""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE goals SET status=?, completed_at=?, recap=?, updated_at=?
                   WHERE goal_id=?""",
                (GoalStatus.COMPLETE.value, now, recap, now, goal_id),
            )
        return self.get_goal(goal_id)

    # ── 进度摘要 ────────────────────────────────────────────

    def get_progress(self, goal_id: str) -> Dict[str, Any]:
        """获取目标进度摘要"""
        goal = self.get_goal(goal_id)
        criteria = self.get_criteria(goal_id)
        evidence = self.list_evidence(goal_id)

        covered = sum(1 for c in criteria if c.status != "pending")
        return {
            "goal_id": goal_id,
            "status": goal.status.value,
            "objective": goal.objective,
            "symbol": goal.symbol,
            "strategy": goal.strategy,
            "target": f"{goal.target_metric} ≥ {goal.target_value}",
            "criteria_progress": f"{covered}/{len(criteria)}",
            "evidence_count": len(evidence),
            "turns_used": goal.turns_used,
            "turn_budget": goal.turn_budget,
            "best_so_far": self._best_result(goal_id),
        }

    def _best_result(self, goal_id: str) -> Optional[Dict]:
        goal = self.get_goal(goal_id)
        evidence = self.list_evidence(goal_id)
        best = None
        best_val = -float("inf")
        for ev in evidence:
            val = ev.metrics.get(goal.target_metric, None)
            if isinstance(val, (int, float)) and val > best_val:
                best_val = val
                best = {"params": ev.params, "metrics": ev.metrics}
        return best


# ══════════════════════════════════════════════════════════════
# BacktestGoal — 便捷回测目标封装
# ══════════════════════════════════════════════════════════════

class BacktestGoal:
    """
    回测优化目标的便捷封装。

    自动管理参数搜索的生命周期：
      create() → add_result() × N → audit() → complete()
    """

    def __init__(self, runtime: Optional[GoalRuntime] = None):
        self._runtime = runtime or GoalRuntime()
        self._goal_id: str = ""

    def create(
        self,
        symbol: str,
        strategy: str,
        target_metric: str = "sharpe_ratio",
        target_value: float = 1.0,
        search_space: Optional[Dict] = None,
        turn_budget: int = 50,
    ) -> GoalRecord:
        goal = self._runtime.create_backtest_goal(
            symbol=symbol,
            strategy=strategy,
            target_metric=target_metric,
            target_value=target_value,
            search_space=search_space,
            turn_budget=turn_budget,
        )
        self._goal_id = goal.goal_id
        logger.info(f"回测目标已创建: {goal.ui_summary}")
        return goal

    def add_result(self, params: Dict, metrics: Dict) -> EvidenceRecord:
        if not self._goal_id:
            raise RuntimeError("请先调用 create()")
        return self._runtime.add_backtest_evidence(
            goal_id=self._goal_id,
            params=params,
            metrics=metrics,
        )

    def audit(self) -> Dict[str, Any]:
        if not self._goal_id:
            raise RuntimeError("请先调用 create()")
        return self._runtime.auto_audit(self._goal_id)

    def complete(self, recap: str = "") -> GoalRecord:
        if not self._goal_id:
            raise RuntimeError("请先调用 create()")
        return self._runtime.complete_goal(self._goal_id, recap)

    @property
    def goal_id(self) -> str:
        return self._goal_id

    @property
    def progress(self) -> Dict[str, Any]:
        if not self._goal_id:
            return {}
        return self._runtime.get_progress(self._goal_id)
