#!/usr/bin/env python3
"""使用Binance API获取BTC/USDT 2h K线数据，从2022-01-01至今"""
import sys, os, time, sqlite3, requests
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from history_cache import init_cache_db, save_ohlcv, get_ohlcv as cache_get_ohlcv

DB = "ohlcv_cache/ohlcv_cache.db"
SYMBOL = "BTC/USDT"
TF = "2h"
BATCH = 1000

def fetch_binance_2h(since_ms):
    """从Binance获取2h K线数据"""
    all_candles = []
    current_since = since_ms
    
    while True:
        params = {
            "symbol": "BTCUSDT",
            "interval": "2h",
            "startTime": current_since,
            "limit": BATCH,
        }
        try:
            resp = requests.get(
                "https://api.binance.com/api/v3/klines",
                params=params,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
                break
            
            raw = resp.json()
            if not isinstance(raw, list) or len(raw) == 0:
                break
            
            for item in raw:
                try:
                    ts_ms = int(item[0])
                    all_candles.append({
                        "timestamp": ts_ms,
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]),
                    })
                except (IndexError, ValueError):
                    continue
            
            last_ts = all_candles[-1]["timestamp"]
            last_dt = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc)
            print(f"  Fetched {len(raw)} bars, last={last_dt.strftime('%Y-%m-%d %H:%M')}")
            
            if len(raw) < BATCH:
                break
            
            current_since = last_ts + 1
            time.sleep(0.5)
            
        except Exception as e:
            print(f"  Error: {e}")
            break
    
    return all_candles

def main():
    init_cache_db()
    
    # Check existing
    existing = cache_get_ohlcv(SYMBOL, TF, limit=2)
    if existing:
        print(f"Existing BTC 2h: {len(existing)} bars, latest: {datetime.fromtimestamp(existing[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    
    # Fetch from 2022-01-01
    start_dt = datetime(2022, 1, 1, tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)
    
    print(f"\nFetching BTC/USDT 2h from {start_dt.isoformat()}...")
    candles = fetch_binance_2h(start_ms)
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
        print(f"After dedup: {len(candles)} bars")
        
        # Save
        save_ohlcv(SYMBOL, TF, candles)
        print("Saved to cache")
        
        # Verify
        verify = cache_get_ohlcv(SYMBOL, TF, limit=2)
        if verify:
            print(f"Verified: {len(verify)} bars in cache")
            print(f"Latest: {datetime.fromtimestamp(verify[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    
    print("\nDone!")

if __name__ == "__main__":
    main()