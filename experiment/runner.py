"""
实验管线运行器 - Experiment Pipeline Runner
参考 QuantDinger 的 app/services/experiment/runner.py

串联完整的实验流程：
  1. 市场状态识别
  2. LLM 生成策略候选
  3. 批量回测
  4. 多因子评分
  5. 参数进化（最优策略的进一步优化）
  6. 输出最优策略
"""

import os
import sys
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .regime import MarketRegimeDetector, RegimeResult
from .scoring import StrategyScorer, ScoreResult
from .evolution import ParameterEvolver, EvolutionResult
from .llm_strategist import LLMStrategist, StrategySpec

logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    """实验完整结果"""
    symbol: str
    timeframe: str
    regime: Optional[RegimeResult] = None
    candidate_strategies: List[StrategySpec] = field(default_factory=list)
    ranked_strategies: List[ScoreResult] = field(default_factory=list)
    best_strategy: Optional[Dict] = None
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime.to_dict() if self.regime else None,
            "candidates": [s.to_dict() for s in self.candidate_strategies],
            "ranked": [
                {"name": r.strategy_name, "score": r.total_score, "rank": r.rank, "factors": r.factors}
                for r in self.ranked_strategies
            ],
            "best_strategy": self.best_strategy,
            "elapsed_seconds": self.elapsed_seconds,
        }


class ExperimentRunner:
    """
    实验管线运行器

    简化版实现：复用现有 backtest.py 的 BacktestResult，
    不重复造回测引擎，专注于编排流程。

    使用方式：
      runner = ExperimentRunner(symbol="ETH/USDT", timeframe="4h")
      result = runner.run(use_ai=True)
    """

    def __init__(
        self,
        symbol: str = "ETH/USDT",
        timeframe: str = "4h",
        initial_capital: float = 10000.0,
        start_date: str = "2025-01-01",
        end_date: str = "2026-01-01",
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.initial_capital = initial_capital
        self.start_date = start_date
        self.end_date = end_date
        self.regime_detector = MarketRegimeDetector()
        self.scorer = StrategyScorer()
        self.evolver = ParameterEvolver()
        self.llm = LLMStrategist()

    def run(self, use_ai: bool = True) -> ExperimentResult:
        """
        运行完整实验管线

        Args:
            use_ai: 是否使用 AI 生成策略（False 时使用默认策略）

        Returns:
            ExperimentResult
        """
        t0 = time.time()
        result = ExperimentResult(symbol=self.symbol, timeframe=self.timeframe)

        # Step 1: 获取 OHLCV 数据
        logger.info(f"[Experiment] Step 1: 获取 {self.symbol} OHLCV")
        ohlcv = self._fetch_ohlcv()
        if not ohlcv:
            logger.error(f"[Experiment] 无法获取 {self.symbol} 数据")
            result.elapsed_seconds = time.time() - t0
            return result

        # Step 2: 市场状态识别
        logger.info("[Experiment] Step 2: 市场状态识别")
        regime = self.regime_detector.detect(ohlcv)
        result.regime = regime
        logger.info(f"  regime={regime.regime}, confidence={regime.confidence}")

        # Step 3: 策略生成
        logger.info("[Experiment] Step 3: 策略候选生成")
        if use_ai and self.llm.is_available():
            price_summary = {
                "current_price": ohlcv[-1]["close"],
                "volatility": regime.features.get("volatility_pct", 0),
                "trend": regime.features.get("recent_trend_pct", 0),
            }
            strategies = self.llm.generate_strategies(regime.to_dict(), price_summary)
        else:
            strategies = LLMStrategist._default_strategies(regime.to_dict())
        result.candidate_strategies = strategies
        logger.info(f"  {len(strategies)} candidates generated")

        # Step 4: 批量回测
        logger.info("[Experiment] Step 4: 批量回测")
        bt_results = self._run_backtests(ohlcv, strategies)

        # Step 5: 评分排名
        logger.info("[Experiment] Step 5: 多因子评分")
        ranked = self.scorer.score_batch(bt_results)
        result.ranked_strategies = ranked
        for r in ranked:
            logger.info(f"  #{r.rank} {r.strategy_name}: {r.total_score}")

        # Step 6: 最优策略参数进化
        if ranked:
            best = ranked[0]
            best_spec = next(
                (s for s in strategies if s.name == best.strategy_name),
                strategies[0],
            )
            logger.info(f"[Experiment] Step 6: 进化 {best.strategy_name}")
            evolved = self._evolve_best(best_spec, ohlcv)
            result.best_strategy = {
                "name": best_spec.name,
                "strategy_type": best_spec.strategy_type,
                "score": best.total_score,
                "params": evolved,
                "rationale": best_spec.rationale,
            }

        result.elapsed_seconds = round(time.time() - t0, 1)
        logger.info(f"[Experiment] 完成，耗时 {result.elapsed_seconds}s")
        return result

    def _fetch_ohlcv(self) -> Optional[List[dict]]:
        """获取 OHLCV 数据"""
        try:
            from history_cache import get_ohlcv as cache_get_ohlcv
            ohlcv = cache_get_ohlcv(self.symbol, self.timeframe)
            if not ohlcv:
                from crypto_api import get_ohlcv as api_get_ohlcv
                ohlcv = api_get_ohlcv(self.symbol, self.timeframe, limit=200)
            if ohlcv:
                # 标准化为 dict 列表
                if isinstance(ohlcv[0], list):
                    return [
                        {"timestamp": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
                        for r in ohlcv
                    ]
                return ohlcv
        except Exception as e:
            logger.error(f"获取 OHLCV 失败: {e}")
        return None

    def _run_backtests(self, ohlcv, strategies) -> List[Tuple[str, dict]]:
        """批量运行回测"""
        results = []
        from backtest import BacktestEngine
        from strategies import StrategyConfig, RSIStrategy, MACDStrategy, SMAcrossStrategy, BollingerBandsStrategy
        from tdx_compiler import FormulaStrategy

        # 策略类型 → 类映射
        STRATEGY_MAP = {
            "RSI": RSIStrategy,
            "MACD": MACDStrategy,
            "SMAcross": SMAcrossStrategy,
            "Bollinger": BollingerBandsStrategy,
        }

        for spec in strategies:
            try:
                strat_cls = STRATEGY_MAP.get(spec.strategy_type)
                if strat_cls is None:
                    logger.warning(f"  跳过未知策略类型: {spec.strategy_type}")
                    continue

                # 构建配置
                params = spec.params
                cfg = StrategyConfig(
                    symbol=self.symbol,
                    timeframe=self.timeframe,
                    stop_loss=params.get("stop_loss_pct", 0.02),
                    take_profit=params.get("take_profit_pct", 0.04),
                    capital_pct=1.0,
                    trade_direction="long",
                )

                # 创建策略实例
                if spec.strategy_type == "RSI":
                    strat = strat_cls(cfg,
                        rsi_period=int(params.get("rsi_period", 14)),
                        oversold=params.get("rsi_oversold", 30.0),
                        overbought=params.get("rsi_overbought", 70.0))
                elif spec.strategy_type == "Bollinger":
                    strat = strat_cls(cfg,
                        period=int(params.get("bb_period", 20)),
                        std_dev=params.get("bb_std", 2.0))
                elif spec.strategy_type == "MACD":
                    strat = strat_cls(cfg)
                elif spec.strategy_type == "SMAcross":
                    strat = strat_cls(cfg, fast=params.get("fast_sma", 20), slow=params.get("slow_sma", 50))
                else:
                    strat = strat_cls(cfg)

                engine = BacktestEngine(strat, initial_capital=10000.0)
                engine.candles = ohlcv
                engine.compute_signals()
                bt_result = engine.run()
                bt_dict = {
                    "total_return_pct": bt_result.total_return_pct,
                    "sharpe_ratio": bt_result.sharpe_ratio,
                    "max_drawdown_pct": bt_result.max_drawdown_pct,
                    "win_rate_pct": bt_result.win_rate_pct,
                    "total_trades": bt_result.total_trades,
                    "avg_win_pct": getattr(bt_result, 'avg_win_pct', 0),
                    "avg_loss_pct": getattr(bt_result, 'avg_loss_pct', 0),
                }
                results.append((spec.name, bt_dict))
                logger.info(f"  {spec.name}: return={bt_result.total_return_pct:.1f}% dd={bt_result.max_drawdown_pct:.1f}%")

            except Exception as e:
                logger.error(f"  {spec.name} 回测失败: {e}")

        return results

    def _evolve_best(self, best_spec: StrategySpec, ohlcv) -> Dict:
        """对最优策略进行参数进化"""
        param_space = ParameterEvolver.default_parameter_space(best_spec.strategy_type)
        if not param_space:
            return best_spec.params

        evolution = self.evolver.evolve(param_space, method="random", max_variants=10)

        # 测试变体
        best_params = best_spec.params
        best_score = 0

        for variant in evolution.variants:
            # 合并默认参数
            test_params = {**best_spec.params, **variant}
            spec_copy = StrategySpec(
                name=f"{best_spec.name}_v",
                strategy_type=best_spec.strategy_type,
                description="variant",
                params=test_params,
            )
            bt_results = self._run_backtests(ohlcv, [spec_copy])
            if bt_results:
                scored = self.scorer.score_one(bt_results[0][1], spec_copy.name)
                if scored.total_score > best_score:
                    best_score = scored.total_score
                    best_params = test_params

        return best_params
