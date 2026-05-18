"""
多策略投票回测优化 — Vote Backtest Engine
=========================================

对 MultiStrategyVote 进行 Grid Search：
  1. 策略子集选择（3~6 策略组合）
  2. 权重分配寻优（step=0.1）
  3. 投票阈值寻优（0.1 ~ 0.5）
  4. SL/TP 细调（在最优权重上）

输出：
  - 最优投票配置（权重 + 阈值 + SL/TP）
  - vs 单策略最优 vs 默认权重(40/30/30) 三方对比
  - 写入 config_optimized.json

使用方式：
  python vote_backtest.py ETH/USDT 4h              # 单标的完整寻优
  python vote_backtest.py ETH/USDT 4h --quick       # 快速模式（减少组合数）
  python vote_backtest.py --all                      # 全部标的
  python vote_backtest.py ETH/USDT 4h --compare-only # 仅对比已有配置
"""

import os
import sys
import json
import time
import logging
import itertools
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Sequence
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ATRSTOP_EMA_PERIOD, ATRSTOP_ATR_PERIOD, ATRSTOP_ATR_MULTIPLIER
from strategies import (
    Strategy, StrategyConfig, Signal,
    RSIStrategy, SMAcrossStrategy, MACDStrategy,
    BollingerBandsStrategy, KDJStrategy, ATRStopStrategy,
    STRATEGY_REGISTRY,
)
from multi_strategy_vote import MultiStrategyVote
from backtest import BacktestEngine, BacktestResult, generate_report, print_summary
from history_cache import get_ohlcv as cache_get_ohlcv, init_cache_db

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
VOTE_DIR = os.path.join(BACKTEST_DIR, "vote_search")
PARAM_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config_optimized.json")
INITIAL_CAPITAL = 10000.0

# 单策略 Grid Search 最优参数（从 config_optimized.json 或硬编码回退）
OPTIMAL_SINGLE: Dict[str, dict] = {
    "BTC/USDT": dict(rsi_period=10, oversold=18.0, overbought=65.0, stop_loss=0.040, take_profit=0.080),
    "ETH/USDT": dict(rsi_period=14, oversold=28.0, overbought=65.0, stop_loss=0.020, take_profit=0.040),
    "SOL/USDT":  dict(rsi_period=10, oversold=20.0, overbought=65.0, stop_loss=0.015, take_profit=0.040),
    "SUI/USDT":  dict(rsi_period=10, oversold=25.0, overbought=65.0, stop_loss=0.012, take_profit=0.025),
}

# 策略简称映射
STRATEGY_ALIASES = {
    "RSI": RSIStrategy,
    "SMA": SMAcrossStrategy,
    "MACD": MACDStrategy,
    "BOLL": BollingerBandsStrategy,
    "KDJ": KDJStrategy,
    "ATR": ATRStopStrategy,
}

# 默认权重（当前生产配置）
DEFAULT_WEIGHTS = {"RSI": 0.4, "MACD": 0.3, "BOLL": 0.3}
DEFAULT_THRESHOLD = 0.3

# 网格搜索参数
GRID_THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
GRID_SL = [0.015, 0.02, 0.025, 0.03, 0.04]
GRID_TP = [0.03, 0.04, 0.05, 0.06, 0.08]

# 快速模式缩减
GRID_THRESHOLDS_QUICK = [0.20, 0.30, 0.40]
GRID_SL_QUICK = [0.02, 0.03]
GRID_TP_QUICK = [0.04, 0.06]


@dataclass
class VoteConfig:
    """投票策略配置"""
    symbol: str
    timeframe: str = "4h"
    strategies: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    threshold: float = DEFAULT_THRESHOLD
    stop_loss: float = 0.02
    take_profit: float = 0.04
    capital_pct: float = 1.0
    trade_direction: str = "long"

    @property
    def name(self) -> str:
        parts = [f"{k}{v:.0%}" for k, v in sorted(self.strategies.items())]
        return "+".join(parts)

    @property
    def config_id(self) -> str:
        return f"VOTE_{self.symbol.replace('/', '_')}_{self.name}_th{self.threshold:.2f}_sl{self.stop_loss}_tp{self.take_profit}"


@dataclass
class VoteResult:
    """投票回测结果"""
    config: VoteConfig
    result: BacktestResult
    score: float  # 综合评分

    @staticmethod
    def calc_score(r: BacktestResult, min_trades: int = 5) -> float:
        """综合评分 = Sharpe*0.4 + WR*0.2 + Return*0.1 - DD*0.3 - 低交易惩罚"""
        base = (
            r.sharpe_ratio * 0.4
            + r.win_rate_pct * 0.2
            + r.total_return_pct * 0.1
            - r.max_drawdown_pct * 0.3
        )
        # 交易数过少惩罚：每少 1 笔交易扣 3 分（防止统计假象）
        if r.total_trades < min_trades:
            base -= (min_trades - r.total_trades) * 3.0
        return base


# ============================================================
# 权重生成工具
# ============================================================

def generate_weight_distributions(
    strategy_names: List[str],
    step: float = 0.1,
) -> List[Dict[str, float]]:
    """
    生成所有权重分配组合，使权重和 = 1.0。

    使用整数分拆 + 组合数生成，避免浮点精度问题。
    例：3 策略 step=0.1 → 将 10 拆为 3 份 → C(9,2) = 36 种。
    """
    n = len(strategy_names)
    total = int(1.0 / step)  # 例如 step=0.1 → total=10

    # only positive weights → 每个至少 1 unit
    if total < n:
        return []

    # Compositions: total units into n parts, each >= 1
    # Equivalent to C(total-1, n-1)
    results = []
    for combo in itertools.combinations(range(1, total), n - 1):
        parts = []
        prev = 0
        for c in combo:
            parts.append(c - prev)
            prev = c
        parts.append(total - prev)
        dist = {strategy_names[i]: parts[i] * step for i in range(n)}
        results.append(dist)
    return results


def generate_strategy_subsets() -> List[List[str]]:
    """生成所有有意义的策略子集（3~6 策略）"""
    all_strategies = list(STRATEGY_ALIASES.keys())
    subsets = []
    for size in range(3, len(all_strategies) + 1):
        for combo in itertools.combinations(all_strategies, size):
            # 必须包含 RSI（核心策略）
            if "RSI" not in combo:
                continue
            subsets.append(list(combo))
    return subsets


# ============================================================
# 投票回测器
# ============================================================

class VoteBacktester:
    """多策略投票 Grid Search 引擎"""

    def __init__(self, symbol: str, timeframe: str = "4h", quick: bool = False):
        self.symbol = symbol
        self.timeframe = timeframe
        self.quick = quick

        os.makedirs(VOTE_DIR, exist_ok=True)

        # 加载 OHLCV 数据（只加载一次，所有组合共享）
        self.candles = self._load_candles()
        if len(self.candles) < 100:
            raise RuntimeError(f"数据不足（{len(self.candles)} 条），无法回测")

        # 使用标的专属最优参数作为子策略基准
        self.opt = OPTIMAL_SINGLE.get(symbol, OPTIMAL_SINGLE["ETH/USDT"])

        # 阈值和 SL/TP 网格
        if quick:
            self.thresholds = GRID_THRESHOLDS_QUICK
            self.sl_grid = GRID_SL_QUICK
            self.tp_grid = GRID_TP_QUICK
        else:
            self.thresholds = GRID_THRESHOLDS
            self.sl_grid = GRID_SL
            self.tp_grid = GRID_TP

        logger.info(f"VoteBacktester 初始化: {symbol} {timeframe} quick={quick}")
        logger.info(f"  数据: {len(self.candles)} 条 K线")
        logger.info(f"  子策略基准: RSI({self.opt['rsi_period']},{self.opt['oversold']},{self.opt['overbought']}) "
                     f"SL={self.opt['stop_loss']:.1%} TP={self.opt['take_profit']:.1%}")

    # ── 数据加载 ──────────────────────────────────────────

    def _load_candles(self) -> List[Dict]:
        init_cache_db()
        candles = cache_get_ohlcv(self.symbol, self.timeframe, limit=5000)
        logger.info(f"加载数据: {len(candles)} 条 {self.symbol} {self.timeframe}")
        return candles

    # ── 策略构建 ──────────────────────────────────────────

    def _build_sub_strategy(self, name: str, sl: float, tp: float) -> Strategy:
        """为投票器的子策略构建实例"""
        config = StrategyConfig(
            symbol=self.symbol,
            timeframe=self.timeframe,
            capital_pct=1.0,
            stop_loss=sl,
            take_profit=tp,
        )

        if name == "RSI":
            return RSIStrategy(
                config=config,
                rsi_period=self.opt["rsi_period"],
                oversold=self.opt["oversold"],
                overbought=self.opt["overbought"],
            )
        elif name == "SMA":
            return SMAcrossStrategy(config=config, fast_period=10, slow_period=30)
        elif name == "MACD":
            return MACDStrategy(config=config)
        elif name == "BOLL":
            return BollingerBandsStrategy(config=config, period=20, std_dev=2.0)
        elif name == "KDJ":
            return KDJStrategy(config=config)
        elif name == "ATR":
            return ATRStopStrategy(config=config, ema_period=ATRSTOP_EMA_PERIOD, atr_period=ATRSTOP_ATR_PERIOD, atr_multiplier=ATRSTOP_ATR_MULTIPLIER)
        raise ValueError(f"未知策略: {name}")

    # ── 单次回测 ──────────────────────────────────────────

    def _run_single_vote(self, cfg: VoteConfig) -> BacktestResult:
        """执行单次投票回测"""
        strategies: List[Tuple[Strategy, float]] = []
        for name, weight in cfg.strategies.items():
            s = self._build_sub_strategy(name, cfg.stop_loss, cfg.take_profit)
            strategies.append((s, weight))

        vote = MultiStrategyVote(
            strategies=strategies,
            threshold=cfg.threshold,
            name=cfg.name,
        )

        engine = BacktestEngine(vote, initial_capital=INITIAL_CAPITAL,
                               trade_direction=cfg.trade_direction)
        engine.candles = self.candles
        engine.compute_signals()
        return engine.run()

    # ── Phase 1: 权重 + 阈值寻优（固定 SL/TP） ──────────

    def search_weights(self, max_workers: int = 6) -> List[VoteResult]:
        """
        Phase 1: 遍历策略子集 × 权重分布 × 阈值。
        使用标的最优 SL/TP 固定。
        """
        base_sl = self.opt["stop_loss"]
        base_tp = self.opt["take_profit"]

        subsets = generate_strategy_subsets()
        total_combos = 0
        configs: List[VoteConfig] = []

        for subset in subsets:
            weight_dists = generate_weight_distributions(subset, step=0.1)
            for wdist in weight_dists:
                for th in self.thresholds:
                    configs.append(VoteConfig(
                        symbol=self.symbol,
                        timeframe=self.timeframe,
                        strategies=wdist,
                        threshold=th,
                        stop_loss=base_sl,
                        take_profit=base_tp,
                    ))
                    total_combos += 1

        logger.info(f"Phase 1 权重搜索: {len(subsets)} 策略子集 × 权重分布 × {len(self.thresholds)} 阈值 "
                     f"= {total_combos} 组合")

        results: List[VoteResult] = []
        completed = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._run_single_vote, cfg): cfg for cfg in configs}

            for future in as_completed(futures):
                cfg = futures[future]
                completed += 1
                try:
                    result = future.result(timeout=120)
                    score = VoteResult.calc_score(result)
                    results.append(VoteResult(config=cfg, result=result, score=score))

                    if completed % 100 == 0:
                        best = max(results, key=lambda x: x.score)
                        logger.info(f"Phase1 进度 {completed}/{total_combos}  "
                                     f"Top1 Score={best.score:.2f} "
                                     f"{best.config.name} th={best.config.threshold:.2f} "
                                     f"Ret={best.result.total_return_pct:+.1f}%")
                except Exception as e:
                    logger.error(f"[{cfg.config_id}] 失败: {e}")

        results.sort(key=lambda x: x.score, reverse=True)
        logger.info(f"Phase 1 完成，共 {len(results)} 个有效结果")
        return results

    # ── Phase 2: SL/TP 细调（在 Top 权重上） ──────────────

    def search_sltp(self, top_weight_configs: List[VoteConfig], max_workers: int = 6) -> List[VoteResult]:
        """
        Phase 2: 在 Phase 1 的 Top N 权重配置上，细调 SL/TP。
        """
        configs = []
        for base in top_weight_configs:
            for sl in self.sl_grid:
                for tp in self.tp_grid:
                    if sl >= tp:
                        continue
                    configs.append(VoteConfig(
                        symbol=self.symbol,
                        timeframe=self.timeframe,
                        strategies=base.strategies,
                        threshold=base.threshold,
                        stop_loss=sl,
                        take_profit=tp,
                    ))

        total = len(configs)
        logger.info(f"Phase 2 SL/TP 细调: {len(top_weight_configs)} 个权重配置 × SL×TP = {total} 组合")

        results: List[VoteResult] = []
        completed = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._run_single_vote, cfg): cfg for cfg in configs}

            for future in as_completed(futures):
                cfg = futures[future]
                completed += 1
                try:
                    result = future.result(timeout=120)
                    score = VoteResult.calc_score(result)
                    results.append(VoteResult(config=cfg, result=result, score=score))

                    if completed % 50 == 0:
                        best = max(results, key=lambda x: x.score)
                        logger.info(f"Phase2 进度 {completed}/{total}  "
                                     f"Top1 Score={best.score:.2f} "
                                     f"SL={best.config.stop_loss:.1%} TP={best.config.take_profit:.1%} "
                                     f"Ret={best.result.total_return_pct:+.1f}%")
                except Exception as e:
                    logger.error(f"[{cfg.config_id}] 失败: {e}")

        results.sort(key=lambda x: x.score, reverse=True)
        logger.info(f"Phase 2 完成，共 {len(results)} 个有效结果")
        return results

    # ── 完整寻优流程 ──────────────────────────────────────

    def full_search(self, top_n: int = 10) -> List[VoteResult]:
        """完整两阶段寻优"""
        start = time.time()

        # Phase 1
        phase1 = self.search_weights()
        top_configs = [r.config for r in phase1[:top_n]]
        # 去重（按策略名+阈值去重）
        seen = set()
        unique_configs = []
        for cfg in top_configs:
            key = (tuple(sorted(cfg.strategies.items())), cfg.threshold)
            if key not in seen:
                seen.add(key)
                unique_configs.append(cfg)
        logger.info(f"Phase 1 Top{top_n} 去重后: {len(unique_configs)} 个唯一配置")

        # Phase 2
        phase2 = self.search_sltp(unique_configs[:top_n])

        elapsed = time.time() - start
        logger.info(f"完整寻优耗时: {elapsed:.1f}s")

        return phase2


# ============================================================
# 对比报告
# ============================================================

def run_baseline(symbol: str, timeframe: str) -> List[VoteResult]:
    """
    运行基线测试：
      - 默认权重 (RSI 40% + MACD 30% + BOLL 30%, th=0.3)
      - 各单策略（使用最优参数）
    """
    opt = OPTIMAL_SINGLE.get(symbol, OPTIMAL_SINGLE["ETH/USDT"])
    candles = cache_get_ohlcv(symbol, timeframe, limit=5000)

    results = []

    # 1. 默认投票权重
    try:
        tester = VoteBacktester(symbol, timeframe)
        cfg = VoteConfig(
            symbol=symbol, timeframe=timeframe,
            strategies=dict(DEFAULT_WEIGHTS),
            threshold=DEFAULT_THRESHOLD,
            stop_loss=opt["stop_loss"], take_profit=opt["take_profit"],
        )
        r = tester._run_single_vote(cfg)
        results.append(VoteResult(config=cfg, result=r, score=VoteResult.calc_score(r)))
        logger.info(f"基线-默认投票: Ret={r.total_return_pct:+.2f}% Sharpe={r.sharpe_ratio:.2f} "
                     f"DD={r.max_drawdown_pct:.2f}% WR={r.win_rate_pct:.1f}% Trades={r.total_trades}")
    except Exception as e:
        logger.error(f"默认投票基线失败: {e}")

    # 2. 各单策略
    for name in ["RSI", "SMA", "MACD", "BOLL", "KDJ", "ATR"]:
        try:
            tester = VoteBacktester(symbol, timeframe)
            cfg = VoteConfig(
                symbol=symbol, timeframe=timeframe,
                strategies={name: 1.0},
                threshold=0.0,  # 单策略无阈值
                stop_loss=opt["stop_loss"], take_profit=opt["take_profit"],
            )
            r = tester._run_single_vote(cfg)
            results.append(VoteResult(config=cfg, result=r, score=VoteResult.calc_score(r)))
            logger.info(f"基线-{name}: Ret={r.total_return_pct:+.2f}% Sharpe={r.sharpe_ratio:.2f} "
                         f"DD={r.max_drawdown_pct:.2f}% WR={r.win_rate_pct:.1f}% Trades={r.total_trades}")
        except Exception as e:
            logger.error(f"基线 {name} 失败: {e}")

    results.sort(key=lambda x: x.score, reverse=True)
    return results


def print_compare_report(
    best_vote: Optional[VoteResult],
    baselines: List[VoteResult],
    symbol: str,
):
    """打印对比报告"""
    print("\n" + "=" * 90)
    print(f"  多策略投票回测优化 — 对比报告  {symbol}")
    print("=" * 90)

    # 表头
    print(f"{'类型':<20} {'配置':<30} {'收益率':>8} {'夏普':>6} {'回撤':>8} {'胜率':>6} {'交易':>5} {'Score':>7}")
    print("-" * 90)

    # 最优投票
    if best_vote:
        r = best_vote.result
        cfg = best_vote.config
        strategy_str = "+".join(f"{k}{v:.0%}" for k, v in sorted(cfg.strategies.items()))
        print(f"{'★最优投票':<20} {strategy_str:<30} "
              f"{r.total_return_pct:>+7.2f}% {r.sharpe_ratio:>6.2f} "
              f"{r.max_drawdown_pct:>7.2f}% {r.win_rate_pct:>5.1f}% {r.total_trades:>5d} "
              f"{best_vote.score:>7.2f}")
        print(f"  {'':>20} th={cfg.threshold:.2f} SL={cfg.stop_loss:.1%} TP={cfg.take_profit:.1%}")

    # 基线
    for vr in baselines:
        r = vr.result
        cfg = vr.config
        if len(cfg.strategies) == 1:
            name = list(cfg.strategies.keys())[0]
            label = f"单策略-{name}"
        else:
            name = "+".join(f"{k}{v:.0%}" for k, v in sorted(cfg.strategies.items()))
            label = "默认投票" if cfg.threshold == DEFAULT_THRESHOLD else name

        strategy_str = name
        print(f"{label:<20} {strategy_str:<30} "
              f"{r.total_return_pct:>+7.2f}% {r.sharpe_ratio:>6.2f} "
              f"{r.max_drawdown_pct:>7.2f}% {r.win_rate_pct:>5.1f}% {r.total_trades:>5d} "
              f"{vr.score:>7.2f}")

    print("=" * 90)

    # 结论
    all_results = ([best_vote] if best_vote else []) + baselines
    if all_results:
        best = max(all_results, key=lambda x: x.score)
        if best_vote and best.config.config_id == best_vote.config.config_id:
            improvement = ""
            # 找最好的单策略
            singles = [b for b in baselines if len(b.config.strategies) == 1]
            if singles:
                best_single = max(singles, key=lambda x: x.score)
                imp = best.score - best_single.score
                improvement = f" vs 最优单策略 {list(best_single.config.strategies.keys())[0]} (Score={best_single.score:.2f}, Δ={imp:+.2f})"

            # 找默认投票
            defaults = [b for b in baselines if b.config.threshold == DEFAULT_THRESHOLD and len(b.config.strategies) > 1]
            if defaults:
                def_score = defaults[0].score
                improvement += f" vs 默认投票 (Score={def_score:.2f}, Δ={best.score - def_score:+.2f})"

            print(f"\n✅ 投票策略优于所有基线 {improvement}")
        else:
            best_label = list(best.config.strategies.keys())[0] if len(best.config.strategies) == 1 else "投票"
            print(f"\n⚠️ 最优为 {best_label} (Score={best.score:.2f})，投票未带来提升")


# ============================================================
# 保存结果
# ============================================================

def save_vote_results(
    best: VoteResult,
    all_phase2: List[VoteResult],
    baselines: List[VoteResult],
    symbol: str,
):
    """保存最优投票参数到 config_optimized.json"""
    cfg = best.config

    # 更新 config_optimized.json
    existing = {"optimized_params": [], "vote_params": {}}
    if os.path.exists(PARAM_CONFIG_PATH):
        with open(PARAM_CONFIG_PATH, "r") as f:
            try:
                existing = json.load(f)
            except Exception:
                pass

    existing["vote_params"] = {
        "symbol": symbol,
        "strategies": {k: round(v, 2) for k, v in cfg.strategies.items()},
        "threshold": cfg.threshold,
        "stop_loss": cfg.stop_loss,
        "take_profit": cfg.take_profit,
        "total_return_pct": round(best.result.total_return_pct, 2),
        "sharpe_ratio": round(best.result.sharpe_ratio, 2),
        "max_drawdown_pct": round(best.result.max_drawdown_pct, 2),
        "win_rate_pct": round(best.result.win_rate_pct, 2),
        "score": round(best.score, 2),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    with open(PARAM_CONFIG_PATH, "w") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    logger.info(f"最优投票参数已保存: {PARAM_CONFIG_PATH}")

    # 保存 Phase 2 全部结果
    report_path = os.path.join(
        VOTE_DIR,
        f"vote_search_{symbol.replace('/', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    report = {
        "symbol": symbol,
        "best": {
            "strategies": {k: round(v, 2) for k, v in cfg.strategies.items()},
            "threshold": cfg.threshold,
            "stop_loss": cfg.stop_loss,
            "take_profit": cfg.take_profit,
            "total_return_pct": round(best.result.total_return_pct, 2),
            "sharpe_ratio": round(best.result.sharpe_ratio, 2),
            "max_drawdown_pct": round(best.result.max_drawdown_pct, 2),
            "win_rate_pct": round(best.result.win_rate_pct, 2),
            "total_trades": best.result.total_trades,
            "score": round(best.score, 2),
        },
        "top20": [
            {
                "rank": i + 1,
                "strategies": {k: round(v, 2) for k, v in vr.config.strategies.items()},
                "threshold": vr.config.threshold,
                "stop_loss": vr.config.stop_loss,
                "take_profit": vr.config.take_profit,
                "total_return_pct": round(vr.result.total_return_pct, 2),
                "sharpe_ratio": round(vr.result.sharpe_ratio, 2),
                "max_drawdown_pct": round(vr.result.max_drawdown_pct, 2),
                "win_rate_pct": round(vr.result.win_rate_pct, 2),
                "total_trades": vr.result.total_trades,
                "score": round(vr.score, 2),
            }
            for i, vr in enumerate(all_phase2[:20])
        ],
        "baselines": [
            {
                "label": list(vr.config.strategies.keys())[0] if len(vr.config.strategies) == 1 else "默认投票",
                "strategies": {k: round(v, 2) for k, v in vr.config.strategies.items()},
                "threshold": vr.config.threshold,
                "total_return_pct": round(vr.result.total_return_pct, 2),
                "sharpe_ratio": round(vr.result.sharpe_ratio, 2),
                "max_drawdown_pct": round(vr.result.max_drawdown_pct, 2),
                "win_rate_pct": round(vr.result.win_rate_pct, 2),
                "total_trades": vr.result.total_trades,
                "score": round(vr.score, 2),
            }
            for vr in baselines
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    with open(report_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"完整报告已保存: {report_path}")


# ============================================================
# CLI
# ============================================================

def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="多策略投票回测优化")
    p.add_argument("symbol", nargs="?", default="ETH/USDT", help="交易对")
    p.add_argument("timeframe", nargs="?", default="4h", help="K线周期")
    p.add_argument("--quick", action="store_true", help="快速模式（减少参数组合）")
    p.add_argument("--all", action="store_true", help="对全部标的执行")
    p.add_argument("--compare-only", action="store_true", help="仅对比基线，不搜索")
    p.add_argument("--top-n", type=int, default=10, help="Phase 1 保留的 Top N 权重配置（默认 10）")
    p.add_argument("--workers", type=int, default=6, help="并行线程数（默认 6）")
    return p.parse_args()


def main():
    args = parse_args()

    symbols = [args.symbol]
    if args.all:
        symbols = list(OPTIMAL_SINGLE.keys())

    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"  多策略投票优化: {symbol} {args.timeframe}")
        print(f"{'='*60}")

        # 基线
        baselines = run_baseline(symbol, args.timeframe)

        if args.compare_only:
            print_compare_report(None, baselines, symbol)
            continue

        # 完整寻优
        try:
            tester = VoteBacktester(symbol, args.timeframe, quick=args.quick)
            phase2_results = tester.full_search(top_n=args.top_n)
        except RuntimeError as e:
            logger.error(f"回测器初始化失败: {e}")
            continue

        if not phase2_results:
            logger.error("未找到有效结果")
            continue

        best = phase2_results[0]

        # 打印对比
        print_compare_report(best, baselines, symbol)

        # 打印 Top 5 投票配置
        print(f"\n--- Top 5 投票配置 ---")
        for i, vr in enumerate(phase2_results[:5]):
            r = vr.result
            cfg = vr.config
            strategy_str = "+".join(f"{k}{v:.0%}" for k, v in sorted(cfg.strategies.items()))
            print(f"  #{i+1} {strategy_str} th={cfg.threshold:.2f} SL={cfg.stop_loss:.1%} TP={cfg.take_profit:.1%} "
                  f"Ret={r.total_return_pct:+.2f}% SR={r.sharpe_ratio:.2f} DD={r.max_drawdown_pct:.2f}% "
                  f"WR={r.win_rate_pct:.1f}% Trades={r.total_trades} Score={vr.score:.2f}")

        # 保存
        save_vote_results(best, phase2_results, baselines, symbol)

    print("\n✅ 多策略投票优化完成")


if __name__ == "__main__":
    main()
