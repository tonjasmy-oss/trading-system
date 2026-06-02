"""检查各标的数据可用性"""
from history_cache import get_ohlcv, init_cache_db
from datetime import datetime, timezone

init_cache_db()
symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'SUI/USDT', 'XAUT/USDT']
timeframes = ['2h', '4h']

for s in symbols:
    for tf in timeframes:
        data = get_ohlcv(s, tf, limit=5000)
        if data:
            start = data[0]['timestamp']
            end = data[-1]['timestamp']
            s_dt = datetime.fromtimestamp(start/1000, tz=timezone.utc).strftime('%Y-%m-%d')
            e_dt = datetime.fromtimestamp(end/1000, tz=timezone.utc).strftime('%Y-%m-%d')
            print(f'{s:>12s} {tf:>3s}: {len(data):>5d} 条  [{s_dt} ~ {e_dt}]')
        else:
            print(f'{s:>12s} {tf:>3s}: 无数据')
