"""
批量回测引擎 - Batch Backtest Engine
参考金策智算 batch_backtest_tasks.csv + Grid Search 参数优化

功能：
  - 并行/串行跑多组 (symbol, timeframe, strategy, 参数) 组合
  - Grid Search 自动寻优（RSI_period / oversold / overbought / stop_loss / take_profit）
  - 输出对比表：谁夏普最高、谁回撤最小
  - 保存最优参数到 config.json
  - 支持增量回测（新数据只回测参数变化的标的）

使用方式：
  python batch_backtest.py                         # 全量回测所有配置
  python batch_backtest.py --symbols BTC ETH SOL   # 只回测指定标的
  python batch_batchtest.py --grid-search          # Grid Search 模式
  python batch_backtest.py --incremental            # 增量回测（仅新数据）
"""

import os
import sys
import json
import logging
import math
import sqlite3
import time
import itertools
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies import Signal, Strategy, RSIStrategy, SMAcrossStrategy, StrategyConfig
from backtest import BacktestEngine, BacktestResult, TradeRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================

BACKTEST_DIR = os.path.join(os.path.dirname(__file__), "backtest_results")
GRID_SEARCH_DIR = os.path.join(BACKTEST_DIR, "grid_search")
PARAM_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config_optimized.json")

INITIAL_CAPITAL = 10000.0
DEFAULT_TIMEFRAME = "4h"

# Grid Search 参数空间
GRID_RSI_PERIOD = [6, 8, 10, 14]
GRID_OVERSOLD = [18, 20, 22, 25, 28]
GRID_OVERBOUGHT = [65, 70, 73, 75, 78]
GRID_STOP_LOSS = [0.015, 0.02, 0.025, 0.03, 0.04]
GRID_TAKE_PROFIT = [0.03, 0.04, 0.05, 0.06, 0.08]

# ============================================================
# 数据类
# ============================================================

@dataclass
class BacktestConfig:
    """单次回测配置"""
    symbol: str
    timeframe: str = DEFAULT_TIMEFRAME
    strategy: str = "RSIStrategy"
    rsi_period: int = 8
    oversold: float = 22.0
    overbought: float = 75.0
    stop_loss: float = 0.025
    take_profit: float = 0.04
    capital_pct: float = 1.0
    initial_capital: float = INITIAL_CAPITAL

    def to_strategy_config(self) -> StrategyConfig:
        return StrategyConfig(
            symbol=self.symbol,
            timeframe=self.timeframe,
            capital_pct=self.capital_pct,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
        )

    def make_strategy(self) -> Strategy:
        if self.strategy == "RSIStrategy":
            return RSIStrategy(
                config=self.to_strategy_config(),
                rsi_period=self.rsi_period,
                oversold=self.oversold,
                overbought=self.overbought,
            )
        elif self.strategy == "SMAcrossStrategy":
            return SMAcrossStrategy(
                config=self.to_strategy_config(),
                fast_period=10,
                slow_period=30,
            )
        raise ValueError(f"Unknown strategy: {self.strategy}")

    @property
    def config_id(self) -> str:
        return f"{self.symbol}_{self.strategy}_rsi{self.rsi_period}_os{self.oversold}_ob{self.overbought}_sl{self.stop_loss}_tp{self.take_profit}"


@dataclass
class GridSearchResult:
    """参数组合回测结果"""
    config: BacktestConfig
    result: BacktestResult
    score: float   # 综合评分（夏普 * 权重 - 回撤 * 权重）

    @staticmethod
    def calc_score(r: BacktestResult) -> float:
        """
        综合评分 = 夏普 * 0.4 + 胜率 * 0.2 + 收益率 * 0.1 - 回撤 * 0.3
        分数越高越好
        """
        return (
            r.sharpe_ratio * 0.4
            + r.win_rate_pct * 0.2
            + r.total_return_pct * 0.1
            - r.max_drawdown_pct * 0.3
        )


# ============================================================
# 批量回测器
# ============================================================

class BatchBacktester:
    """
    批量回测器

    支持：
      - 全量回测（所有预设配置）
      - Grid Search（遍历参数组合）
      - 增量回测（仅回测有变化的标的）
    """

    def __init__(self, symbols: Optional[List[str]] = None):
        self.symbols = symbols or ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        os.makedirs(BACKTEST_DIR, exist_ok=True)
        os.makedirs(GRID_SEARCH_DIR, exist_ok=True)

        # 加载已有最优参数
        self.optimized_params = self._load_optimized_params()

    # ======================== 公开 API ========================

    def run_all(self, configs: List[BacktestConfig], parallel: bool = True) -> List[BacktestResult]:
        """
        并行/串行执行所有回测配置
        Returns: 回测结果列表
        """
        logger.info(f"开始批量回测，共 {len(configs)} 个配置，标的: {[c.symbol for c in configs]}")
        results = []

        if parallel:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._run_single, cfg): cfg
                    for cfg in configs
                }
                for future in as_completed(futures):
                    cfg = futures[future]
                    try:
                        result = future.result()
                        results.append(result)
                        self._print_result_summary(cfg, result)
                    except Exception as e:
                        logger.error(f"[{cfg.symbol}] 回测失败: {e}")
        else:
            for cfg in configs:
                try:
                    result = self._run_single(cfg)
                    results.append(result)
                    self._print_result_summary(cfg, result)
                except Exception as e:
                    logger.error(f"[{cfg.symbol}] 回测失败: {e}")

        return results

    def grid_search(
        self,
        symbol: str,
        strategy: str = "RSIStrategy",
        max_workers: int = 4,
    ) -> List[GridSearchResult]:
        """
        Grid Search 参数寻优

        遍历所有参数组合，找出最优配置
        结果按 score 排序，保存到 grid_search_report.json
        """
        logger.info(f"开始 Grid Search: {symbol} 策略={strategy}")
        logger.info(
            f"参数空间: RSI_period={GRID_RSI_PERIOD}, oversold={GRID_OVERSOLD}, "
            f"overbought={GRID_OVERBOUGHT}, SL={GRID_STOP_LOSS}, TP={GRID_TAKE_PROFIT}"
        )

        # 生成所有组合
        configs = []
        for rp, os_val, ob_val, sl, tp in itertools.product(
            GRID_RSI_PERIOD, GRID_OVERSOLD, GRID_OVERBOUGHT,
            GRID_STOP_LOSS, GRID_TAKE_PROFIT,
        ):
            configs.append(BacktestConfig(
                symbol=symbol,
                strategy=strategy,
                rsi_period=rp,
                oversold=os_val,
                overbought=ob_val,
                stop_loss=sl,
                take_profit=tp,
            ))

        total = len(configs)
        logger.info(f"共 {total} 个参数组合...")

        best_results: List[GridSearchResult] = []
        completed = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._run_single, cfg): cfg for cfg in configs}

            for future in as_completed(futures):
                cfg = futures[future]
                completed += 1
                try:
                    result = future.result()
                    score = GridSearchResult.calc_score(result)
                    gs_result = GridSearchResult(config=cfg, result=result, score=score)
                    best_results.append(gs_result)

                    # Top10 实时日志
                    best_results.sort(key=lambda x: x.score, reverse=True)
                    if len(best_results) > 10:
                        best_results = best_results[:10]

                    if completed % 50 == 0:
                        logger.info(f"进度 {completed}/{total}  当前Top1 Score={best_results[0].score:.3f}")

                except Exception as e:
                    logger.error(f"[{cfg.symbol} {cfg.config_id}] 失败: {e}")

        # 排序
        best_results.sort(key=lambda x: x.score, reverse=True)

        # 保存报告
        report_path = os.path.join(
            GRID_SEARCH_DIR,
            f"{symbol.replace('/', '_')}_grid_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        report = {
            "symbol": symbol,
            "strategy": strategy,
            "total_combinations": total,
            "best_config": self._serialize_config(best_results[0].config),
            "best_score": round(best_results[0].score, 3),
            "top10": [
                {
                    "rank": i + 1,
                    "config": self._serialize_config(gs.config),
                    "score": round(gs.score, 3),
                    "total_return_pct": round(gs.result.total_return_pct, 2),
                    "sharpe_ratio": round(gs.result.sharpe_ratio, 2),
                    "max_drawdown_pct": round(gs.result.max_drawdown_pct, 2),
                    "win_rate_pct": round(gs.result.win_rate_pct, 2),
                    "total_trades": gs.result.total_trades,
                }
                for i, gs in enumerate(best_results[:10])
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"Grid Search 完成，最优配置已保存: {report_path}")
        self._print_grid_search_top10(best_results[:10])

        return best_results

    def load_or_run_default_configs(self) -> List[BacktestResult]:
        """
        加载预设配置（如 config.json 中的最优参数）
        如有变更则重新回测
        """
        configs = self._build_default_configs()
        return self.run_all(configs, parallel=True)

    def update_optimized_params(self, symbol: str, best_config: BacktestConfig, result: BacktestResult):
        """回测完成后，更新最优参数到内存和磁盘"""
        self.optimized_params[symbol] = {
            "symbol": symbol,
            "strategy": best_config.strategy,
            "rsi_period": best_config.rsi_period,
            "oversold": best_config.oversold,
            "overbought": best_config.overbought,
            "stop_loss": best_config.stop_loss,
            "take_profit": best_config.take_profit,
            "total_return_pct": round(result.total_return_pct, 2),
            "sharpe_ratio": round(result.sharpe_ratio, 2),
            "max_drawdown_pct": round(result.max_drawdown_pct, 2),
            "win_rate_pct": round(result.win_rate_pct, 2),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_optimized_params()

    def get_optimized_config(self, symbol: str) -> Optional[BacktestConfig]:
        """获取某标的的最优配置"""
        p = self.optimized_params.get(symbol)
        if not p:
            return None
        return BacktestConfig(
            symbol=p["symbol"],
            strategy=p["strategy"],
            rsi_period=p["rsi_period"],
            oversold=p["oversold"],
            overbought=p["overbought"],
            stop_loss=p["stop_loss"],
            take_profit=p["take_profit"],
        )

    def print_comparison_table(self, results: List[BacktestResult]):
        """打印回测结果对比表"""
        if not results:
            return

        print("\n" + "=" * 85)
        print(f"  批量回测对比表  ({len(results)} 个配置)")
        print("=" * 85)
        print(f"{'标的':<12} {'策略':<15} {'收益率':>8} {'夏普':>6} {'最大回撤':>8} {'胜率':>6} {'交易数':>6}")
        print("-" * 85)

        for r in sorted(results, key=lambda x: x.sharpe_ratio, reverse=True):
            strategy = r.strategy_name
            print(
                f"{r.symbol:<12} {strategy:<15} "
                f"{r.total_return_pct:>+7.2f}% "
                f"{r.sharpe_ratio:>6.2f} "
                f"{r.max_drawdown_pct:>7.2f}% "
                f"{r.win_rate_pct:>5.1f}% "
                f"{r.total_trades:>6d}"
            )

        print("=" * 85)

    # ======================== 私有方法 ========================

    def _run_single(self, cfg: BacktestConfig) -> BacktestResult:
        """执行单次回测"""
        from history_cache import get_ohlcv as cache_get_ohlcv, init_cache_db

        init_cache_db()
        candles = cache_get_ohlcv(cfg.symbol, cfg.timeframe, limit=5000)

        # 缓存不足则跳过（不在线补数据，避免污染）
        if len(candles) < 100:
            raise ValueError(f"数据不足（{len(candles)} 条）")

        strategy = cfg.make_strategy()
        engine = BacktestEngine(strategy, initial_capital=cfg.initial_capital)
        engine.candles = candles
        engine.compute_signals()
        result = engine.run()
        return result

    def _build_default_configs(self) -> List[BacktestConfig]:
        """从优化参数构建回测配置"""
        configs = []
        for symbol in self.symbols:
            opt = self.get_optimized_config(symbol)
            if opt:
                configs.append(opt)
            else:
                # 默认配置
                configs.append(BacktestConfig(symbol=symbol))
        return configs

    def _load_optimized_params(self) -> Dict:
        if os.path.exists(PARAM_CONFIG_PATH):
            try:
                with open(PARAM_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info(f"加载最优参数配置: {PARAM_CONFIG_PATH}")
                    return {p["symbol"]: p for p in data.get("optimized_params", [])}
            except Exception as e:
                logger.warning(f"加载最优参数失败: {e}")
        return {}

    def _save_optimized_params(self):
        data = {
            "optimized_params": list(self.optimized_params.values()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(PARAM_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"最优参数已保存: {PARAM_CONFIG_PATH}")

    def _print_result_summary(self, cfg: BacktestConfig, result: BacktestResult):
        print(
            f"[{cfg.symbol}] {cfg.strategy}  "
            f"收益:{result.total_return_pct:+.2f}%  "
            f"夏普:{result.sharpe_ratio:.2f}  "
            f"回撤:{result.max_drawdown_pct:.2f}%  "
            f"胜率:{result.win_rate_pct:.1f}%  "
            f"交易:{result.total_trades}次"
        )

    def _print_grid_search_top10(self, top_results: List[GridSearchResult]):
        print("\n" + "=" * 70)
        print(f"  Grid Search Top 10  ({top_results[0].config.symbol})")
        print("=" * 70)
        print(f"{'排名':<4} {'RSI_P':>6} {'OS':>5} {'OB':>5} {'SL':>6} {'TP':>6} "
              f"{'Score':>7} {'收益率':>8} {'夏普':>6} {'回撤':>7} {'胜率':>6}")
        print("-" * 70)
        for i, gs in enumerate(top_results):
            c = gs.config
            r = gs.result
            print(
                f"{i+1:<4} {c.rsi_period:>6} {c.oversold:>5.1f} {c.overbought:>5.1f} "
                f"{c.stop_loss:>6.3f} {c.take_profit:>6.3f} "
                f"{gs.score:>7.3f} {r.total_return_pct:>+7.2f}% {r.sharpe_ratio:>6.2f} "
                f"{r.max_drawdown_pct:>6.2f}% {r.win_rate_pct:>5.1f}%"
            )
        print("=" * 70)

    @staticmethod
    def _serialize_config(cfg: BacktestConfig) -> Dict:
        return {
            "symbol": cfg.symbol,
            "strategy": cfg.strategy,
            "rsi_period": cfg.rsi_period,
            "oversold": cfg.oversold,
            "overbought": cfg.overbought,
            "stop_loss": cfg.stop_loss,
            "take_profit": cfg.take_profit,
        }


# ============================================================
# 主入口
# ============================================================

def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description="批量回测引擎 + Grid Search 参数优化")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="指定标的，如 --symbols BTC ETH SOL")
    parser.add_argument("--strategy", default="RSIStrategy",
                        choices=["RSIStrategy", "SMAcrossStrategy"],
                        help="回测策略（默认 RSIStrategy）")
    parser.add_argument("--timeframe", default="4h",
                        help="K线周期（默认 4h）")
    parser.add_argument("--grid-search", action="store_true",
                        help="启用 Grid Search 模式（遍历所有参数组合）")
    parser.add_argument("--grid-symbol", default="ETH/USDT",
                        help="Grid Search 标的（默认 ETH/USDT）")
    parser.add_argument("--incremental", action="store_true",
                        help="增量回测模式（仅回测有变化的标的）")
    parser.add_argument("--parallel", action="store_true", default=True,
                        help="并行回测（默认开启）")
    parser.add_argument("--workers", type=int, default=4,
                        help="并行工作线程数（默认 4）")
    parser.add_argument("--load-default", action="store_true",
                        help="加载预设最优配置并回测")
    return parser.parse_args()


def main():
    args = parse_args()

    symbols = args.symbols or ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    batch = BatchBacktester(symbols=symbols)

    if args.grid_search:
        # Grid Search 模式
        results = batch.grid_search(symbol=args.grid_symbol, strategy=args.strategy, max_workers=args.workers)
        best = results[0]
        batch.update_optimized_params(best.config.symbol, best.config, best.result)
        print(f"\n✅ 最优参数已更新到 config_optimized.json")
        return

    if args.load_default:
        # 加载最优配置并回测
        results = batch.load_or_run_default_configs()
        batch.print_comparison_table(results)
        return

    # 默认全量回测（预设配置）
    configs = [
        BacktestConfig(symbol=s, strategy=args.strategy, timeframe=args.timeframe)
        for s in symbols
    ]
    results = batch.run_all(configs, parallel=args.parallel)
    batch.print_comparison_table(results)

    # 自动保存最优
    for result in results:
        cfg = BacktestConfig(symbol=result.symbol, strategy=result.strategy_name)
        batch.update_optimized_params(result.symbol, cfg, result)


if __name__ == "__main__":
    main()