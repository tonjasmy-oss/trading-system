#!/usr/bin/env python3
"""
formula_tester.py — 通达信公式快速回测工具
用法: python3 formula_tester.py --symbol ETH/USDT --formula "DIF:=EMA(CLOSE,12)-EMA(CLOSE,26);..."
"""
import argparse, sys, os
from datetime import datetime

# 加载 .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

sys.path.insert(0, os.path.dirname(__file__))

from tdx_compiler import FormulaStrategy, BUILTIN_FORMULAS
from strategies import StrategyConfig, Signal
from batch_backtest import BacktestEngine, BacktestConfig, BacktestResult
from crypto_api import get_ohlcv


def format_result(r: BacktestResult) -> str:
    emoji = "🟢" if r.total_return_pct >= 0 else "🔴"
    return f"""📊 回测结果

{'='*36}
  收益率   {emoji} {r.total_return_pct:+.2f}%
  夏普比率   {r.sharpe_ratio:.2f}
  最大回撤   {r.max_drawdown_pct:.2f}%
  胜率      {r.win_rate_pct:.1f}%
  总交易次数 {r.total_trades} 次
  持仓时长   {r.avg_holding_ms/3600000:.1f}h（均）
{'='*36}"""


def quick_backtest(symbol: str, formula: str, timeframe: str = "4h",
                   initial_capital: float = 10000.0,
                   stop_loss: float = 0.02, take_profit: float = 0.04) -> str:
    """对单个公式执行快速回测，返回格式化的结果字符串"""

    symbol_base = symbol.split("/")[0]

    # 1. 获取数据
    candles = get_ohlcv(symbol=symbol_base, timeframe=timeframe, limit=200)
    if not candles or len(candles) < 30:
        return f"❌ 数据不足（仅获取到 {len(candles) if candles else 0} 条 K线）"

    # 2. 编译公式
    try:
        strategy = FormulaStrategy(
            formula=formula,
            symbol=symbol,
            timeframe=timeframe,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
    except Exception as e:
        return f"❌ 公式编译失败: {e}"

    # 3. 运行回测
    try:
        from backtest import BacktestEngine
        engine = BacktestEngine(strategy=strategy, initial_capital=initial_capital)
        engine.candles = candles
        engine.compute_signals()
        result = engine.run()
    except Exception as e:
        return f"❌ 回测运行失败: {e}"

    # 4. 格式化输出
    header = f"📈 公式回测 — {symbol} ({timeframe})"
    formula_preview = formula[:60].replace("\n", " ") + ("..." if len(formula) > 60 else "")
    return f"""{header}
公式: {formula_preview}
{format_result(result)}"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="通达信公式快速回测")
    parser.add_argument("--symbol", default="ETH/USDT", help="标的，如 ETH/USDT")
    parser.add_argument("--formula", required=True, help="通达信公式代码")
    parser.add_argument("--timeframe", default="4h", help="K线周期，默认 4h")
    parser.add_argument("--capital", type=float, default=10000.0, help="初始资金")
    parser.add_argument("--stop-loss", type=float, default=0.02, help="止损比例")
    parser.add_argument("--take-profit", type=float, default=0.04, help="止盈比例")
    args = parser.parse_args()

    result = quick_backtest(
        symbol=args.symbol,
        formula=args.formula,
        timeframe=args.timeframe,
        initial_capital=args.capital,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
    )
    print(result)
