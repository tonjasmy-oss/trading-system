"""
experiment_pipeline.py — 策略实验管线
======================================

Regime → Generate → Backtest → Multi-Factor Score → Rank → Best

借鉴 QuantDinger /api/experiment/pipeline/run 设计，提供：
  1. 市场状态识别（复用 MarketRegime）
  2. 候选策略生成（Grid Search 参数空间）
  3. 批量回测（复用 BacktestEngine）
  4. 多因子评分（夏普+胜率+收益+回撤+利润因子+稳定性+regime适配）
  5. 最优策略输出

使用方式：
  pipeline = ExperimentPipeline()
  result = pipeline.run(
      symbol="SOL/USDT",
      timeframe="2h",
      strategies=["DONCHIAN", "BOLLINGER", "RSI"],
      parameter_space={
          "DONCHIAN": {"channel_period": [14, 20, 30], "trend_ema_period": [10, 30, 50]},
          "RSI": {"rsi_period": [10, 14], "oversold": [22, 28, 30]},
      },
  )
  print(f"Best: {result.best['strategy']} score={result.best['score']:.2f}")
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import (
    Strategy, StrategyConfig, build_strategy,
    RSIStrategy, MACDStrategy, BollingerBandsStrategy,
    DonchianChannelStrategy,
)
from backtest import BacktestEngine, BacktestResult, TradeRecord

logger = logging.getLogger(__name__)

# ============================================================
# 数据类
# ============================================================

@dataclass
class CandidateResult:
    """单个候选策略的回测结果"""
    strategy_name: str
    params: dict
    backtest: Optional[BacktestResult] = None
    error: Optional[str] = None
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    regime_fit_bonus: int = 0


@dataclass
class ExperimentResult:
    """实验管线完整结果"""
    symbol: str
    timeframe: str
    regime: dict
    candidates_tested: int
    candidates_succeeded: int
    ranked: List[CandidateResult] = field(default_factory=list)
    best: Optional[dict] = None
    duration_seconds: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime,
            "candidates_tested": self.candidates_tested,
            "candidates_succeeded": self.candidates_succeeded,
            "best": self.best,
            "ranked": [
                {
                    "strategy": r.strategy_name,
                    "params": r.params,
                    "score": round(r.score, 2),
                    "score_breakdown": r.score_breakdown,
                    "regime_fit_bonus": r.regime_fit_bonus,
                    "summary": _summarize_backtest(r.backtest) if r.backtest else None,
                }
                for r in self.ranked[:10]  # top 10
            ],
            "duration_seconds": round(self.duration_seconds, 1),
            "timestamp": self.timestamp,
        }


def _summarize_backtest(bt: BacktestResult) -> dict:
    """将 BacktestResult 精简为可序列化的摘要"""
    if bt is None:
        return None
    return {
        "total_return_pct": round(bt.total_return_pct, 2),
        "sharpe_ratio": round(bt.sharpe_ratio, 2),
        "max_drawdown_pct": round(bt.max_drawdown_pct, 2),
        "total_trades": bt.total_trades,
        "winning_trades": bt.winning_trades,
        "losing_trades": bt.losing_trades,
        "win_rate_pct": round(bt.win_rate_pct, 1),
        "profit_factor": _calc_profit_factor(bt),
        "start_date": bt.start_date,
        "end_date": bt.end_date,
    }


def _calc_profit_factor(bt: BacktestResult) -> float:
    """利润因子 = 总盈利 / 总亏损"""
    total_profit = sum(t.pnl_pct for t in bt.trades if t.pnl_pct > 0)
    total_loss = abs(sum(t.pnl_pct for t in bt.trades if t.pnl_pct < 0))
    return round(total_profit / total_loss, 2) if total_loss > 0 else (999.0 if total_profit > 0 else 0.0)


# ============================================================
# 多因子评分引擎
# ============================================================

def multi_factor_score(bt: BacktestResult, regime_fit: int = 0) -> Tuple[float, dict]:
    """
    多因子综合评分 — 借鉴 QuantDinger scoring.py 设计

    权重分配：
      - 夏普比率: 30%  (风险调整后收益最重要)
      - 最大回撤: 25%  (惩罚大回撤)
      - 胜率:     15%  (交易质量)
      - 收益率:   15%  (绝对收益)
      - 利润因子: 10%  (盈亏比)
      - 稳定性:    5%  (回撤持续时间)
    + regime 适配加成: max 10 分
    """
    if bt is None or bt.total_trades == 0:
        return 0.0, {"error": "no trades"}

    # 夏普归一化: 0-100 (夏普>3=满分)
    sharpe_score = min(bt.sharpe_ratio / 3.0 * 100, 100)

    # 回撤惩罚: 回撤越小分越高 (回撤>50%=0, 回撤=0%=100)
    dd_score = max(0, 100 - bt.max_drawdown_pct * 2)

    # 胜率: 直接使用百分比
    win_score = bt.win_rate_pct

    # 收益率归一化: 0-100 (收益>100%=满分)
    ret_score = min(max(bt.total_return_pct, 0), 100)

    # 利润因子归一化: 0-100 (>3=满分)
    pf = _calc_profit_factor(bt)
    pf_score = min(pf / 3.0 * 100, 100)

    # 稳定性: 回撤持续时间越短越好 (ms → hours)
    dd_hours = bt.max_drawdown_duration_ms / 3600000 if bt.max_drawdown_duration_ms else 0
    stability_score = max(0, 100 - dd_hours * 2)

    score = (
        sharpe_score * 0.30
        + dd_score * 0.25
        + win_score * 0.15
        + ret_score * 0.15
        + pf_score * 0.10
        + stability_score * 0.05
    )

    # regime 适配加成: 0~10 分
    regime_bonus = min(regime_fit / 10, 10)
    final_score = score + regime_bonus

    breakdown = {
        "sharpe": round(sharpe_score, 1),
        "max_drawdown": round(dd_score, 1),
        "win_rate": round(win_score, 1),
        "total_return": round(ret_score, 1),
        "profit_factor": round(pf_score, 1),
        "stability": round(stability_score, 1),
        "regime_bonus": round(regime_bonus, 1),
        "raw_score": round(score, 1),
        "final_score": round(final_score, 1),
    }
    return final_score, breakdown


# ============================================================
# 实验管线
# ============================================================

# 默认参数空间（每个策略的默认搜索参数）
DEFAULT_PARAMETER_SPACE = {
    "DONCHIAN": {
        "channel_period": [14, 20, 30],
        "trend_ema_period": [10, 30, 50],
    },
    "BOLLINGER": {
        "period": [14, 20],
        "std_dev": [1.5, 2.0, 2.5],
    },
    "RSI": {
        "rsi_period": [8, 10, 14],
        "oversold": [22, 25, 28, 30],
        "overbought": [60, 65, 70],
    },
    "ATRSTOP": {
        "ema_period": [10, 20],
        "atr_period": [14, 28],
        "atr_multiplier": [1.5, 2.0, 2.5],
    },
    "SMA": {
        "fast_period": [5, 10, 15],
        "slow_period": [20, 30, 50],
    },
    "MACD": {
        "fast_period": [8, 12],
        "slow_period": [21, 26],
        "signal_period": [5, 9],
    },
}

# 默认回测参数
DEFAULT_STOP_LOSS = 0.03
DEFAULT_TAKE_PROFIT = 0.06
DEFAULT_INITIAL_CAPITAL = 10000.0


class ExperimentPipeline:
    """
    策略实验管线 — 自动化策略寻优
    
    流程：
      1. 检测市场状态（MarketRegime）
      2. 生成候选参数组合（Grid Search）
      3. 并行回测所有候选
      4. 多因子评分 + regime 适配加成
      5. 输出最优策略
    """

    def __init__(self, db_dir: str = None):
        self._db_dir = db_dir or os.path.dirname(os.path.abspath(__file__))

    # ── 公开 API ──────────────────────────────────────────

    def run(
        self,
        symbol: str,
        timeframe: str = "4h",
        strategies: Optional[List[str]] = None,
        parameter_space: Optional[dict] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        stop_loss: float = DEFAULT_STOP_LOSS,
        take_profit: float = DEFAULT_TAKE_PROFIT,
        max_workers: int = 4,
        trade_direction: str = "long",
    ) -> ExperimentResult:
        """
        运行完整实验管线

        Args:
            symbol:       交易对，如 "SOL/USDT"
            timeframe:    K线周期
            strategies:   策略列表，默认 ["RSI", "BOLLINGER", "DONCHIAN"]
            parameter_space: 自定义参数空间，格式 {"策略名": {"参数": [值列表]}}
            start_date:   回测起始日期 (可选，默认自动)
            end_date:     回测结束日期 (可选，默认自动)
            initial_capital: 初始资金
            stop_loss:    止损比例
            take_profit:  止盈比例
            max_workers:  并行回测线程数
            trade_direction: 交易方向

        Returns:
            ExperimentResult
        """
        t0 = time.time()
        strategies = strategies or ["RSI", "BOLLINGER", "DONCHIAN"]
        param_space = parameter_space or {}

        # 1. Regime detection
        regime = self._detect_regime(symbol, timeframe)

        # 2. Generate candidates
        candidates = self._generate_candidates(
            symbol, timeframe, strategies, param_space,
            stop_loss, take_profit, initial_capital, trade_direction,
        )
        logger.info(f"[ExperimentPipeline] {symbol} {timeframe}: {len(candidates)} candidates generated")

        # 3. Run backtests in parallel
        results = self._run_backtests(candidates, max_workers)
        succeeded = [r for r in results if r.backtest is not None]
        logger.info(f"[ExperimentPipeline] {len(succeeded)}/{len(results)} backtests succeeded")

        # 4. Score and rank
        self._score_results(results, regime)

        # 5. Select best
        results.sort(key=lambda r: r.score, reverse=True)
        best = None
        if results and results[0].backtest:
            r = results[0]
            best = {
                "strategy": r.strategy_name,
                "params": r.params,
                "score": round(r.score, 2),
                "score_breakdown": r.score_breakdown,
                "summary": _summarize_backtest(r.backtest),
                "regime_fit_bonus": r.regime_fit_bonus,
            }

        elapsed = time.time() - t0
        return ExperimentResult(
            symbol=symbol,
            timeframe=timeframe,
            regime=regime,
            candidates_tested=len(results),
            candidates_succeeded=len(succeeded),
            ranked=results,
            best=best,
            duration_seconds=elapsed,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ── 内部方法 ──────────────────────────────────────────

    def _detect_regime(self, symbol: str, timeframe: str) -> dict:
        """检测市场状态"""
        try:
            from components.market_regime import MarketRegime, recommend_strategies
            mr = MarketRegime(db_dir=self._db_dir)
            state = mr.get_current_regime(symbol, timeframe=timeframe, save=False)
            recs = recommend_strategies(
                state.get("trend", "unknown"),
                state.get("volatility", "unknown"),
                top_n=5,
            )
            return {
                "trend": state.get("trend", "unknown"),
                "volatility": state.get("volatility", "unknown"),
                "volume": state.get("volume", "unknown"),
                "confidence": state.get("confidence", 0),
                "price": state.get("price"),
                "recommended_strategies": [
                    {"strategy": r["strategy"], "fit_score": r["fit_score"]}
                    for r in recs
                ],
            }
        except Exception as e:
            logger.warning(f"[ExperimentPipeline] Regime detection failed: {e}")
            return {"trend": "unknown", "volatility": "unknown", "recommended_strategies": []}

    def _generate_candidates(
        self, symbol, timeframe, strategies, param_space,
        stop_loss, take_profit, initial_capital, trade_direction,
    ) -> List[dict]:
        """生成候选参数组合"""
        candidates = []
        for strategy_name in strategies:
            space = param_space.get(strategy_name, DEFAULT_PARAMETER_SPACE.get(strategy_name, {}))
            keys = list(space.keys())
            if not keys:
                # 无参数空间 → 单一默认配置
                candidates.append({
                    "strategy": strategy_name,
                    "params": {},
                    "config": StrategyConfig(
                        symbol=symbol, timeframe=timeframe,
                        stop_loss=stop_loss, take_profit=take_profit,
                        capital_pct=1.0,
                    ),
                })
                continue

            values = [space[k] for k in keys]
            import itertools
            for combo in itertools.product(*values):
                params = dict(zip(keys, combo))
                candidates.append({
                    "strategy": strategy_name,
                    "params": params,
                    "config": StrategyConfig(
                        symbol=symbol, timeframe=timeframe,
                        stop_loss=stop_loss, take_profit=take_profit,
                        capital_pct=1.0, trade_direction=trade_direction,
                    ),
                })
        return candidates

    def _run_backtests(self, candidates: List[dict], max_workers: int) -> List[CandidateResult]:
        """并行执行所有候选的回测"""
        results = []

        def _run_one(candidate: dict) -> CandidateResult:
            r = CandidateResult(
                strategy_name=candidate["strategy"],
                params=candidate["params"],
            )
            try:
                strategy = self._build_strategy_instance(candidate)
                engine = BacktestEngine(strategy, initial_capital=DEFAULT_INITIAL_CAPITAL)
                if not engine.load_data():
                    r.error = "data load failed"
                    return r
                bt_result = engine.run()
                r.backtest = bt_result
            except Exception as e:
                r.error = str(e)
                logger.debug(f"[ExperimentPipeline] {candidate['strategy']} failed: {e}")
            return r

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_run_one, c) for c in candidates]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.warning(f"[ExperimentPipeline] Backtest worker failed: {e}")

        return results

    def _build_strategy_instance(self, candidate: dict):
        """根据候选配置构建策略实例"""
        name = candidate["strategy"]
        params = candidate["params"]
        config = candidate["config"]

        strategy_map = {
            "DONCHIAN": lambda: DonchianChannelStrategy(
                config=config,
                channel_period=params.get("channel_period", 20),
                trend_ema_period=params.get("trend_ema_period", 50),
            ),
            "BOLLINGER": lambda: BollingerBandsStrategy(
                config=config,
                period=params.get("period", 20),
                std_dev=params.get("std_dev", 2.0),
            ),
            "RSI": lambda: RSIStrategy(
                config=config,
                rsi_period=params.get("rsi_period", 14),
                oversold=params.get("oversold", 30),
                overbought=params.get("overbought", 65),
            ),
            "SMA": lambda: __import__("strategies").SMAcrossStrategy(
                config=config,
                fast_period=params.get("fast_period", 10),
                slow_period=params.get("slow_period", 30),
            ),
            "MACD": lambda: __import__("strategies").MACDStrategy(
                config=config,
                fast_period=params.get("fast_period", 12),
                slow_period=params.get("slow_period", 26),
                signal_period=params.get("signal_period", 9),
            ),
        }

        # 尝试 build_strategy（支持 ATRSTOP, KDJ 等）
        builder = strategy_map.get(name)
        if builder:
            return builder()
        return build_strategy(name, config, **{k: v for k, v in params.items()
                                                if k not in ("stop_loss", "take_profit", "capital_pct")})

    def _score_results(self, results: List[CandidateResult], regime: dict):
        """为所有结果评分"""
        # 构建 regime fit map
        regime_fit_map = {}
        for rec in regime.get("recommended_strategies", []):
            regime_fit_map[rec["strategy"]] = rec["fit_score"]

        for r in results:
            if r.backtest is None:
                r.score = 0
                r.score_breakdown = {"error": r.error or "unknown"}
                continue
            fit = regime_fit_map.get(r.strategy_name, 0)
            score, breakdown = multi_factor_score(r.backtest, regime_fit=fit)
            r.score = score
            r.score_breakdown = breakdown
            r.regime_fit_bonus = fit


# ============================================================
# 便捷函数
# ============================================================

def quick_experiment(symbol: str, timeframe: str = "2h",
                     strategies: List[str] = None) -> dict:
    """
    快速实验：使用默认参数空间运行实验管线
    返回可序列化的 dict
    """
    pipeline = ExperimentPipeline()
    result = pipeline.run(
        symbol=symbol,
        timeframe=timeframe,
        strategies=strategies,
        max_workers=2,
    )
    return result.to_dict()


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="策略实验管线")
    parser.add_argument("symbol", help="交易对，如 SOL/USDT")
    parser.add_argument("--timeframe", "-t", default="2h", help="K线周期")
    parser.add_argument("--strategies", "-s", nargs="*", default=["RSI", "BOLLINGER", "DONCHIAN"])
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    result = quick_experiment(args.symbol, args.timeframe, args.strategies)
    print(json.dumps(result, indent=2, ensure_ascii=False))
