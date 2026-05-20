"""
交易后验证与反思服务 - Post-Trade Reflection
参考 QuantDinger 的 app/services/reflection.py

功能：
  - 每笔交易结束后，用 LLM 复盘入场/出场逻辑
  - 记录「当时判断 vs 实际结果」
  - 定期汇总 → 校准策略参数建议
  - 维护 analysis_memory 用于长期学习
"""

import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


class DecisionQuality(Enum):
    """决策质量评级"""
    CORRECT = "correct"           # 判断正确
    PARTIALLY = "partially"       # 部分正确
    INCORRECT = "incorrect"       # 判断错误
    UNCERTAIN = "uncertain"       # 无法判断


@dataclass
class TradeReflection:
    """单笔交易反思"""
    trade_id: int
    symbol: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    exit_reason: str
    entry_rationale: str = ""       # 入场时的理由（来自信号日志）
    exit_rationale: str = ""        # 出场时的理由
    was_correct: DecisionQuality = DecisionQuality.UNCERTAIN
    lessons: str = ""               # 学到的教训
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl_pct": self.pnl_pct,
            "exit_reason": self.exit_reason,
            "entry_rationale": self.entry_rationale,
            "exit_rationale": self.exit_rationale,
            "was_correct": self.was_correct.value,
            "lessons": self.lessons,
            "created_at": self.created_at,
        }


@dataclass
class ReflectionSummary:
    """反思汇总"""
    total_trades: int
    correct_trades: int
    incorrect_trades: int
    accuracy: float
    avg_pnl_pct: float
    lessons: List[str]
    calibration_suggestions: List[str]


class ReflectionService:
    """
    交易后反思服务

    使用方式：
      svc = ReflectionService()
      # 单笔交易反思
      reflection = svc.reflect_on_trade(trade_data)
      # 定期汇总
      summary = svc.generate_summary(days=7)
    """

    REFLECTION_PROMPT = """你是一个交易策略复盘顾问。请分析以下交易，判断入场/出场决策是否正确，并总结教训。

交易详情：
- 交易对：{symbol}
- 入场价：{entry_price}
- 出场价：{exit_price}
- 盈亏：{pnl_pct}%
- 出场原因：{exit_reason}
- 入场理由：{entry_rationale}

请以 JSON 格式输出分析结果：
{{
  "was_correct": "correct|partially|incorrect|uncertain",
  "lessons": "这条交易的核心教训（一句话）",
  "should_adjust": true/false,
  "adjustment_suggestion": "如果需要调整参数，具体建议是什么"
}}"""

    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
        db_path: str = None,
    ):
        self.api_key = api_key or os.getenv("AI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
        self.base_url = base_url or os.getenv("AI_BASE_URL", "")
        self.model = model or os.getenv("AI_MODEL", "deepseek-chat")
        self.db_path = db_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "reflection.db"
        )
        self._client = None
        self._init_db()

    @property
    def client(self):
        if self._client is None and _OPENAI_AVAILABLE and self.api_key:
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _init_db(self):
        """初始化反思数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER,
                symbol TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl_pct REAL,
                exit_reason TEXT,
                entry_rationale TEXT,
                exit_rationale TEXT,
                was_correct TEXT,
                lessons TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                param_name TEXT,
                old_value REAL,
                new_value REAL,
                reason TEXT,
                source TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()

    def reflect_on_trade(self, trade_data: dict) -> Optional[TradeReflection]:
        """
        对单笔交易进行反思

        Args:
            trade_data: 包含 trade_id, symbol, entry_price, exit_price, pnl_pct, exit_reason

        Returns:
            TradeReflection 或 None
        """
        reflection = TradeReflection(
            trade_id=trade_data.get("id", 0),
            symbol=trade_data.get("symbol", "UNKNOWN"),
            entry_price=trade_data.get("entry_price", 0),
            exit_price=trade_data.get("exit_price", 0),
            pnl_pct=trade_data.get("pnl_pct", 0),
            exit_reason=trade_data.get("exit_reason", "unknown"),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # 查找入场理由
        entry_rationale = self._get_entry_rationale(reflection.symbol)
        reflection.entry_rationale = entry_rationale

        # AI 分析（如果可用）
        if self.client:
            try:
                analysis = self._ai_analyze(reflection)
                reflection.was_correct = DecisionQuality(analysis.get("was_correct", "uncertain"))
                reflection.lessons = analysis.get("lessons", "")
            except Exception as e:
                logger.error(f"AI 反思失败: {e}")
                reflection.was_correct = self._heuristic_judge(reflection)
        else:
            reflection.was_correct = self._heuristic_judge(reflection)

        # 存储
        self._save_reflection(reflection)

        return reflection

    def _get_entry_rationale(self, symbol: str) -> str:
        """从信号日志获取入场理由"""
        try:
            conn = sqlite3.connect(self.db_path.replace("reflection.db", "live_trading.db"))
            cur = conn.execute(
                "SELECT ai_verdict, message FROM signal_log WHERE symbol LIKE ? ORDER BY created_at DESC LIMIT 1",
                (f"%{symbol.split('/')[0]}%",)
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return row[0] or row[1] or ""
        except Exception:
            pass
        return ""

    def _ai_analyze(self, reflection: TradeReflection) -> dict:
        """使用 AI 分析交易"""
        prompt = self.REFLECTION_PROMPT.format(
            symbol=reflection.symbol,
            entry_price=reflection.entry_price,
            exit_price=reflection.exit_price,
            pnl_pct=reflection.pnl_pct,
            exit_reason=reflection.exit_reason,
            entry_rationale=reflection.entry_rationale,
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)

    @staticmethod
    def _heuristic_judge(reflection: TradeReflection) -> DecisionQuality:
        """启发式判断（无 AI 时）"""
        if reflection.pnl_pct > 2:
            return DecisionQuality.CORRECT
        if reflection.exit_reason == "take_profit":
            return DecisionQuality.CORRECT
        if reflection.exit_reason == "stop_loss" and reflection.pnl_pct < -3:
            return DecisionQuality.INCORRECT
        return DecisionQuality.UNCERTAIN

    def _save_reflection(self, reflection: TradeReflection):
        """存储反思记录"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO reflections
            (trade_id, symbol, entry_price, exit_price, pnl_pct, exit_reason,
             entry_rationale, exit_rationale, was_correct, lessons, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                reflection.trade_id, reflection.symbol,
                reflection.entry_price, reflection.exit_price,
                reflection.pnl_pct, reflection.exit_reason,
                reflection.entry_rationale, reflection.exit_rationale,
                reflection.was_correct.value, reflection.lessons,
                reflection.created_at,
            )
        )
        conn.commit()
        conn.close()

    def generate_summary(self, days: int = 7) -> ReflectionSummary:
        """生成指定天数的反思汇总"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
        from datetime import timedelta
        cutoff -= timedelta(days=days)

        rows = conn.execute(
            "SELECT * FROM reflections WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff.isoformat(),)
        ).fetchall()
        conn.close()

        total = len(rows)
        if total == 0:
            return ReflectionSummary(0, 0, 0, 0, 0, [], [])

        correct = sum(1 for r in rows if r["was_correct"] == "correct")
        incorrect = sum(1 for r in rows if r["was_correct"] in ("incorrect", "partially"))
        avg_pnl = sum(r["pnl_pct"] for r in rows) / total
        accuracy = correct / total * 100

        lessons = list(set(r["lessons"] for r in rows if r["lessons"]))

        # 生成校准建议
        cal_suggestions = []
        if accuracy < 50 and total >= 5:
            cal_suggestions.append("胜率低于50%，建议检查入场条件或增加AI信号过滤严格度")
        if avg_pnl < -1:
            cal_suggestions.append("平均盈亏为负，建议收紧止损或提高止盈比例")

        return ReflectionSummary(
            total_trades=total,
            correct_trades=correct,
            incorrect_trades=incorrect,
            accuracy=round(accuracy, 1),
            avg_pnl_pct=round(avg_pnl, 2),
            lessons=lessons,
            calibration_suggestions=cal_suggestions,
        )
