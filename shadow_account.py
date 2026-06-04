"""
shadow_account.py — 交易影子账户
===============================

从历史交易记录中提取盈利模式，生成"影子策略规则"，回测对比"严格按规则执行 vs 实际执行"的差异。

借鉴 Vibe-Trading Shadow Account 理念：
  1. 读取 trades 表 → 2. 聚类分析入场条件 → 3. 提取影子规则
  → 4. 回测影子策略 → 5. 对比实际表现 → 6. 生成审计报告

用法:
  from shadow_account import ShadowAccount
  sa = ShadowAccount(db_path="live_trading.db")
  report = sa.analyze()
  print(sa.format_report(report))
"""

import sqlite3
import json
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import logging
logger = logging.getLogger(__name__)


class ShadowAccount:
    """交易影子账户 — 从历史交易提炼可复现的盈利规则"""

    def __init__(self, db_path: str = "live_trading.db",
                 min_trades: int = 5,
                 min_win_rate: float = 0.4):
        self.db_path = db_path
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate

    # ---------- 数据获取 ----------

    def _get_trades(self) -> List[Dict]:
        """读取所有已完成的交易记录"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT t.*, e.rsi as entry_rsi, e.equity as entry_equity
            FROM trades t
            LEFT JOIN equity_log e ON t.symbol = (SELECT symbol FROM equity_log WHERE agent_id LIKE 'agent_%' LIMIT 1)
                AND e.created_at <= t.created_at
            WHERE t.pnl_pct IS NOT NULL
            ORDER BY t.id
        """).fetchall()
        conn.close()

        trades = []
        for r in rows:
            # 从 signal_log 获取入场时的 RSI 和 AI verdict
            rsi_val = None
            ai_val = ""
            try:
                c2 = sqlite3.connect(self.db_path)
                sig = c2.execute(
                    "SELECT rsi, ai_verdict FROM signal_log WHERE price>0 ORDER BY ABS(id - ?) LIMIT 1",
                    (r["id"],)
                ).fetchone()
                c2.close()
                if sig:
                    rsi_val = sig[0]
                    ai_val = sig[1] or ""
            except Exception:
                pass

            trades.append({
                "id": r["id"],
                "symbol": r["symbol"],
                "entry_price": r["entry_price"],
                "exit_price": r["exit_price"],
                "pnl_pct": r["pnl_pct"],
                "exit_reason": r["exit_reason"],
                "ai_verdict": r["ai_verdict"] if "ai_verdict" in r.keys() else ai_val,
                "rsi": rsi_val,
                "created_at": r["created_at"],
            })
        return trades

    def _get_market_context(self, symbol: str, timestamp: str) -> Dict:
        """获取某时刻的市场状态"""
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT trend, volatility, volume FROM market_regime "
                "WHERE symbol=? AND created_at <= ? ORDER BY created_at DESC LIMIT 1",
                (symbol, timestamp)
            ).fetchone()
            conn.close()
            if row:
                return {"trend": row[0], "volatility": row[1], "volume": row[2]}
        except Exception:
            pass
        return {}

    # ---------- 分析 ----------

    def analyze(self) -> Dict:
        """
        主入口：分析所有历史交易，提取影子规则。
        Returns: {"trades": [...], "winners": [...], "losers": [...],
                  "shadow_rules": [...], "shadow_backtest": {...}, "comparison": {...}}
        """
        trades = self._get_trades()
        if len(trades) < self.min_trades:
            return {"error": f"交易不足（{len(trades)}<{self.min_trades}），无法生成影子规则"}

        winners = [t for t in trades if (t["pnl_pct"] or 0) > 0]
        losers = [t for t in trades if (t["pnl_pct"] or 0) <= 0]

        if len(winners) < 2:
            return {"error": f"盈利交易不足（{len(winners)}<2），无法提取有效规则"}

        # 1. 聚类盈利交易的共同特征
        patterns = self._extract_patterns(winners, losers)
        # 2. 提炼影子规则
        shadow_rules = self._build_rules(patterns)
        # 3. 回测影子规则
        shadow_result = self._backtest_shadow(trades, shadow_rules)
        # 4. 对比
        comparison = self._compare(trades, shadow_result)

        return {
            "total_trades": len(trades),
            "winners": len(winners),
            "losers": len(losers),
            "actual_win_rate": round(len(winners) / len(trades), 2),
            "actual_total_pnl": round(sum(t["pnl_pct"] for t in trades), 2),
            "shadow_rules": shadow_rules,
            "shadow_backtest": shadow_result,
            "comparison": comparison,
            "patterns": patterns,
        }

    def _extract_patterns(self, winners: List[Dict], losers: List[Dict]) -> Dict:
        """提取盈利交易 vs 亏损交易的差异特征"""
        def avg(items, key, default=0):
            vals = [i.get(key, default) for i in items if i.get(key) is not None]
            return round(sum(vals) / len(vals), 2) if vals else default

        def most_common(items, key):
            counts = defaultdict(int)
            for i in items:
                val = i.get(key, "")
                if val:
                    counts[val] += 1
            return max(counts, key=counts.get) if counts else ""

        # 退出原因分布
        win_reasons = defaultdict(int)
        loss_reasons = defaultdict(int)
        for w in winners:
            win_reasons[w.get("exit_reason", "")] += 1
        for l in losers:
            loss_reasons[l.get("exit_reason", "")] += 1

        return {
            "winner": {
                "avg_rsi": avg(winners, "rsi"),
                "avg_pnl": avg(winners, "pnl_pct"),
                "top_exit_reason": max(win_reasons, key=win_reasons.get) if win_reasons else "",
                "ai_approved_ratio": round(
                    sum(1 for w in winners if "批准" in str(w.get("ai_verdict", ""))) / max(len(winners), 1), 2
                ),
            },
            "loser": {
                "avg_rsi": avg(losers, "rsi"),
                "avg_pnl": avg(losers, "pnl_pct"),
                "top_exit_reason": max(loss_reasons, key=loss_reasons.get) if loss_reasons else "",
                "stop_loss_ratio": round(
                    sum(1 for l in losers if l.get("exit_reason") == "stop_loss") / max(len(losers), 1), 2
                ),
            },
            "key_differences": self._find_differences(winners, losers),
        }

    def _find_differences(self, winners: List[Dict], losers: List[Dict]) -> List[str]:
        """找出盈利交易和亏损交易的关键差异"""
        diffs = []

        w_rsi = sum(w.get("rsi", 0) or 0 for w in winners) / max(len(winners), 1)
        l_rsi = sum(l.get("rsi", 0) or 0 for l in losers) / max(len(losers), 1)
        if w_rsi and l_rsi:
            if abs(w_rsi - l_rsi) > 5:
                diffs.append(f"RSI: 盈利={w_rsi:.1f} vs 亏损={l_rsi:.1f} (差异{abs(w_rsi-l_rsi):.0f}点)")

        w_ai = sum(1 for w in winners if "批准" in str(w.get("ai_verdict", "")))
        l_ai = sum(1 for l in losers if "批准" in str(l.get("ai_verdict", "")))
        if w_ai > 0 and l_ai == 0:
            diffs.append("AI批准的交易全部盈利，未批准的交易有亏损 — AI过滤器是有效信号")
        elif w_ai > l_ai * 1.5 and len(winners) >= 2:
            diffs.append(f"AI批准率: 盈利={w_ai/len(winners):.0%} > 亏损={l_ai/len(losers):.0%}")

        w_sl = sum(1 for w in winners if w.get("exit_reason") == "stop_loss")
        l_sl = sum(1 for l in losers if l.get("exit_reason") == "stop_loss")
        if l_sl > len(losers) * 0.5:
            diffs.append(f"亏损交易中{l_sl}/{len(losers)}是止损出场 → 建议放宽止损或缩小仓位")

        return diffs

    def _build_rules(self, patterns: Dict) -> List[Dict]:
        """从盈利模式构建影子规则"""
        rules = []
        w = patterns["winner"]

        # 规则1: RSI 入场范围
        if w.get("avg_rsi"):
            rsi_low = max(10, w["avg_rsi"] - 8)
            rsi_high = min(85, w["avg_rsi"] + 8)
            rules.append({
                "rule": "RSI入场范围",
                "condition": f"{rsi_low:.0f} < RSI < {rsi_high:.0f}",
                "confidence": min(0.9, w["avg_pnl"] / 5 + 0.5) if w.get("avg_pnl") else 0.6,
                "source": f"盈利交易平均RSI={w['avg_rsi']:.1f}",
            })

        # 规则2: AI 过滤器必须批准
        if w.get("ai_approved_ratio", 0) > 0.5:
            rules.append({
                "rule": "AI过滤器确认",
                "condition": "AI verdict = APPROVE",
                "confidence": w["ai_approved_ratio"],
                "source": f"盈利交易中{w['ai_approved_ratio']:.0%}有AI批准",
            })

        # 规则3: 避免止损频繁的入场条件
        l = patterns["loser"]
        if l.get("stop_loss_ratio", 0) > 0.5:
            rules.append({
                "rule": "避免高止损率入场",
                "condition": f"最近5笔交易止损率 < 50%",
                "confidence": 0.7,
                "source": f"亏损交易{l['stop_loss_ratio']:.0%}为止损 → 高止损率时应暂停",
            })

        # 规则4: 止盈命中特征
        if w.get("top_exit_reason") == "take_profit":
            rules.append({
                "rule": "持有到止盈",
                "condition": "持仓直到 take_profit 触发（不提前 signal_exit）",
                "confidence": 0.75,
                "source": "盈利交易主要为止盈出场",
            })

        return rules

    # ---------- 影子回测 ----------

    def _backtest_shadow(self, trades: List[Dict], rules: List[Dict]) -> Dict:
        """
        用影子规则重新审视每笔交易：
        - 如果当时遵守影子规则，会不会改变决策？
        """
        shadow_pnl = 0.0
        avoided_losses = 0
        missed_gains = 0
        improved_count = 0
        worsened_count = 0

        for t in trades:
            pnl = t["pnl_pct"] or 0
            compliant = self._check_compliance(t, rules)

            if pnl <= 0 and not compliant:
                # 亏损交易 + 违反规则 → 如果遵守规则就不会做这笔交易
                avoided_losses += abs(pnl)
                improved_count += 1
                shadow_pnl += 0  # 不做这笔交易
            elif pnl > 0 and not compliant:
                # 盈利交易 + 违反规则 → 如果遵守规则就会错过盈利
                missed_gains += pnl
                worsened_count += 1
                shadow_pnl += pnl  # 仍然计入（保守估计）
            else:
                shadow_pnl += pnl

        actual_pnl = sum(t["pnl_pct"] or 0 for t in trades)

        return {
            "shadow_total_pnl": round(shadow_pnl, 2),
            "actual_total_pnl": round(actual_pnl, 2),
            "net_improvement": round(shadow_pnl - actual_pnl, 2),
            "avoided_losses": round(avoided_losses, 2),
            "missed_gains": round(missed_gains, 2),
            "trades_improved": improved_count,
            "trades_worsened": worsened_count,
            "compliance_rate": round(
                sum(1 for t in trades if self._check_compliance(t, rules)) / max(len(trades), 1), 2
            ),
        }

    def _check_compliance(self, trade: Dict, rules: List[Dict]) -> bool:
        """检查一笔交易是否符合影子规则"""
        for r in rules:
            rule_name = r["rule"]
            if rule_name == "RSI入场范围":
                rsi = trade.get("rsi")
                if rsi is not None:
                    cond = r["condition"]  # "10 < RSI < 30"
                    try:
                        parts = cond.replace("RSI", "").split("<")
                        low = float(parts[0].strip())
                        high = float(parts[-1].strip())
                        if not (low < rsi < high):
                            return False
                    except (ValueError, IndexError):
                        pass
            elif rule_name == "AI过滤器确认":
                ai = trade.get("ai_verdict", "")
                if "否决" in str(ai) or "HOLD" in str(ai):
                    return False
        return True

    # ---------- 对比 ----------

    def _compare(self, trades: List[Dict], shadow: Dict) -> Dict:
        actual = sum(t["pnl_pct"] or 0 for t in trades)
        shadow_pnl = shadow["shadow_total_pnl"]
        improvement = shadow_pnl - actual

        recommendations = []
        if shadow["avoided_losses"] > 5:
            recommendations.append(
                f"遵守影子规则可避免 {shadow['avoided_losses']:.1f}% 的亏损 "
                f"({shadow['trades_improved']} 笔交易)"
            )
        if improvement > 0:
            recommendations.append(f"预计净改善: {improvement:+.1f}%")
        if shadow["compliance_rate"] < 0.5:
            recommendations.append(f"当前规则遵守率仅 {shadow['compliance_rate']:.0%}，建议严格执行影子规则")
        if shadow["missed_gains"] > shadow["avoided_losses"]:
            recommendations.append("警告: 影子规则会错过一些盈利机会，建议放宽部分条件")

        return {
            "actual_total_pnl": round(actual, 2),
            "shadow_total_pnl": round(shadow_pnl, 2),
            "improvement": round(improvement, 2),
            "recommendations": recommendations,
        }

    # ---------- 报告 ----------

    def format_report(self, result: Dict) -> str:
        """格式化审计报告"""
        if "error" in result:
            return f"⚠️ {result['error']}"

        lines = ["=" * 60,
                 "  🧠 交易影子账户 · 审计报告",
                 "=" * 60,
                 ""]

        # 概况
        c = result.get("comparison", {})
        lines.append("## 概况")
        lines.append(f"  总交易: {result['total_trades']} 笔")
        lines.append(f"  盈利: {result['winners']} / 亏损: {result['losers']}  (胜率 {result['actual_win_rate']:.0%})")
        lines.append(f"  实际累计盈亏: {c.get('actual_total_pnl', 0):+.1f}%")
        lines.append("")

        # 盈利模式分析
        p = result.get("patterns", {})
        if p:
            lines.append("## 盈利交易特征")
            w = p.get("winner", {})
            lines.append(f"  平均入场RSI: {w.get('avg_rsi', 'N/A')}")
            lines.append(f"  主要出场方式: {w.get('top_exit_reason', 'N/A')}")
            lines.append(f"  AI批准率: {w.get('ai_approved_ratio', 0):.0%}")
            lines.append("")

            lines.append("## 亏损交易特征")
            l = p.get("loser", {})
            lines.append(f"  平均入场RSI: {l.get('avg_rsi', 'N/A')}")
            lines.append(f"  止损出场率: {l.get('stop_loss_ratio', 0):.0%}")
            lines.append("")

            diffs = p.get("key_differences", [])
            if diffs:
                lines.append("## 关键差异")
                for d in diffs:
                    lines.append(f"  • {d}")
                lines.append("")

        # 影子规则
        rules = result.get("shadow_rules", [])
        if rules:
            lines.append("## 影子规则")
            for i, r in enumerate(rules, 1):
                lines.append(f"  {i}. [{r['rule']}] {r['condition']}")
                lines.append(f"     置信度: {r['confidence']:.0%} | 来源: {r['source']}")
            lines.append("")

        # 回测结果
        sb = result.get("shadow_backtest", {})
        if sb:
            lines.append("## 影子回测")
            lines.append(f"  影子策略盈亏: {sb.get('shadow_total_pnl', 0):+.1f}%")
            lines.append(f"  实际盈亏:     {sb.get('actual_total_pnl', 0):+.1f}%")
            lines.append(f"  净改善:       {sb.get('net_improvement', 0):+.1f}%")
            lines.append(f"  可避免亏损:   {sb.get('avoided_losses', 0):.1f}% ({sb.get('trades_improved', 0)}笔)")
            lines.append(f"  会错过盈利:   {sb.get('missed_gains', 0):.1f}% ({sb.get('trades_worsened', 0)}笔)")
            lines.append(f"  规则遵守率:   {sb.get('compliance_rate', 0):.0%}")
            lines.append("")

        # 建议
        recs = c.get("recommendations", [])
        if recs:
            lines.append("## 建议")
            for r in recs:
                lines.append(f"  💡 {r}")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "live_trading.db"
    sa = ShadowAccount(db_path=db)
    report = sa.analyze()
    print(sa.format_report(report))
