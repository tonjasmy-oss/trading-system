"""
Swarm 预设批量回测 — 29 种预设 × 5 标的

对未跑过回测的 Swarm 预设，在主要标的上执行完整回测。
使用 BacktestEngine + SwarmVoteStrategy，并行执行。
"""
import os, sys, json, time, logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

# ⚠️ 本地路径必须保持在 Vibe-Trading 之前
_LOCAL = os.path.dirname(os.path.abspath(__file__))
if _LOCAL not in sys.path:
    sys.path.insert(0, _LOCAL)

# 先导入 backtest，避免被 swarm_bridge 的 sys.path 干扰
from backtest import BacktestEngine, BacktestResult
from history_cache import init_cache_db
from strategies import StrategyConfig
from swarm_bridge import SwarmVoteStrategy, list_swarm_presets

# swarm_bridge 的 import 会把 VT root 插入 sys.path[0]，
# 重新把本地路径放回最前面
while sys.path[0] != _LOCAL:
    try:
        sys.path.remove(_LOCAL)
    except ValueError:
        pass
    sys.path.insert(0, _LOCAL)
    # 确认没有死循环
    if sys.path[0] == _LOCAL:
        break
    break

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("swarm_bt")

RESULT_DIR = os.path.join(_LOCAL, "backtest_results", "swarm")
INITIAL_CAPITAL = 10_000.0

# 回测标的和时间框架
BACKTEST_TASKS = [
    ("SOL/USDT",  "2h"),
    ("SUI/USDT",  "2h"),
    ("XAUT/USDT", "4h"),
    ("BTC/USDT",  "4h"),
    ("ETH/USDT",  "4h"),
]


@dataclass
class SwarmBTResult:
    preset: str
    symbol: str
    timeframe: str
    result: Optional[BacktestResult] = None
    error: Optional[str] = None
    elapsed_s: float = 0.0


def run_one(preset_name: str, symbol: str, timeframe: str) -> SwarmBTResult:
    t0 = time.monotonic()
    r = SwarmBTResult(preset=preset_name, symbol=symbol, timeframe=timeframe)
    try:
        config = StrategyConfig(
            symbol=symbol,
            timeframe=timeframe,
            capital_pct=1.0,
            stop_loss=0.03,
            take_profit=0.05,
            trade_direction="long",
        )
        strategy = SwarmVoteStrategy(config=config, preset_name=preset_name)
        engine = BacktestEngine(strategy, initial_capital=INITIAL_CAPITAL)
        if not engine.load_data():
            r.error = "数据加载失败"
            return r
        engine.compute_signals()
        bt_result = engine.run()
        r.result = bt_result
    except Exception as e:
        r.error = str(e)[:200]
    r.elapsed_s = time.monotonic() - t0
    return r


def main():
    os.makedirs(RESULT_DIR, exist_ok=True)
    init_cache_db()

    presets_raw = list_swarm_presets()
    preset_names = [p["name"] for p in presets_raw]
    logger.info(f"Swarm 预设: {len(preset_names)} 个")
    logger.info(f"标的: {[f'{s} {tf}' for s, tf in BACKTEST_TASKS]}")
    logger.info(f"总任务: {len(preset_names) * len(BACKTEST_TASKS)} 个")

    all_results: List[SwarmBTResult] = []
    tasks = [(pn, s, tf) for pn in preset_names for s, tf in BACKTEST_TASKS]

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(run_one, pn, s, tf): (pn, s, tf) for pn, s, tf in tasks}
        for i, future in enumerate(as_completed(futures), 1):
            pn, s, tf = futures[future]
            try:
                r = future.result()
                all_results.append(r)
            except Exception as e:
                all_results.append(SwarmBTResult(preset=pn, symbol=s, timeframe=tf, error=str(e)))

            if r.result:
                logger.info(
                    f"[{i}/{len(tasks)}] {pn:>35s} | {s:<10s} {tf} "
                    f"ret={r.result.total_return_pct:+7.2f}% "
                    f"sharpe={r.result.sharpe_ratio:+6.2f} "
                    f"dd={r.result.max_drawdown_pct:+6.2f}% "
                    f"trades={r.result.total_trades:>3d} "
                    f"wr={r.result.win_rate_pct:>5.1f}% "
                    f"({r.elapsed_s:.1f}s)"
                )
            else:
                logger.warning(f"[{i}/{len(tasks)}] {pn:>35s} | {s} {tf}  ERROR: {r.error}")

    # 保存完整结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(RESULT_DIR, f"swarm_full_{timestamp}.json")
    serializable = []
    for r in all_results:
        entry = {
            "preset": r.preset, "symbol": r.symbol, "timeframe": r.timeframe,
            "error": r.error, "elapsed_s": round(r.elapsed_s, 1),
        }
        if r.result:
            entry.update({
                "total_return_pct": round(r.result.total_return_pct, 2),
                "sharpe_ratio": round(r.result.sharpe_ratio, 2),
                "max_drawdown_pct": round(r.result.max_drawdown_pct, 2),
                "total_trades": r.result.total_trades,
                "winning_trades": r.result.winning_trades,
                "losing_trades": r.result.losing_trades,
                "win_rate_pct": round(r.result.win_rate_pct, 1),
                "start_date": r.result.start_date,
                "end_date": r.result.end_date,
            })
        serializable.append(entry)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    logger.info(f"完整结果: {json_path}  ({len(serializable)} 条)")

    # 摘要
    print("\n" + "=" * 100)
    print("📊 Swarm 预设回测摘要 (按预设 × 标的)")
    print("=" * 100)

    by_preset: Dict[str, List[SwarmBTResult]] = {}
    for r in all_results:
        by_preset.setdefault(r.preset, []).append(r)

    def preset_score(results: List[SwarmBTResult]) -> float:
        valid = [rr for rr in results if rr.result and rr.result.total_trades >= 3]
        if not valid:
            return -999
        return max(
            rr.result.sharpe_ratio * 0.4 + rr.result.win_rate_pct * 0.2
            + rr.result.total_return_pct * 0.1 - rr.result.max_drawdown_pct * 0.3
            for rr in valid
        )

    sorted_presets = sorted(by_preset.keys(), key=lambda pn: preset_score(by_preset[pn]), reverse=True)

    for pn in sorted_presets:
        results = by_preset[pn]
        valid = [r for r in results if r.result and r.result.total_trades >= 3]
        if not valid:
            print(f"\n{pn}: 无有效结果（所有标的不满足最少 3 笔交易）")
            for r in results:
                if r.result:
                    print(f"  {r.symbol} {r.timeframe}: trades={r.result.total_trades} ret={r.result.total_return_pct:+.1f}%")
                else:
                    print(f"  {r.symbol} {r.timeframe}: {r.error}")
            continue

        best = max(valid, key=lambda rr: rr.result.sharpe_ratio)
        print(f"\n{'─'*90}")
        print(f"  🏆 {pn}")
        print(f"     最佳: {best.symbol} {best.timeframe}  "
              f"ret={best.result.total_return_pct:+7.2f}%  "
              f"sharpe={best.result.sharpe_ratio:+6.2f}  "
              f"dd={best.result.max_drawdown_pct:+6.2f}%  "
              f"trades={best.result.total_trades:>3d}  "
              f"wr={best.result.win_rate_pct:>5.1f}%")

        for r in sorted(valid, key=lambda rr: rr.result.sharpe_ratio, reverse=True):
            marker = "←" if r is best else " "
            print(f"     {marker} {r.symbol:<10s} {r.timeframe}  "
                  f"ret={r.result.total_return_pct:+7.2f}%  "
                  f"sharpe={r.result.sharpe_ratio:+6.2f}  "
                  f"dd={r.result.max_drawdown_pct:+6.2f}%  "
                  f"trades={r.result.total_trades:>3d}")

    print(f"\n{'='*100}")
    print(f"结果保存: {json_path}")


if __name__ == "__main__":
    main()
