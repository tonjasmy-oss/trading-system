#!/usr/bin/env python3
"""补充BTC 2h历史K线数据 - 从Gate.io允许的最早时间开始，分段获取"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import requests
from datetime import datetime, timezone
from history_cache import init_cache_db, save_ohlcv, get_ohlcv as cache_get_ohlcv

GATEIO_BASE = "https://api.gateio.ws/api/v4"

def gateio_fetch_full(symbol_base, timeframe, start_dt):
    """从Gate.io获取K线数据，从start_dt开始分页获取"""
    pair = f"{symbol_base.upper()}_USDT"
    interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "2h": "2h"}
    interval = interval_map.get(timeframe, timeframe)
    
    all_candles = []
    current_since = int(start_dt.timestamp())
    
    while True:
        params = {"currency_pair": pair, "interval": interval, "limit": 1000}
        params["from"] = current_since
        
        try:
            resp = requests.get(f"{GATEIO_BASE}/spot/candlesticks", params=params, timeout=15)
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
                break
            
            raw = resp.json()
            if not isinstance(raw, list) or len(raw) == 0:
                print("  Empty, done")
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
            first_ts = all_candles[0]["timestamp"]
            first_dt = datetime.fromtimestamp(first_ts/1000, tz=timezone.utc)
            last_dt = datetime.fromtimestamp(last_ts/1000, tz=timezone.utc)
            print(f"  {len(all_candles)} total: {first_dt.strftime('%Y-%m-%d')} to {last_dt.strftime('%Y-%m-%d')}")
            
            if len(raw) < 1000:
                break
            current_since = last_ts // 1000 + 1
            time.sleep(0.3)
            
        except Exception as e:
            print(f"  Error: {e}")
            break
    
    return all_candles

def main():
    init_cache_db()
    
    symbol_base = "BTC"
    symbol_full = "BTC/USDT"
    timeframe = "2h"
    
    # Gate.io 2h 最大10000条，从2024-03-01可以获取到2026-06-11
    # (约4920条，远少于10000限制)
    start_dt = datetime(2024, 3, 1, tzinfo=timezone.utc)
    
    print(f"Fetching {symbol_full} {timeframe} from {start_dt.strftime('%Y-%m-%d')}")
    
    candles = gateio_fetch_full(symbol_base, timeframe, start_dt)
    print(f"\nTotal fetched: {len(candles)} bars")
    
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
        
        # Sort by timestamp
        candles.sort(key=lambda x: x["timestamp"])
        
        # Save to cache
        save_ohlcv(symbol_full, timeframe, candles)
        print(f"Saved to cache")
        
        # Verify
        verify = cache_get_ohlcv(symbol_full, timeframe)
        print(f"Verified in cache: {len(verify)} bars")
        if verify:
            from_ts = verify[0]["timestamp"]
            to_ts = verify[-1]["timestamp"]
            print(f"Range: {datetime.fromtimestamp(from_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(to_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    else:
        print("No data!")
    
    print("\nDone!")

if __name__ == "__main__":
    main()