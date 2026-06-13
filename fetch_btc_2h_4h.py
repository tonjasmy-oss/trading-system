#!/usr/bin/env python3
"""补充BTC历史K线数据：2h和4h，从2022-01-01至今"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import requests
from datetime import datetime, timezone
from history_cache import init_cache_db, save_ohlcv, get_ohlcv as cache_get_ohlcv

GATEIO_BASE = "https://api.gateio.ws/api/v4"

def gateio_fetch(symbol_base, timeframe, since_ms, limit=1000):
    """从Gate.io获取K线数据 - symbol_base如BTC"""
    pair = f"{symbol_base.upper()}_USDT"
    interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "2h": "2h"}
    interval = interval_map.get(timeframe, timeframe)
    
    all_candles = []
    current_since = since_ms
    
    while True:
        params = {"currency_pair": pair, "interval": interval, "limit": min(limit, 1000)}
        params["from"] = current_since // 1000
        
        try:
            resp = requests.get(f"{GATEIO_BASE}/spot/candlesticks", params=params, timeout=15)
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
            print(f"  Fetched {len(raw)} bars, last={datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
            
            if len(raw) < 1000:
                break
            current_since = last_ts + 1
            time.sleep(0.3)
            
        except Exception as e:
            print(f"  Error: {e}")
            break
    
    return all_candles

def main():
    init_cache_db()
    
    symbol = "BTC"
    symbol_full = "BTC/USDT"
    start_dt = datetime(2022, 1, 1, tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)
    
    timeframes = ["2h", "4h"]
    
    for tf in timeframes:
        print(f"\n{'='*50}")
        print(f"Fetching {symbol_full} {tf} from {start_dt.isoformat()}")
        
        # Check existing data
        existing = cache_get_ohlcv(symbol_full, tf, limit=2)
        if existing:
            print(f"  Existing: {len(existing)} bars, latest ts={existing[-1]['timestamp']} ({datetime.fromtimestamp(existing[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')})")
        
        candles = gateio_fetch(symbol, tf, start_ms)
        print(f"  Total fetched: {len(candles)} bars")
        
        if candles:
            # Deduplicate by timestamp
            seen = set()
            unique = []
            for c in candles:
                if c["timestamp"] not in seen:
                    seen.add(c["timestamp"])
                    unique.append(c)
            candles = unique
            print(f"  After dedup: {len(candles)} bars")
            
            # Save to cache
            save_ohlcv(symbol_full, tf, candles)
            print(f"  Saved to cache")
            
            # Verify
            verify = cache_get_ohlcv(symbol_full, tf, limit=2)
            print(f"  Verified: {len(verify)} bars in cache")
            if verify:
                print(f"  Latest: {datetime.fromtimestamp(verify[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    
    print("\nDone!")

if __name__ == "__main__":
    main()