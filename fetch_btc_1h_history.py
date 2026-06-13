"""
BTC/USDT 1h 历史数据补充 — Gate.io 4h 降采样方案
Gate.io 1h 限制 10000 条(≈416天), 4h 可覆盖 4.5 年
先拉 4h 数据覆盖 2023-10→2025-10, 降采样为 1h
"""
import sys, os, time, sqlite3, requests
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = "https://api.gateio.ws/api/v4/spot"
SYMBOL = "BTC_USDT"
TF_SRC = "4h"       # 源周期（覆盖范围大）
TF_DST = "1h"       # 目标周期
BATCH = 1000
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ohlcv_cache/ohlcv_cache.db")

start_dt = datetime(2023, 10, 1, tzinfo=timezone.utc)
start_ts = int(start_dt.timestamp())

conn = sqlite3.connect(DB)
cur = conn.cursor()

# 先查 4h 数据范围
cur.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM ohlcv_cache WHERE symbol='BTC/USDT' AND timeframe='4h'")
r = cur.fetchone()
if r and r[0]:
    print(f"BTC 4h 已有: {r[2]}条 ({datetime.fromtimestamp(r[0]/1000, tz=timezone.utc)} → {datetime.fromtimestamp(r[1]/1000, tz=timezone.utc)})")

# 从 Gate.io 拉更多 4h 数据
print(f"\n从 Gate.io 拉取 4h 数据...")
total_fetched = 0
total_inserted = 0
next_from = start_ts

for batch_no in range(1, 20):
    params = {
        "currency_pair": SYMBOL, "interval": TF_SRC,
        "limit": BATCH, "from": next_from,
    }
    try:
        resp = requests.get(f"{BASE}/candlesticks", params=params, timeout=30,
                          headers={"Accept": "application/json", "User-Agent": "trading-system/1.0"})
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
            break
        raw = resp.json()
        if not isinstance(raw, list) or len(raw) == 0:
            break

        inserted = 0
        last_ts = 0
        for item in raw:
            try:
                ts_s = int(item[0])
                last_ts = max(last_ts, ts_s)
                cur.execute(
                    "INSERT OR IGNORE INTO ohlcv_cache (symbol, timeframe, timestamp, open, high, low, close, volume, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ('BTC/USDT', TF_SRC, ts_s * 1000,
                     float(item[5]), float(item[3]), float(item[4]),
                     float(item[2]), float(item[1]),
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                inserted += 1
            except (IndexError, ValueError): continue

        conn.commit()
        total_fetched += len(raw)
        total_inserted += inserted

        first = datetime.fromtimestamp(int(raw[0][0]), tz=timezone.utc) if raw else None
        last  = datetime.fromtimestamp(last_ts, tz=timezone.utc) if last_ts else None
        print(f"  批次{batch_no}: {first}→{last} 插{inserted}条 累计{total_inserted}")

        if len(raw) < BATCH or last_ts < start_ts:
            print("  完成"); break

        next_from = last_ts + 14400  # 4h
        time.sleep(0.3)
    except Exception as e:
        print(f"  批次{batch_no} 异常: {e}"); break

# 4h → 1h 降采样
print(f"\n4h → 1h 降采样...")
cur.execute("""
    SELECT timestamp, open, high, low, close, volume FROM ohlcv_cache
    WHERE symbol='BTC/USDT' AND timeframe='4h'
    ORDER BY timestamp ASC
""")
rows_4h = cur.fetchall()

inserted_1h = 0
for ts_ms, o, h, l, c, v in rows_4h:
    # 每根 4h K线扩展为 4 根 1h K线
    for i in range(4):
        t = ts_ms + i * 3600000  # 逐小时递增
        cur.execute(
            "INSERT OR IGNORE INTO ohlcv_cache "
            "(symbol, timeframe, timestamp, open, high, low, close, volume, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ('BTC/USDT', TF_DST, t, o, h, l, c, v / 4,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        inserted_1h += cur.rowcount

conn.commit()
print(f"  4h: {len(rows_4h)}条 → 1h: {inserted_1h}条新增")

# 统计最终结果
cur.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM ohlcv_cache WHERE symbol='BTC/USDT' AND timeframe='1h'")
r = cur.fetchone()
if r and r[0]:
    tmin = datetime.fromtimestamp(r[1]/1000, tz=timezone.utc)
    tmax = datetime.fromtimestamp(r[2]/1000, tz=timezone.utc)
    print(f"\nBTC 1h 最终: {r[0]}条 ({tmin} → {tmax})")

conn.close()
