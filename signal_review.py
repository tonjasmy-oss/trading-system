"""
P1/P3: 信号错误复盘机制（SQLite持久化版）
============================================
每次信号被执行后，对比信号 vs 实际行情，自动记录错误模式。
通过 review_signal() 积累错误样本，should_block_signal() 在下单前拦截高风险信号。
"""

from typing import List, Dict, Optional, Tuple
import json
import sqlite3
import os
from datetime import datetime


class SignalReview:
    """
    信号错误复盘器（SQLite持久化版）。
    用法:
      review = SignalReview()
      review.record_signal('sig_001', signal_type=1, confidence=0.85, strategy_name='MultiVote', price=2000.0)
      review.mark_result('sig_001', is_correct=True, actual_return=0.025)
      blocked, reason = review.should_block_signal(1, 0.35, 'MultiVote', {'rsi': 82})
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(os.path.dirname(__file__), "signal_review.db")
        self._init_db()
        self._error_patterns: Dict[str, int] = {}
        self._rebuild_patterns()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_records (
                signal_id TEXT PRIMARY KEY,
                signal_type INTEGER,
                confidence REAL,
                strategy_name TEXT,
                entry_price REAL,
                market TEXT DEFAULT 'CN',
                indicators TEXT,
                recorded_at TEXT,
                result TEXT,
                actual_return REAL,
                holding_period INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS error_patterns (
                pattern TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def _rebuild_patterns(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT pattern, count FROM error_patterns").fetchall()
        conn.close()
        self._error_patterns = {p: c for p, c in rows}

    def record_signal(self, signal_id: str, signal_type: int,
                      confidence: float, strategy_name: str,
                      price: float, market: str = "CN",
                      indicators: Dict = None):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO signal_records
            (signal_id, signal_type, confidence, strategy_name, entry_price, market, indicators, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (signal_id, signal_type, confidence, strategy_name, price, market,
              json.dumps(indicators or {}), datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def mark_result(self, signal_id: str, is_correct: bool,
                     actual_return: float = None,
                     holding_period: int = None):
        conn = sqlite3.connect(self.db_path)
        result_val = "correct" if is_correct else "wrong"
        conn.execute("""
            UPDATE signal_records
            SET result = ?, actual_return = ?, holding_period = ?
            WHERE signal_id = ? AND result IS NULL
        """, (result_val, actual_return, holding_period, signal_id))
        conn.commit()

        if not is_correct:
            pattern = self._extract_pattern(signal_id)
            if pattern:
                self._error_patterns[pattern] = self._error_patterns.get(pattern, 0) + 1
                conn.execute("""
                    INSERT OR REPLACE INTO error_patterns (pattern, count) VALUES (?, ?)
                """, (pattern, self._error_patterns[pattern]))
                conn.commit()
        conn.close()

    def _extract_pattern(self, signal_id: str) -> Optional[str]:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT confidence, indicators FROM signal_records WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        conf, ind_json = row
        ind = json.loads(ind_json or "{}")
        if conf is not None and float(conf) < 0.4:
            return "low_confidence"
        rsi = float(ind.get("rsi", 50))
        if rsi > 75:
            return "rsi_overbought_wrong"
        if rsi < 25:
            return "rsi_oversold_wrong"
        return "unknown_error"

    def get_error_patterns(self, top_n: int = 5) -> List[Tuple[str, int]]:
        sorted_pat = sorted(self._error_patterns.items(), key=lambda x: -x[1])
        return sorted_pat[:top_n]

    def get_signal_quality_score(self, strategy_name: str = None) -> float:
        conn = sqlite3.connect(self.db_path)
        if strategy_name:
            rows = conn.execute("""
                SELECT result, confidence FROM signal_records
                WHERE result IS NOT NULL AND strategy_name = ?
            """, (strategy_name,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT result, confidence FROM signal_records WHERE result IS NOT NULL"
            ).fetchall()
        conn.close()
        if not rows:
            return 0.5
        correct = sum(1 for r in rows if r[0] == "correct")
        avg_conf = sum(float(r[1] or 0.5) for r in rows) / len(rows)
        accuracy = correct / len(rows)
        return round(accuracy * 0.6 + avg_conf * 0.4, 3)

    def get_recent_summary(self, n: int = 20) -> dict:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT result, confidence FROM signal_records ORDER BY recorded_at DESC LIMIT ?
        """, (n,)).fetchall()
        conn.close()
        if not rows:
            return {"total": 0, "correct": 0, "accuracy": 0.0, "avg_confidence": 0.0}
        correct = sum(1 for r in rows if r[0] == "correct")
        return {
            "total": len(rows),
            "correct": correct,
            "accuracy": round(correct / len(rows), 3),
            "avg_confidence": round(sum(float(r[1] or 0.5) for r in rows) / len(rows), 3),
            "error_patterns": self.get_error_patterns(3),
        }

    def should_block_signal(self, signal_type: int, confidence: float,
                            strategy_name: str, indicators: Dict) -> Tuple[bool, str]:
        """决策前检查：是否应该阻止该信号。返回 (blocked, reason)"""
        if confidence < 0.4:
            return True, f"confidence_too_low:{confidence}"
        for pat, count in self.get_error_patterns(3):
            if count < 3:
                continue
            if pat == "low_confidence" and confidence < 0.6:
                return True, f"pattern_blocked:{pat}"
            if pat == "rsi_overbought_wrong" and indicators.get("rsi", 0) > 75:
                return True, f"pattern_blocked:{pat}"
            if pat == "rsi_oversold_wrong" and indicators.get("rsi", 0) < 25:
                return True, f"pattern_blocked:{pat}"
        return False, ""


_global_review: Optional[SignalReview] = None


def get_review() -> SignalReview:
    global _global_review
    if _global_review is None:
        _global_review = SignalReview()
    return _global_review
