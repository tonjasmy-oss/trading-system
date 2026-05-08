"""
auditor.py — Reflection Agent 事后独立审计（Agent-S 借鉴）
===========================================================

定位（与 signal_review.py 的区别）：
  - signal_review.py：信号级复盘（单个信号对不对）
  - auditor.py：   交易序列级深度审计（这段时期什么策略+什么市场状态赚钱）

核心功能：
  1. 审计员身份独立运行，不依赖交易主流程
  2. 读取 trade_history.db 的完整交易序列
  3. 按策略 × 市场状态 × 时间窗口 交叉分析
  4. 输出结构化审计报告（含洞察 + 建议）
  5. 将审计结果注入 SignalRouter 的 regime_match 知识库

触发方式：
  - 每日收盘后定时触发（cronjob）
  - 手动触发：auditor.run_audit(symbol="BTC/USDT")

审计报告结构：
  {
    "period": "2026-05-01 ~ 2026-05-07",
    "total_trades": 12,
    "overall_pnl_pct": 3.2,
    "win_rate": 0.58,
    "insights": [
      {
        "type": "strategy_regime",
        "description": "RSI 在 ranging 市场中胜率仅 33%，建议趋势市场启用",
        "recommendation": "当 market_trend='ranging' 时将 RSI 权重从 40% 降至 10%",
        "severity": "warning"
      },
      {
        "type": "stop_loss_timing",
        "description": "50% 的止损在开仓后 2h 内触发（波动率高时）",
        "recommendation": "高波动市场将止损从 2.5% 放宽至 3.5%",
        "severity": "info"
      },
      ...
    ],
    "strategy_rankings": {
      "RSI":   {"count": 5, "win_rate": 0.6, "avg_pnl": 1.2},
      "MACD":  {"count": 4, "win_rate": 0.75, "avg_pnl": 2.1},
      "BB":    {"count": 3, "win_rate": 0.33, "avg_pnl": -0.5},
    },
    "regime_insights": [
      {"trend": "uptrend", "volatility": "high", "count": 3, "avg_pnl": 2.8, "win_rate": 1.0}
    ]
  }
"""

import logging
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
from pathlib import Path
from dataclasses import dataclass, asdict
from collections import defaultdict

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class Insight:
    type: str            # strategy_regime | stop_loss_timing | regime_shift | execution_quality
    description: str
    recommendation: str
    severity: str         # warning | info | critical
    evidence: Dict = None  # 支撑数据

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d


# ─────────────────────────────────────────────────────────────
# 审计引擎
# ─────────────────────────────────────────────────────────────

class Auditor:
    """
    Reflection Agent — 事后独立审计引擎

    设计原则：
      - 读取引擎（只读 trade_history.db，不写主交易流程）
      - 无状态（每次审计独立，可重复运行）
      - 洞察驱动（不只输出统计数字，还要输出可操作的建议）
    """

    DB_NAME = "trade_history.db"

    def __init__(self, db_dir: str = "."):
        self.db_path = Path(db_dir) / self.DB_NAME
        self.findings: List[Insight] = []

    # ── 主入口 ──────────────────────────────────────────────

    def run_audit(
        self,
        symbol: Optional[str] = None,
        days_back: int = 30,
        min_trades: int = 5,
    ) -> Dict[str, Any]:
        """
        执行完整审计。
        Returns: 审计报告字典
        """
        logger.info(f"[Auditor] 开始审计 symbol={symbol or '全部'}, days_back={days_back}")
        self.findings = []

        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())

        # 读取交易数据
        trades = self._load_trades(symbol=symbol, cutoff_ts=cutoff_ts)
        if len(trades) < min_trades:
            logger.info(f"[Auditor] 交易数量 {len(trades)} < {min_trades}，审计终止")
            return {"enough_data": False, "trade_count": len(trades), "min_required": min_trades}

        # ── 审计维度 ──
        self._audit_strategy_regime(trades)
        self._audit_stop_loss_timing(trades)
        self._audit_execution_quality(trades)
        self._audit_regime_stability(trades)
        self._audit_holding_hours(trades)

        # ── 生成报告 ──
        period_start = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        period_end   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        report = {
            "period": f"{period_start} ~ {period_end}",
            "total_trades": len(trades),
            "overall_pnl_pct": round(sum(t["pnl_pct"] for t in trades), 3),
            "win_rate": round(len([t for t in trades if t["pnl_pct"] > 0]) / len(trades), 3),
            "avg_holding_hours": round(
                sum(t.get("holding_hours", 0) or 0 for t in trades) / len(trades), 1
            ),
            "strategy_rankings": self._strategy_rankings(trades),
            "regime_insights": self._regime_insights(trades),
            "exit_reason_stats": self._exit_reason_stats(trades),
            "insights": [f.to_dict() for f in self.findings],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            f"[Auditor] 审计完成: {len(trades)} 笔交易, "
            f"胜率 {report['win_rate']:.0%}, 总盈亏 {report['overall_pnl_pct']:+.2f}%, "
            f"生成 {len(self.findings)} 条洞察"
        )
        return report

    # ── 审计维度 1：策略 × 市场状态 ─────────────────────────

    def _audit_strategy_regime(self, trades: List[Dict]):
        """
        核心审计：什么策略在什么市场状态下赚钱。
        生成洞察：RSI 在 ranging 市场胜率低 → 建议降权。
        """
        # 按 strategy × market_trend 分组
        groups: Dict[tuple, List[Dict]] = defaultdict(list)
        for t in trades:
            if not t.get("strategy") or not t.get("market_trend"):
                continue
            key = (t["strategy"], t["market_trend"])
            groups[key].append(t)

        if len(groups) < 2:
            return  # 数据不足

        best_combo = max(groups.items(), key=lambda x: sum(t["pnl_pct"] for t in x[1]))
        worst_combo = min(groups.items(), key=lambda x: sum(t["pnl_pct"] for t in x[1]))

        best_strat, best_trend = best_combo[0]
        best_avg = sum(t["pnl_pct"] for t in best_combo[1]) / len(best_combo[1])
        worst_strat, worst_trend = worst_combo[0]
        worst_avg = sum(t["pnl_pct"] for t in worst_combo[1]) / len(worst_combo[1])

        if best_avg - worst_avg > 2.0:  # 差异超过 2% 才报
            self.findings.append(Insight(
                type="strategy_regime",
                description=(
                    f"策略 '{best_strat}' 在 '{best_trend}' 市场平均盈利 {best_avg:+.2f}%，"
                    f"而在 '{worst_trend}' 市场仅 {worst_avg:+.2f}%"
                ),
                recommendation=(
                    f"当 market_trend='{best_trend}' 时增加 {best_strat} 权重，"
                    f"当 market_trend='{worst_trend}' 时降低 {best_strat} 权重"
                ),
                severity="warning",
                evidence={
                    "best_combo": {"strategy": best_strat, "trend": best_trend,
                                   "avg_pnl": round(best_avg, 3), "count": len(best_combo[1])},
                    "worst_combo": {"strategy": worst_strat, "trend": worst_trend,
                                    "avg_pnl": round(worst_avg, 3), "count": len(worst_combo[1])},
                },
            ))

        # 按策略统计
        strat_stats: Dict[str, Dict] = defaultdict(lambda: {"wins": 0, "total": 0, "pnls": []})
        for t in trades:
            if not t.get("strategy"):
                continue
            s = t["strategy"]
            strat_stats[s]["total"] += 1
            strat_stats[s]["pnls"].append(t["pnl_pct"])
            if t["pnl_pct"] > 0:
                strat_stats[s]["wins"] += 1

        for strat, stats in strat_stats.items():
            if stats["total"] < 3:
                continue
            wr = stats["wins"] / stats["total"]
            avg = sum(stats["pnls"]) / stats["total"]
            if wr < 0.35 and avg < -0.5:
                self.findings.append(Insight(
                    type="strategy_regime",
                    description=f"策略 '{strat}' 近期 {stats['total']} 次交易胜率 {wr:.0%}，平均 {avg:+.2f}%",
                    recommendation=f"考虑在 '{strat}' 连续亏损 3 次后自动降权或暂停使用",
                    severity="warning" if wr < 0.25 else "info",
                    evidence={"win_rate": round(wr, 3), "avg_pnl": round(avg, 3), "count": stats["total"]},
                ))

    # ── 审计维度 2：止损时机 ────────────────────────────────

    def _audit_stop_loss_timing(self, trades: List[Dict]):
        """分析止损触发的频率和时间分布"""
        stop_loss_trades = [t for t in trades if t.get("exit_reason") == "stop_loss"]
        if len(stop_loss_trades) < 3:
            return

        # 持仓时长 vs 止损关系
        short_holds = [t for t in stop_loss_trades if (t.get("holding_hours") or 0) < 4]
        long_holds  = [t for t in stop_loss_trades if (t.get("holding_hours") or 0) >= 4]

        if len(short_holds) / len(stop_loss_trades) > 0.5:
            self.findings.append(Insight(
                type="stop_loss_timing",
                description=(
                    f"50% 的止损在开仓后 4h 内触发（{len(short_holds)}/{len(stop_loss_trades)} 次），"
                    "说明短期波动噪声触发了止损"
                ),
                recommendation="考虑在高波动时段（如非美国交易时段）临时扩大止损幅度",
                severity="info",
                evidence={
                    "short_hold_count": len(short_holds),
                    "total_stop_loss": len(stop_loss_trades),
                    "short_hold_ratio": round(len(short_holds) / len(stop_loss_trades), 2),
                },
            ))

    # ── 审计维度 3：执行质量（滑点）────────────────────────

    def _audit_execution_quality(self, trades: List[Dict]):
        """分析滑点对盈亏的真实影响"""
        slipages = [abs(t.get("slippage_pct", 0)) for t in trades]
        if not slipages:
            return
        avg_slip = sum(slipages) / len(slipages)

        if avg_slip > 0.15:  # 平均滑点超过 0.15% 才报
            self.findings.append(Insight(
                type="execution_quality",
                description=f"平均滑点 {avg_slip:.3f}%，实盘中滑点可能进一步侵蚀收益",
                recommendation="使用限价单代替市价单，尤其在流动性低的币种",
                severity="warning",
                evidence={"avg_slippage_pct": round(avg_slip, 4)},
            ))

    # ── 审计维度 4：市场状态稳定性 ─────────────────────────

    def _audit_regime_stability(self, trades: List[Dict]):
        """检查是否有市场状态切换导致亏损的模式"""
        trend_groups: Dict[str, List[Dict]] = defaultdict(list)
        for t in trades:
            k = t.get("market_trend", "unknown")
            trend_groups[k].append(t)

        if len(trend_groups) < 2:
            return

        unknown_trades = trend_groups.get("unknown", [])
        if len(unknown_trades) / len(trades) > 0.5:
            self.findings.append(Insight(
                type="regime_shift",
                description=(
                    f"{len(unknown_trades)}/{len(trades)} 笔交易缺少市场状态标注，"
                    "market_regime 模块尚未充分运行"
                ),
                recommendation="确保 market_regime 模块（ P4 ）已启用并正常标注每笔交易",
                severity="info",
                evidence={"unknown_ratio": round(len(unknown_trades) / len(trades), 2)},
            ))

    # ── 审计维度 5：持仓时长分析 ────────────────────────────

    def _audit_holding_hours(self, trades: List[Dict]):
        """分析持仓时长与盈亏的关系"""
        valid = [(t["holding_hours"] or 0, t["pnl_pct"]) for t in trades if t.get("holding_hours")]
        if len(valid) < 5:
            return

        short = [pnl for hrs, pnl in valid if hrs < 12]
        medium = [pnl for hrs, pnl in valid if 12 <= hrs < 48]
        long = [pnl for hrs, pnl in valid if hrs >= 48]

        for label, group in [("短期(<12h)", short), ("中期(12~48h)", medium), ("长期(>48h)", long)]:
            if len(group) < 2:
                continue
            avg = sum(group) / len(group)
            wr = len([p for p in group if p > 0]) / len(group)
            if wr < 0.3:
                self.findings.append(Insight(
                    type="stop_loss_timing",
                    description=f"{label} 持仓胜率仅 {wr:.0%}，平均 {avg:+.2f}%",
                    recommendation=f"{label} 持仓亏损较多，建议复盘是否因趋势判断错误导致",
                    severity="info",
                    evidence={"avg_pnl": round(avg, 3), "win_rate": round(wr, 3), "count": len(group)},
                ))

    # ── 辅助统计 ────────────────────────────────────────────

    def _strategy_rankings(self, trades: List[Dict]) -> Dict:
        strat_map: Dict[str, Dict] = defaultdict(lambda: {"wins": 0, "count": 0, "pnls": []})
        for t in trades:
            if not t.get("strategy"):
                continue
            s = t["strategy"]
            strat_map[s]["count"] += 1
            strat_map[s]["pnls"].append(t["pnl_pct"])
            if t["pnl_pct"] > 0:
                strat_map[s]["wins"] += 1
        result = {}
        for strat, stats in sorted(strat_map.items(), key=lambda x: sum(x[1]["pnls"]), reverse=True):
            pnls = stats["pnls"]
            result[strat] = {
                "count": stats["count"],
                "win_rate": round(stats["wins"] / stats["count"], 3) if stats["count"] else 0,
                "avg_pnl": round(sum(pnls) / len(pnls), 3) if pnls else 0,
            }
        return result

    def _regime_insights(self, trades: List[Dict]) -> List[Dict]:
        groups: Dict[tuple, List[Dict]] = defaultdict(list)
        for t in trades:
            if not t.get("market_trend"):
                continue
            key = (t["market_trend"], t.get("market_volatility", "unknown"))
            groups[key].append(t)
        insights = []
        for (trend, vol), group in groups.items():
            pnls = [t["pnl_pct"] for t in group]
            wins = len([p for p in pnls if p > 0])
            insights.append({
                "trend": trend,
                "volatility": vol,
                "count": len(group),
                "avg_pnl": round(sum(pnls) / len(pnls), 3),
                "win_rate": round(wins / len(group), 3),
            })
        return sorted(insights, key=lambda x: x["avg_pnl"], reverse=True)

    def _exit_reason_stats(self, trades: List[Dict]) -> Dict:
        reason_map: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "pnls": []})
        for t in trades:
            k = t.get("exit_reason", "unknown")
            reason_map[k]["count"] += 1
            reason_map[k]["pnls"].append(t["pnl_pct"])
        result = {}
        for k, stats in reason_map.items():
            pnls = stats["pnls"]
            result[k] = {
                "count": stats["count"],
                "avg_pnl": round(sum(pnls) / len(pnls), 3),
                "win_rate": round(len([p for p in pnls if p > 0]) / len(pnls), 3),
            }
        return result

    # ── 数据加载 ────────────────────────────────────────────

    def _load_trades(self, symbol: Optional[str] = None, cutoff_ts: int = 0) -> List[Dict]:
        if not Path(self.db_path).exists():
            logger.warning(f"[Auditor] 数据库不存在: {self.db_path}")
            return []
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM trade_history WHERE exit_time IS NOT NULL AND entry_time >= ?"
        params: List[Any] = [cutoff_ts]
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        sql += " ORDER BY entry_time ASC"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# 审计报告持久化
# ─────────────────────────────────────────────────────────────

def run_audit_and_save(
    symbol: Optional[str] = None,
    days_back: int = 30,
    min_trades: int = 5,
    output_dir: str = ".",
) -> Dict[str, Any]:
    """
    执行审计并保存报告到 JSON 文件。
    供 cronjob 每日调用。
    """
    auditor = Auditor(db_dir=output_dir)
    report = auditor.run_audit(symbol=symbol, days_back=days_back, min_trades=min_trades)

    if report.get("enough_data", True) is False:
        logger.info(f"[Auditor] 数据不足，跳过报告保存")
        return report

    # 保存报告
    import json
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tag = f"_by_{symbol.replace('/', '_')}" if symbol else "_all"
    filename = f"audit_report{tag}_{ts}.json"
    out_path = Path(output_dir) / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"[Auditor] 报告已保存: {out_path}")
    return report
