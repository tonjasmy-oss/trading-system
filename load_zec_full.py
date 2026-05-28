import sqlite3, json

conn = sqlite3.connect('ohlcv_cache/ohlcv_cache.db')
cur = conn.cursor()
cur.execute("PRAGMA table_info(ohlcv_cache)")
print('columns:', [r[1] for r in cur.fetchall()])

with open('zec_3y_full.json') as f:
    candles = json.load(f)

# 写入 ZEC/USDT 1d 数据（覆盖）
cur.execute("DELETE FROM ohlcv_cache WHERE symbol='ZEC/USDT' AND timeframe='1d'")
for c in candles:
    cur.execute("INSERT INTO ohlcv_cache (symbol, timeframe, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ('ZEC/USDT', '1d', c['timestamp'], c['open'], c['high'], c['low'], c['close'], c['volume']))

conn.commit()
cur.execute("SELECT COUNT(*) FROM ohlcv_cache WHERE symbol='ZEC/USDT' AND timeframe='1d'")
print(f'ZEC/USDT 1d: {cur.fetchone()[0]} 条')
cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM ohlcv_cache WHERE symbol='ZEC/USDT' AND timeframe='1d'")
r = cur.fetchone()
print('范围:', r[0], '~', r[1])
import time
print('范围:', time.strftime('%Y-%m-%d', time.localtime(r[0]/1000)), '~', time.strftime('%Y-%m-%d', time.localtime(r[1]/1000)))
conn.close()
print('写入完成')