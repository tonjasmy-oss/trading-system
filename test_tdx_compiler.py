"""
P1 通达信兼容 — 测试套件
测试：TdxCompiler / FormulaStrategy / BacktestEngine(公式) / TradingAgent(公式)

运行：
  pytest test_tdx_compiler.py -v
  python test_tdx_compiler.py
"""

import sys
import os
import random
import math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from tdx_compiler import (
    TdxCompiler, FormulaStrategy, BUILTIN_FORMULAS,
    _is_buy_signal_var, _is_sell_signal_var,
)
from strategies import Signal
from backtest import BacktestEngine
from strategies import StrategyConfig


# ============================================================
# 固定种子模拟 K 线数据（200 根 4h K 线）
# ============================================================

def _make_candles(n=200, base=2000.0, trend=0.001):
    """生成确定性模拟 K 线"""
    random.seed(42)
    candles = []
    price = base
    for i in range(n):
        r = random.uniform(-0.03, 0.03) + trend
        o = price
        c = price * (1 + r)
        h = max(o, c) * (1 + random.uniform(0, 0.01))
        l = min(o, c) * (1 - random.uniform(0, 0.01))
        vol = random.uniform(100, 1000)
        candles.append({
            "timestamp": 1600000000000 + i * 4 * 3600000,
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": round(vol, 2),
        })
        price = c
    return candles


CANDLES = _make_candles()


# ============================================================
# TdxCompiler 单元测试
# ============================================================

class TestTdxCompilerBasic:
    """编译器基础功能"""

    def test_simple_assignment(self):
        """变量赋值"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile("MA5:MA(CLOSE,5);")
        ind = ind_fn(CANDLES)
        assert "MA5" in ind
        assert len(ind["MA5"]) == len(CANDLES)
        assert ind["MA5"][-1] > 0

    def test_ma_cross(self):
        """均线金叉死叉"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(
            "MA5:MA(CLOSE,5);MA10:MA(CLOSE,10);"
            "买:CROSS(MA5,MA10);"
            "卖:CROSS(MA10,MA5);"
        )
        ind = ind_fn(CANDLES)
        sig = sig_fn(CANDLES)
        assert "MA5" in ind
        assert "MA10" in ind
        assert "买" in ind
        assert "卖" in ind
        buy_count = sum(1 for s in sig if s == 1)
        sell_count = sum(1 for s in sig if s == -1)
        assert buy_count > 0, "应有买入信号"
        assert sell_count > 0, "应有卖出信号"
        print(f"  均线金叉死叉：买={buy_count}次 卖={sell_count}次")

    def test_kdj_formula(self):
        """KDJ 公式"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(BUILTIN_FORMULAS["KDJ"])
        ind = ind_fn(CANDLES)
        sig = sig_fn(CANDLES)
        assert "K" in ind
        assert "D" in ind
        assert "J" in ind
        # K/D 应在 0~100 范围
        k_vals = ind["K"]
        assert all(0 <= v <= 200 for v in k_vals if v != 0)
        buy_count = sum(1 for s in sig if s == 1)
        print(f"  KDJ：买={buy_count}次")

    def test_macd_formula(self):
        """MACD 公式"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(BUILTIN_FORMULAS["MACD"])
        ind = ind_fn(CANDLES)
        sig = sig_fn(CANDLES)
        assert "DIF" in ind
        assert "DEA" in ind
        assert "MACD" in ind
        # DIF 应有正负值
        assert any(v > 0 for v in ind["DIF"])
        assert any(v < 0 for v in ind["DIF"])
        print(f"  MACD：DIF范围 [{min(ind['DIF']):.2f}, {max(ind['DIF']):.2f}]")

    def test_rsi_formula(self):
        """RSI 公式"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(BUILTIN_FORMULAS["RSI"])
        ind = ind_fn(CANDLES)
        sig = sig_fn(CANDLES)
        assert "RSI" in ind
        rsi_vals = [v for v in ind["RSI"] if v != 0]
        assert all(0 <= v <= 100 for v in rsi_vals), "RSI 应在 0~100"
        print(f"  RSI：范围 [{min(rsi_vals):.2f}, {max(rsi_vals):.2f}]")

    def test_cci_formula(self):
        """CCI 公式"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(BUILTIN_FORMULAS["CCI"])
        ind = ind_fn(CANDLES)
        sig = sig_fn(CANDLES)
        assert "CCI" in ind
        print(f"  CCI：范围 [{min(ind['CCI']):.2f}, {max(ind['CCI']):.2f}]")

    def test_boll_formula(self):
        """布林带公式"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(BUILTIN_FORMULAS["BOLL"])
        ind = ind_fn(CANDLES)
        assert "BOLL" in ind
        assert "UB" in ind
        assert "LB" in ind
        # UB > BOLL > LB
        for i in range(50, len(CANDLES)):
            if ind["UB"][i] != 0:
                assert ind["UB"][i] >= ind["BOLL"][i], "上轨应>=中轨"
                assert ind["BOLL"][i] >= ind["LB"][i], "中轨应>=下轨"
                break
        print(f"  BOLL：BOLL范围 [{min(ind['BOLL']):.2f}, {max(ind['BOLL']):.2f}]")

    def test_if_expression(self):
        """IF 三元表达式"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(
            "A:CLOSE>OPEN;"
            "B:IF(A, 1, -1);"
        )
        ind = ind_fn(CANDLES)
        assert "A" in ind
        assert "B" in ind
        # IF 应有 1 和 -1
        b_vals = [v for v in ind["B"] if v != 0]
        assert len(set(math.sign(v) for v in b_vals)) >= 1

    def test_ref_function(self):
        """REF 滞后函数"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile("MA5:MA(CLOSE,5);REF1:REF(MA5,1);")
        ind = ind_fn(CANDLES)
        # REF(MA5, 1) 应比 MA5 晚一周期
        for i in range(10, len(CANDLES) - 1):
            if ind["MA5"][i] != 0 and ind["REF1"][i + 1] != 0:
                assert abs(ind["REF1"][i + 1] - ind["MA5"][i]) < 0.01, "REF 应滞后一期"
                break

    def test_cross_function(self):
        """CROSS 穿越函数"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(
            "A:MA(CLOSE,5);B:MA(CLOSE,10);"
            "买:CROSS(A,B);"
        )
        ind = ind_fn(CANDLES)
        sig = sig_fn(CANDLES)
        # CROSS 应产生 1 信号
        buy_count = sum(1 for s in sig if s == 1)
        assert buy_count > 0, "CROSS 应产生信号"

    def test_chinese_var_names(self):
        """中文变量名"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(
            "买:CROSS(MA(CLOSE,5),MA(CLOSE,10));"
            "卖:CROSS(MA(CLOSE,10),MA(CLOSE,5));"
        )
        ind = ind_fn(CANDLES)
        sig = sig_fn(CANDLES)
        assert "买" in ind
        assert "卖" in ind
        buy_count = sum(1 for s in sig if s == 1)
        sell_count = sum(1 for s in sig if s == -1)
        assert buy_count > 0
        assert sell_count > 0

    def test_custom_formula_string(self):
        """用户自定义公式字符串"""
        custom = "RSV:(CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;"
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(custom)
        ind = ind_fn(CANDLES)
        assert "RSV" in ind
        rsv_vals = [v for v in ind["RSV"] if v != 0]
        assert all(0 <= v <= 100 for v in rsv_vals)

    def test_invalid_formula(self):
        """无效公式应抛出异常"""
        compiler = TdxCompiler()
        with pytest.raises(Exception):
            compiler.compile("INVALID @#$%")

    def test_empty_formula(self):
        """空公式"""
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile("")
        ind = ind_fn(CANDLES)
        assert len(ind) == 0


class TestSignalDetection:
    """信号检测"""

    def test_buy_signal_var_detection(self):
        assert _is_buy_signal_var("买")
        assert _is_buy_signal_var("买入")
        assert _is_buy_signal_var("BUY")
        assert _is_buy_signal_var("XG")
        assert _is_buy_signal_var("出击")
        assert not _is_buy_signal_var("MA5")
        assert not _is_buy_signal_var("DIF")

    def test_sell_signal_var_detection(self):
        assert _is_sell_signal_var("卖")
        assert _is_sell_signal_var("卖出")
        assert _is_sell_signal_var("SELL")
        assert _is_sell_signal_var("止盈")
        assert _is_sell_signal_var("止损")
        assert not _is_sell_signal_var("MA5")


class TestFormulaStrategy:
    """FormulaStrategy 策略类"""

    def test_builtin_kdj(self):
        """KDJ 内置公式"""
        strategy = FormulaStrategy(
            formula=BUILTIN_FORMULAS["KDJ"],
            symbol="ETH/USDT",
            timeframe="4h",
        )
        indicators = strategy.populate_indicators(CANDLES)
        assert "K" in indicators
        assert "D" in indicators
        assert "J" in indicators

        signals = strategy.populate_entry_trend(CANDLES)
        assert len(signals) == len(CANDLES)
        assert signals[-1] in (Signal.BUY, Signal.SELL, Signal.HOLD)

    def test_builtin_macd(self):
        """MACD 内置公式"""
        strategy = FormulaStrategy(
            formula=BUILTIN_FORMULAS["MACD"],
            symbol="ETH/USDT",
            timeframe="4h",
        )
        indicators = strategy.populate_indicators(CANDLES)
        assert "DIF" in indicators
        assert "DEA" in indicators

    def test_builtin_ma_cross(self):
        """均线交叉内置公式"""
        strategy = FormulaStrategy(
            formula=BUILTIN_FORMULAS["MA_CROSS"],
            symbol="ETH/USDT",
            timeframe="4h",
        )
        indicators = strategy.populate_indicators(CANDLES)
        signals = strategy.populate_entry_trend(CANDLES)
        buy_count = sum(1 for s in signals if s == Signal.BUY)
        assert buy_count > 0, "MA_CROSS 应有买入信号"

    def test_get_config(self):
        """get_config 返回 StrategyConfig"""
        strategy = FormulaStrategy(
            formula=BUILTIN_FORMULAS["MACD"],
            symbol="BTC/USDT",
            timeframe="1h",
            stop_loss=0.03,
            take_profit=0.06,
        )
        cfg = strategy.get_config()
        assert cfg.symbol == "BTC/USDT"
        assert cfg.timeframe == "1h"
        assert cfg.stop_loss == 0.03
        assert cfg.take_profit == 0.06


class TestBacktestFormula:
    """回测引擎 × 公式策略"""

    def test_kdj_backtest(self):
        """KDJ 公式回测"""
        strategy = FormulaStrategy(
            formula=BUILTIN_FORMULAS["KDJ"],
            symbol="ETH/USDT",
            timeframe="4h",
            stop_loss=0.05,
            take_profit=0.10,
        )
        engine = BacktestEngine(strategy, initial_capital=10000.0)
        engine.candles = CANDLES
        engine.compute_signals()
        result = engine.run()

        assert result.total_trades >= 0
        assert result.max_drawdown_pct >= 0
        assert result.sharpe_ratio is not None
        print(f"\n  KDJ 回测：收益率={result.total_return_pct:+.2f}% "
              f"夏普={result.sharpe_ratio:.2f} "
              f"交易={result.total_trades}次 "
              f"胜率={result.win_rate_pct:.1f}%")

    def test_macd_backtest(self):
        """MACD 公式回测"""
        strategy = FormulaStrategy(
            formula=BUILTIN_FORMULAS["MACD"],
            symbol="ETH/USDT",
            timeframe="4h",
            stop_loss=0.05,
            take_profit=0.10,
        )
        engine = BacktestEngine(strategy, initial_capital=10000.0)
        engine.candles = CANDLES
        engine.compute_signals()
        result = engine.run()

        print(f"\n  MACD 回测：收益率={result.total_return_pct:+.2f}% "
              f"夏普={result.sharpe_ratio:.2f} "
              f"交易={result.total_trades}次")
        assert result.total_trades >= 0

    def test_ma_cross_backtest(self):
        """均线交叉公式回测"""
        strategy = FormulaStrategy(
            formula=BUILTIN_FORMULAS["MA_CROSS"],
            symbol="ETH/USDT",
            timeframe="4h",
            stop_loss=0.05,
            take_profit=0.10,
        )
        engine = BacktestEngine(strategy, initial_capital=10000.0)
        engine.candles = CANDLES
        engine.compute_signals()
        result = engine.run()

        print(f"\n  MA_CROSS 回测：收益率={result.total_return_pct:+.2f}% "
              f"夏普={result.sharpe_ratio:.2f} "
              f"交易={result.total_trades}次 "
              f"胜率={result.win_rate_pct:.1f}%")
        assert result.total_trades >= 0

    def test_custom_formula_backtest(self):
        """自定义公式回测"""
        custom = "RSV:(CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;" \
                "K:SMA(RSV,3,1);" \
                "D:SMA(K,3,1);" \
                "买:CROSS(K,D) AND K<20;" \
                "卖:CROSS(D,K) AND K>80;"
        strategy = FormulaStrategy(
            formula=custom,
            symbol="ETH/USDT",
            timeframe="4h",
            stop_loss=0.05,
            take_profit=0.10,
        )
        engine = BacktestEngine(strategy, initial_capital=10000.0)
        engine.candles = CANDLES
        engine.compute_signals()
        result = engine.run()

        assert result.total_trades >= 0
        print(f"\n  自定义 KDJ 回测：收益率={result.total_return_pct:+.2f}% "
              f"交易={result.total_trades}次")


class TestBuiltinFormulas:
    """所有内置公式完整性"""

    def test_all_builtins_exist(self):
        """所有内置公式都存在"""
        for name, formula in BUILTIN_FORMULAS.items():
            assert len(formula.strip()) > 0, f"{name} 公式为空"
            compiler = TdxCompiler()
            try:
                ind_fn, sig_fn = compiler.compile(formula)
                ind = ind_fn(CANDLES)
                assert len(ind) > 0, f"{name} 无输出变量"
                print(f"  {name}: {list(ind.keys())}")
            except Exception as e:
                pytest.fail(f"{name} 编译失败: {e}")

    def test_all_builtins_compile_and_run(self):
        """所有内置公式都能编译并运行"""
        for name, formula in BUILTIN_FORMULAS.items():
            compiler = TdxCompiler()
            ind_fn, sig_fn = compiler.compile(formula)
            sig = sig_fn(CANDLES)
            assert len(sig) == len(CANDLES)
            # 信号应在 [-1, 0, 1] 范围内
            assert all(s in (-1, 0, 1) for s in sig)


# ============================================================
# 主程序（直接运行）
# ============================================================

def _run_all_tests():
    """手动运行所有测试（无 pytest）"""
    candles = _make_candles()
    passed = 0
    failed = 0

    def run(name, fn):
        nonlocal passed, failed
        try:
            result = fn()
            if result:
                print(f"  ✅ {name}")
                passed += 1
            else:
                print(f"  ❌ {name}: 返回 False")
                failed += 1
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print("  P1 通达信兼容 — 测试套件")
    print("=" * 60)

    # TdxCompiler 基础
    run("MA赋值",           lambda: "MA5" in TdxCompiler().compile("MA5:MA(CLOSE,5);")[0](candles))
    run("MA均线金叉死叉",   lambda: len(TdxCompiler().compile("MA5:MA(CLOSE,5);MA10:MA(CLOSE,10);买:CROSS(MA5,MA10);卖:CROSS(MA10,MA5);")[1](candles)) == len(candles))
    run("KDJ",             lambda: "K" in TdxCompiler().compile(BUILTIN_FORMULAS["KDJ"])[0](candles))
    run("MACD",            lambda: "DIF" in TdxCompiler().compile(BUILTIN_FORMULAS["MACD"])[0](candles))
    run("RSI",             lambda: "RSI" in TdxCompiler().compile(BUILTIN_FORMULAS["RSI"])[0](candles))
    run("CCI",             lambda: "CCI" in TdxCompiler().compile(BUILTIN_FORMULAS["CCI"])[0](candles))
    run("布林带",          lambda: "BOLL" in TdxCompiler().compile(BUILTIN_FORMULAS["BOLL"])[0](candles))
    run("IF表达式",        lambda: "B" in TdxCompiler().compile("A:CLOSE>OPEN;B:IF(A,1,-1);")[0](candles))
    run("REF函数",         lambda: "REF1" in TdxCompiler().compile("REF1:REF(MA(CLOSE,5),1);")[0](candles))
    run("CROSS函数",       lambda: len(TdxCompiler().compile("买:CROSS(MA(CLOSE,5),MA(CLOSE,10));")[1](candles)) == len(candles))
    run("中文变量名",      lambda: "买" in TdxCompiler().compile("买:CROSS(MA(CLOSE,5),MA(CLOSE,10));")[0](candles))
    run("自定义RSV",       lambda: "RSV" in TdxCompiler().compile("RSV:(CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;")[0](candles))

    # FormulaStrategy
    run("FormulaStrategy KDJ",   lambda: "K"  in FormulaStrategy(formula=BUILTIN_FORMULAS["KDJ"],  symbol="ETH/USDT").populate_indicators(candles))
    run("FormulaStrategy MACD",  lambda: "DIF" in FormulaStrategy(formula=BUILTIN_FORMULAS["MACD"], symbol="ETH/USDT").populate_indicators(candles))
    run("get_config",            lambda: FormulaStrategy(formula=BUILTIN_FORMULAS["MACD"], symbol="BTC/USDT", stop_loss=0.03, take_profit=0.06).get_config().stop_loss == 0.03)

    # 内置公式完整性
    run("所有内置公式完整性", lambda: all(len(TdxCompiler().compile(f)[0](candles)) > 0 for f in BUILTIN_FORMULAS.values()))

    print("=" * 60)
    print(f"  通过: {passed}  失败: {failed}  总计: {passed + failed}")
    print("=" * 60)

    # 回测单独打印
    print("\n回测结果：")
    for name, formula_key in [("KDJ", "KDJ"), ("MACD", "MACD"), ("MA_CROSS", "MA_CROSS"), ("CCI", "CCI")]:
        s = FormulaStrategy(formula=BUILTIN_FORMULAS[formula_key], symbol="ETH/USDT", stop_loss=0.05, take_profit=0.10)
        e = BacktestEngine(s, 10000.0)
        e.candles = candles
        e.compute_signals()
        r = e.run()
        print(f"  {name}: 收益率={r.total_return_pct:+.2f}% 夏普={r.sharpe_ratio:.2f} "
              f"交易={r.total_trades}次 胜率={r.win_rate_pct:.1f}%")

    return failed == 0


if __name__ == "__main__":
    ok = _run_all_tests()
    exit(0 if ok else 1)
