#!/usr/bin/env python3
"""
使用Gate.io API分批获取BTC/USDT 2h K线数据（2022-01-01 → 2024-03-01空缺填补）
Gate.io每次最多1000条
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

def gateio_fetch_2h_paginated(start_ms, end_ms=None):
    """从Gate.io分页获取2h K线数据（从start_ms向后获取）"""
    all_candles = []
    current_since = start_ms
    batch_num = 0
    
    while True:
        batch_num += 1
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
                print(f"  Batch {batch_num}: no data")
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
            first_dt = datetime.fromtimestamp(all_candles[-len(raw)]["timestamp"] / 1000, tz=timezone.utc)
            print(f"  Batch {batch_num}: {len(raw)} bars, first={first_dt.strftime('%Y-%m-%d %H:%M')}, last={last_dt.strftime('%Y-%m-%d %H:%M')}")
            
            if len(raw) < BATCH:
                break
            
            # Stop if we've passed the end time
            if end_ms and last_ts >= end_ms:
                print(f"  Reached end boundary {datetime.fromtimestamp(end_ms/1000, tz=timezone.utc)}")
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
    
    print(f"Existing BTC 2h: {r[0] if r[0] else 0} bars")
    if r and r[0]:
        print(f"  Range: {datetime.fromtimestamp(r[1]/1000, tz=timezone.utc).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(r[2]/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    
    # 我们需要填补的空缺：2022-01-01 → 2024-03-01
    start_ms = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(2024, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
    
    print(f"\nFetching BTC/USDT 2h from 2022-01-01 to 2024-03-01 (gap fill)...")
    candles = gateio_fetch_2h_paginated(start_ms, end_ms)
    print(f"Total fetched: {len(candles)} bars")
    
    if candles:
        # Deduplicate
        seen = set()
        unique = []
        for c in candles:
            if c["timestamp"] not in seen:
                seen.add(c["timestamp"])
                unique.append(c)
        candles = unique
        candles.sort(key=lambda x: x["timestamp"])
        print(f"After dedup: {len(candles)} bars")
        
        if candles:
            first_dt = datetime.fromtimestamp(candles[0]["timestamp"]/1000, tz=timezone.utc)
            last_dt = datetime.fromtimestamp(candles[-1]["timestamp"]/1000, tz=timezone.utc)
            print(f"  Range: {first_dt.strftime('%Y-%m-%d')} → {last_dt.strftime('%Y-%m-%d')}")
            
            # Save to cache
            save_ohlcv(SYMBOL, TF, candles)
            print("Saved to cache")
    
    # Final check
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM ohlcv_cache WHERE symbol=? AND timeframe=?", (SYMBOL, TF))
    r = cur.fetchone()
    conn.close()
    
    print(f"\nFinal BTC 2h: {r[0]} bars")
    if r[0]:
        print(f"  Range: {datetime.fromtimestamp(r[1]/1000, tz=timezone.utc).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(r[2]/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    
    print("\nDone!")

if __name__ == "__main__":
    main()