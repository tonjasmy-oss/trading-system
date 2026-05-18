import sys, os
sys.path.insert(0, '/root/.openclaw/workspace/trading-system')
from config import ATRSTOP_EMA_PERIOD, ATRSTOP_ATR_PERIOD, ATRSTOP_ATR_MULTIPLIER
from backtest import BacktestEngine
from strategies import (RSIStrategy, MACDStrategy, BollingerBandsStrategy,
    SMAcrossStrategy, KDJStrategy, ATRStopStrategy, StrategyConfig, Signal)
from multi_strategy_vote import MultiStrategyVote
from history_cache import get_ohlcv, init_cache_db

init_cache_db()

PAIRS = ['SUI/USDT', 'BTC/USDT', 'ETH/USDT', 'SOL/USDT']
TF = '4h'

for pair in PAIRS:
    candles = get_ohlcv(pair, TF, limit=5000)
    if len(candles) < 50:
        print(f'{pair}: 数据不足({len(candles)}条)，跳过')
        continue

    print(f'\n{"="*75}')
    print(f'  {pair} {TF} ({len(candles)}条)')

    # Use the optimized SUI params for all pairs (close enough)
    config = StrategyConfig(symbol=pair, timeframe=TF,
        capital_pct=0.05, stop_loss=0.012, take_profit=0.025,
        commission_pct=0.001, slippage_pct=0.0005)

    strategies = [
        ('ATRSTOP', ATRStopStrategy(config, ema_period=ATRSTOP_EMA_PERIOD, atr_period=ATRSTOP_ATR_PERIOD, atr_multiplier=ATRSTOP_ATR_MULTIPLIER)),
    ]
    # Only add VOTE if we have enough data for all sub-strategies
    rsi = RSIStrategy(config, rsi_period=10, oversold=28.0, overbought=65.0)
    strategies.extend([
        ('RSI(28)', rsi),
        ('BOLLINGER', BollingerBandsStrategy(config, period=20, std_dev=2.0)),
        ('MACD', MACDStrategy(config)),
    ])
    # VOTE = RSI+MACD+BOLL
    vote = MultiStrategyVote(strategies=[
        (RSIStrategy(config, rsi_period=10, oversold=28.0, overbought=65.0), 0.4),
        (MACDStrategy(config), 0.3),
        (BollingerBandsStrategy(config, period=20, std_dev=2.0), 0.3),
    ], threshold=0.3, name='VOTE')
    strategies.append(('VOTE', vote))

    results = []
    for name, s in strategies:
        try:
            eng = BacktestEngine(s, initial_capital=271.76)
            eng.candles = candles
            eng.compute_signals()
            r = eng.run()
            results.append((name, r))
        except Exception as e:
            print(f'  {name}: 失败({e})')

    results.sort(key=lambda x: -x[1].total_return_pct)
    print(f'  {"策略":12s} {"收益":>8s} {"回撤":>8s} {"胜率":>6s} {"交易":>5s}')
    print(f'  {"-"*45}')
    for name, r in results:
        print(f'  {name:12s} {r.total_return_pct:>+7.2f}% {r.max_drawdown_pct:>7.2f}% {r.win_rate_pct:>5.1f}% {r.total_trades:>5d}')
