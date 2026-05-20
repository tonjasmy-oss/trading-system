"""
令牌桶速率限制器 - Token Bucket Rate Limiter

防止并发请求触发交易所 API 频率限制。
每个数据源实例独立一个桶，可按交易所配置速率。

使用方式：
  limiter = TokenBucketRateLimiter(rate=10, burst=15)  # 每秒10次，突发15次
  if limiter.acquire():
      api_call()
"""

import time
import threading
from typing import Optional


class TokenBucketRateLimiter:
    """
    令牌桶速率限制器

    算法：每隔 1/rate 秒补充一个令牌，最多 burst 个令牌。
    每次 acquire() 消耗一个令牌，无令牌时返回 False。

    Args:
        rate:  每秒允许的请求数（默认 10）
        burst: 突发允许的最大请求数（默认 15）
        name:  名称（用于日志）
    """

    def __init__(self, rate: float = 10.0, burst: int = 15, name: str = "default"):
        self.rate = max(rate, 0.1)      # 最低 0.1 req/s
        self.burst = max(burst, 1)
        self.name = name
        self._tokens = float(burst)     # 初始满桶
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        # 统计
        self._total_requests = 0
        self._throttled_requests = 0

    def _refill(self) -> None:
        """按时间补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self.rate
        self._tokens = min(self._tokens + new_tokens, self.burst)
        self._last_refill = now

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """
        尝试获取一个令牌

        Args:
            timeout: 最大等待时间（秒），None 表示不等待

        Returns:
            True 如果获取成功，False 如果被限流
        """
        with self._lock:
            self._total_requests += 1
            self._refill()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True

            if timeout is None:
                self._throttled_requests += 1
                return False

            # 等待模式
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                wait = max((1.0 - self._tokens) / self.rate, 0.01)
                time.sleep(min(wait, deadline - time.monotonic()))
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True

            self._throttled_requests += 1
            return False

    def get_stats(self) -> dict:
        """返回速率统计"""
        with self._lock:
            return {
                "name": self.name,
                "rate": self.rate,
                "burst": self.burst,
                "tokens": round(self._tokens, 2),
                "total_requests": self._total_requests,
                "throttled_requests": self._throttled_requests,
                "throttle_rate": (
                    self._throttled_requests / max(self._total_requests, 1)
                ),
            }

    def reset_stats(self) -> None:
        """重置统计计数器"""
        with self._lock:
            self._total_requests = 0
            self._throttled_requests = 0
