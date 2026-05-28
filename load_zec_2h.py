import sqlite3, json

conn = sqlite3.connect('ohlcv_cache/ohlcv_cache.db')
cur = conn.cursor()

with open('zec_2h_raw.json') as f:
    candles = json.load(f)

# 写入 ZEC/USDT 2h 数据
cur.execute("DELETE FROM ohlcv_cache WHERE symbol='ZEC/USDT' AND timeframe='2h'")
for c in candles:
    cur.execute("INSERT INTO ohlcv_cache (symbol, timeframe, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ('ZEC/USDT', '2h', c['timestamp'], c['open'], c['high'], c['low'], c['close'], c['volume']))

conn.commit()
cur.execute("SELECT COUNT(*) FROM ohlcv_cache WHERE symbol='ZEC/USDT' AND timeframe='2h'")
print(f'ZEC/USDT 2h: {cur.fetchone()[0]} 条')
conn.close()
print('写入完成')