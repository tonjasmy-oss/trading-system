"""
熔断器 - Circuit Breaker Pattern

当数据源连续失败达到阈值时，自动「熔断」暂停调用，
避免持续浪费请求配额和阻塞调用方。

状态机：CLOSED → OPEN → HALF_OPEN → CLOSED

使用方式：
  breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
  if breaker.allow_request():
      try:
          result = api_call()
          breaker.on_success()
      except Exception:
          breaker.on_failure()
"""

import time
import threading
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"         # 正常通行
    OPEN = "open"             # 熔断中，拒绝请求
    HALF_OPEN = "half_open"   # 探测中，允许有限请求


class CircuitBreaker:
    """
    熔断器

    Args:
        failure_threshold: 连续失败多少次后熔断（默认 5）
        recovery_timeout:  熔断后多少秒进入半开状态（默认 60）
        name:              名称
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        name: str = "default",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._last_state_change: float = time.monotonic()
        self._lock = threading.Lock()
        # 统计
        self._total_requests = 0
        self._rejected_requests = 0
        self._success_count = 0

    def allow_request(self) -> bool:
        """
        检查是否允许发起请求

        Returns:
            True 表示可以发起请求
        """
        with self._lock:
            self._total_requests += 1

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._last_state_change
                if elapsed >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._last_state_change = time.monotonic()
                    return True
                self._rejected_requests += 1
                return False

            # HALF_OPEN: 允许请求（探测）
            return True

    def on_success(self) -> None:
        """请求成功后调用"""
        with self._lock:
            self._failure_count = 0
            self._success_count += 1
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._last_state_change = time.monotonic()

    def on_failure(self) -> None:
        """请求失败后调用"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._last_state_change = time.monotonic()
            elif (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._last_state_change = time.monotonic()

    def get_state(self) -> str:
        """返回当前状态"""
        with self._lock:
            return self._state.value

    def get_stats(self) -> dict:
        """返回熔断器统计"""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "total_requests": self._total_requests,
                "rejected_requests": self._rejected_requests,
                "success_count": self._success_count,
                "reject_rate": (
                    self._rejected_requests / max(self._total_requests, 1)
                ),
            }

    def reset(self) -> None:
        """手动重置熔断器"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_state_change = time.monotonic()
