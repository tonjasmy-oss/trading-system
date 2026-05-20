"""
通用工具模块
借鉴 QuantDinger 的 cache.py / http.py / logger.py 设计
提供：内存缓存 / HTTP 重试 / 结构化日志 / 时间工具
"""

import os
import time
import json
import hashlib
import logging
import threading
from typing import Optional, Any, Callable
from datetime import datetime, timezone, timedelta

import requests

# ============================================================
# 北京时区
# ============================================================
TZ_BEIJING = timezone(timedelta(hours=8))


def now_beijing() -> datetime:
    """获取北京时间"""
    return datetime.now(TZ_BEIJING)


def ts_beijing() -> str:
    """获取北京时间字符串 ISO 格式"""
    return now_beijing().isoformat(timespec="seconds")


def ts_compact() -> str:
    """紧凑时间戳 yyyyMMdd_HHmmss"""
    return now_beijing().strftime("%Y%m%d_%H%M%S")


# ============================================================
# 内存缓存（借鉴 QuantDinger MemoryCache）
# ============================================================

class MemoryCache:
    """线程安全的内存缓存，带 TTL"""

    def __init__(self, name: str = "default"):
        self.name = name
        self._store: dict = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            value, expiry = entry
            if expiry and time.time() > expiry:
                del self._store[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: Any, ttl: int = 300):
        with self._lock:
            expiry = time.time() + ttl if ttl > 0 else 0
            self._store[key] = (value, expiry)

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "name": self.name,
                "entries": len(self._store),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": f"{self.hits / total * 100:.1f}%" if total > 0 else "N/A",
            }

    def __len__(self):
        with self._lock:
            return len(self._store)


# 预置缓存实例
ohlcv_cache = MemoryCache("ohlcv")
price_cache = MemoryCache("price")
signal_cache = MemoryCache("signal")


# ============================================================
# HTTP 重试工具（借鉴 QuantDinger http.py）
# ============================================================

def http_get(
    url: str,
    params: dict = None,
    headers: dict = None,
    timeout: int = 15,
    retries: int = 2,
    backoff: float = 1.0,
) -> Optional[requests.Response]:
    """带重试的 HTTP GET"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                wait = backoff * (2 ** attempt)
                time.sleep(wait)
    logging.getLogger(__name__).warning(f"HTTP GET 失败 [{url[:60]}]: {last_err}")
    return None


def http_post(
    url: str,
    json_data: dict = None,
    headers: dict = None,
    timeout: int = 15,
    retries: int = 1,
) -> Optional[requests.Response]:
    """带重试的 HTTP POST"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=json_data, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0)
    logging.getLogger(__name__).warning(f"HTTP POST 失败 [{url[:60]}]: {last_err}")
    return None


# ============================================================
# 结构化日志（借鉴 QuantDinger logger.py）
# ============================================================

class TradeLogger:
    """交易专用日志器"""

    def __init__(self, log_dir: str = None):
        self.log_dir = log_dir or os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(self.log_dir, exist_ok=True)
        self._logger = logging.getLogger("trade")

    def log_trade(self, event: str, **kwargs):
        """记录交易事件"""
        entry = {
            "ts": ts_beijing(),
            "event": event,
            **kwargs,
        }
        self._logger.info(json.dumps(entry, ensure_ascii=False))

    def log_signal(self, symbol: str, signal: str, price: float, **kwargs):
        self.log_trade("signal", symbol=symbol, signal=signal, price=price, **kwargs)

    def log_execution(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        self.log_trade("execution", symbol=symbol, side=side, price=price, qty=qty, **kwargs)

    def log_risk(self, level: str, message: str, **kwargs):
        self.log_trade("risk", level=level, message=message, **kwargs)


# ============================================================
# 安全哈希
# ============================================================

def sha256(s: str) -> str:
    """字符串 SHA-256"""
    return hashlib.sha256(s.encode()).hexdigest()


def safe_truncate(s: str, max_len: int = 200) -> str:
    """安全截断字符串（用于日志输出）"""
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."


# ============================================================
# 数值工具
# ============================================================

def pct_change(old: float, new: float) -> float:
    """百分比变化"""
    if old == 0:
        return 0.0
    return (new - old) / old * 100


def clamp(value: float, lo: float, hi: float) -> float:
    """夹逼值"""
    return max(lo, min(hi, value))


if __name__ == "__main__":
    # 测试缓存
    c = MemoryCache("test")
    c.set("key1", "value1", ttl=2)
    assert c.get("key1") == "value1"
    time.sleep(2.1)
    assert c.get("key1") is None
    print("MemoryCache: OK")

    # 测试时间
    print(f"北京时间: {ts_beijing()}")
    print(f"紧凑时间: {ts_compact()}")

    # 测试数值
    print(f"pct_change(100, 105) = {pct_change(100, 105):.2f}%")
    print(f"clamp(150, 0, 100) = {clamp(150, 0, 100)}")
