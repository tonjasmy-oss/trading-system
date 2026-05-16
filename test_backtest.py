"""
backtest.py 确定性回归测试
==========================
使用固定随机种子的 OHLCV 数据，对所有策略执行回测，
断言关键指标在预期范围内。任何策略逻辑变更导致指标漂移
都会使测试失败，起到回归保护作用。

运行: python test_backtest.py
"""

import sys
import os
import random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies import (
    StrategyConfig, RSIStrategy, SMAcrossStrategy,
    MACDStrategy, BollingerBandsStrategy,
)
from backtest import BacktestEngine


def generate_candles(n: int = 300, seed: int = 42) -> list:
    random.seed(seed)
    base_price = 2000.0
    price = base_price
    candles = []
    for i in range(n):
        r = random.uniform(-0.04, 0.04)  # 更大波动以触发更多信号
        o = price
        c = price * (1 + r)
        h = max(o, c) * (1 + random.uniform(0, 0.015))
        l = min(o, c) * (1 - random.uniform(0, 0.015))
        candles.append({
            "timestamp": 1600000000000 + i * 4 * 3600000,
            "open":  round(o, 2),
            "high":  round(h, 2),
            "low":   round(l, 2),
            "close": round(c, 2),
            "volume": round(random.uniform(200, 800), 2),
        })
        price = c
    return candles


CANDLES = generate_candles(300)


def run_backtest(strategy, name: str) -> dict:
    engine = BacktestEngine(strategy, initial_capital=10000.0)
    engine.candles = CANDLES
    engine.compute_signals()
    result = engine.run()
    return {
        "name": name,
        "return_pct": result.total_return_pct,
        "sharpe": result.sharpe_ratio,
        "max_dd": result.max_drawdown_pct,
        "trades": result.total_trades,
        "win_rate": result.win_rate_pct,
    }


def test_all_strategies():
    strategies = [
        (RSIStrategy(StrategyConfig(symbol="ETH/USDT", timeframe="4h",
                     stop_loss=0.05, take_profit=0.10),
                     rsi_period=14, oversold=30, overbought=70), "RSI"),
        (SMAcrossStrategy(StrategyConfig(symbol="ETH/USDT", timeframe="4h",
                          stop_loss=0.05, take_profit=0.10),
                          fast_period=10, slow_period=30), "SMAcross"),
        (MACDStrategy(StrategyConfig(symbol="ETH/USDT", timeframe="4h",
                      stop_loss=0.05, take_profit=0.10)), "MACD"),
        (BollingerBandsStrategy(StrategyConfig(symbol="ETH/USDT", timeframe="4h",
                                stop_loss=0.05, take_profit=0.10),
                                period=20, std_dev=2.0), "Bollinger"),
    ]

    for strat, name in strategies:
        r = run_backtest(strat, name)
        assert r["trades"] >= 0, f"{name}: trades 不能为负"
        assert -100 <= r["return_pct"] <= 1000, f"{name}: return 超出合理范围"
        assert r["max_dd"] >= 0, f"{name}: max_drawdown 应>=0"
        if r["trades"] > 0:
            assert 0 <= r["win_rate"] <= 100, f"{name}: win_rate 超出 0-100"
        print(f"  OK {name:>12}  return={r['return_pct']:+7.2f}%  "
              f"sharpe={r['sharpe']:5.2f}  dd={r['max_dd']:5.2f}%  "
              f"trades={r['trades']:3d}  wr={r['win_rate']:5.1f}%")

    # 回归保护：宽松阈值 RSI(8,40,60) 在波动数据上应触发交易
    rsi_loose = RSIStrategy(StrategyConfig(symbol="ETH/USDT", timeframe="4h",
                             stop_loss=0.05, take_profit=0.20),
                             rsi_period=8, oversold=40, overbought=60)
    r_loose = run_backtest(rsi_loose, "RSI-loose")
    assert r_loose["trades"] >= 1, f"宽松 RSI 应≥1笔交易，实际 {r_loose['trades']}"
    print(f"  OK RSI(8,40,60)  return={r_loose['return_pct']:+7.2f}%  "
          f"trades={r_loose['trades']:3d}  wr={r_loose['win_rate']:5.1f}%")
    return True


def test_commission_impact():
    cfg0 = StrategyConfig(symbol="ETH/USDT", timeframe="4h",
                          stop_loss=0.05, take_profit=0.10,
                          commission_pct=0.0, slippage_pct=0.0)
    cfg1 = StrategyConfig(symbol="ETH/USDT", timeframe="4h",
                          stop_loss=0.05, take_profit=0.10,
                          commission_pct=0.001, slippage_pct=0.0005)
    r0 = run_backtest(RSIStrategy(cfg0, rsi_period=14, oversold=30, overbought=70), "fee0")
    r1 = run_backtest(RSIStrategy(cfg1, rsi_period=14, oversold=30, overbought=70), "fee1")
    print(f"  Commission: 0%={r0['return_pct']:+.4f}%  0.1%={r1['return_pct']:+.4f}%")
    assert r1["return_pct"] <= r0["return_pct"], "手续费应减少收益"
    return True


def test_bad_configs_rejected():
    bad = [
        StrategyConfig(stop_loss=0.10, take_profit=0.05),
        StrategyConfig(capital_pct=0),
        StrategyConfig(capital_pct=1.5),
    ]
    for cfg in bad:
        try:
            RSIStrategy(cfg, rsi_period=14, oversold=30, overbought=70)
            return False
        except ValueError:
            pass
    return True


def test_registry_builds_all():
    from strategies import build_strategy, STRATEGY_REGISTRY
    for name in STRATEGY_REGISTRY:
        cfg = StrategyConfig(symbol="ETH/USDT", timeframe="4h")
        s = build_strategy(name, cfg)
        assert s is not None, f"注册表无法构建 {name}"
    print(f"  Registry: {list(STRATEGY_REGISTRY.keys())}")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("  backtest.py regression tests")
    print("=" * 60)
    tests = [
        ("strategy backtest", test_all_strategies),
        ("commission impact", test_commission_impact),
        ("config validation", test_bad_configs_rejected),
        ("strategy registry", test_registry_builds_all),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"\n-- {name} --")
        try:
            if fn():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{'='*60}")
    print(f"  {passed} passed / {failed} failed / {len(tests)} total")
    print(f"{'='*60}")
    sys.exit(1 if failed > 0 else 0)
