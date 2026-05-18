import sys
sys.path.insert(0, '/root/.openclaw/workspace/trading-system')
from backtest import BacktestEngine
from strategies import RSIStrategy, MACDStrategy, BollingerBandsStrategy, StrategyConfig
from multi_strategy_vote import MultiStrategyVote
from history_cache import get_ohlcv, init_cache_db

init_cache_db()
candles = get_ohlcv('SUI/USDT', '4h', limit=5000)
print(f'数据: {len(candles)} 条')

config = StrategyConfig(symbol='SUI/USDT', timeframe='4h',
    capital_pct=0.05, stop_loss=0.012, take_profit=0.025,
    commission_pct=0.001, slippage_pct=0.0005)

rsi = RSIStrategy(config, rsi_period=10, oversold=28.0, overbought=65.0)
macd = MACDStrategy(config)
boll = BollingerBandsStrategy(config, period=20, std_dev=2.0)
vote = MultiStrategyVote(strategies=[(rsi, 0.4), (macd, 0.3), (boll, 0.3)],
    threshold=0.3, name='VOTE')

for name, s in [('VOTE(优化)', vote), ('BOLLINGER', boll), ('MACD', macd), ('RSI(优化)', rsi)]:
    eng = BacktestEngine(s, initial_capital=271.76)
    eng.candles = candles
    eng.compute_signals()
    r = eng.run()
    print(f'{name:12s} 收益={r.total_return_pct:+.2f}%  回撤={r.max_drawdown_pct:.2f}%  胜率={r.win_rate_pct:.1f}%  交易={r.total_trades}  夏普={r.sharpe_ratio:.2f}')
