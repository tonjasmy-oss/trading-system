#!/usr/bin/env python3
"""
使用Gate.io API分批获取BTC/USDT 2h K线数据，从2022-01-01至今
Gate.io限制每次最多10000条，需要分批获取
"""
import sys, os, time, sqlite3, requests
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from history_cache import init_cache_db, save_ohlcv, get_ohlcv as cache_get_ohlcv

GATEIO_BASE = "https://api.gateio.ws/api/v4/spot"
DB = "ohlcv_cache/ohlcv_cache.db"
SYMBOL = "BTC/USDT"
TF = "2h"
BATCH = 1000

def gateio_fetch_2h(start_ms, end_ms=None):
    """从Gate.io获取2h K线数据"""
    all_candles = []
    current_since = start_ms
    
    while True:
        params = {
            "currency_pair": "BTC_USDT",
            "interval": "2h",
            "limit": BATCH,
        }
        params["from"] = current_since // 1000
        
        try:
            resp = requests.get(
                f"{GATEIO_BASE}/candlesticks",
                params=params,
                timeout=20,
                headers={"Accept": "application/json", "User-Agent": "trading-system/1.0"},
            )
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
                break
            
            raw = resp.json()
            if not isinstance(raw, list) or len(raw) == 0:
                break
            
            for item in raw:
                try:
                    ts_ms = int(item[0]) * 1000
                    all_candles.append({
                        "timestamp": ts_ms,
                        "open": float(item[5]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "close": float(item[2]),
                        "volume": float(item[1]),
                    })
                except (IndexError, ValueError):
                    continue
            
            last_ts = all_candles[-1]["timestamp"]
            last_dt = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc)
            print(f"  Fetched {len(raw)} bars, last={last_dt.strftime('%Y-%m-%d %H:%M')}")
            
            if len(raw) < BATCH:
                break
            
            # Stop if we've reached the target end time
            if end_ms and last_ts >= end_ms:
                break
            
            current_since = last_ts + 1
            time.sleep(0.3)
            
        except Exception as e:
            print(f"  Error: {e}")
            break
    
    return all_candles

def main():
    init_cache_db()
    
    # Check existing
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM ohlcv_cache WHERE symbol=? AND timeframe=?", (SYMBOL, TF))
    r = cur.fetchone()
    conn.close()
    
    if r and r[0]:
        print(f"Existing BTC 2h: {r[0]} bars")
        print(f"  Range: {datetime.fromtimestamp(r[1]/1000, tz=timezone.utc).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(r[2]/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    
    # 目标：2022-01-01至今
    # Gate.io每次最多10000条2h K线 = 20000小时 = 833天 ≈ 2.28年
    # 所以需要从两个起点分批获取
    start_2022 = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    
    # 第一批：从2022-01-01开始（约2.28年 → 10000条，刚好）
    print(f"\nFetching batch 1: from 2022-01-01 (max 10000 bars = ~2.28 years)")
    candles_batch1 = gateio_fetch_2h(start_2022)
    print(f"  Batch1 total: {len(candles_batch1)} bars")
    
    # 第二批：如果还需要更早的数据，尝试从更早开始（但Gate.io限制最大10000条之前的数据）
    # 实际上10000条2h足够覆盖2022-01-01至今，但可能API有其他限制
    # 让我们合并所有数据
    
    all_candles = candles_batch1
    
    if all_candles:
        # Deduplicate by timestamp
        seen = set()
        unique = []
        for c in all_candles:
            if c["timestamp"] not in seen:
                seen.add(c["timestamp"])
                unique.append(c)
        all_candles = unique
        all_candles.sort(key=lambda x: x["timestamp"])
        print(f"After dedup: {len(all_candles)} bars")
        
        if all_candles:
            first_dt = datetime.fromtimestamp(all_candles[0]["timestamp"]/1000, tz=timezone.utc)
            last_dt = datetime.fromtimestamp(all_candles[-1]["timestamp"]/1000, tz=timezone.utc)
            print(f"  Range: {first_dt.strftime('%Y-%m-%d')} → {last_dt.strftime('%Y-%m-%d')}")
            
            # Save to cache
            save_ohlcv(SYMBOL, TF, all_candles)
            print("Saved to cache")
            
            # Verify
            verify = cache_get_ohlcv(SYMBOL, TF, limit=2)
            if verify:
                print(f"Verified in cache: {len(verify)} bars")
                print(f"Latest: {datetime.fromtimestamp(verify[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    
    print("\nDone!")

if __name__ == "__main__":
    main()