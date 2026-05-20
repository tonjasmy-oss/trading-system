"""
数据源抽象层 - Data Provider Abstraction Layer

统一封装多市场数据接入，提供：
  - BaseProvider 抽象接口
  - TokenBucket 速率限制器
  - CircuitBreaker 熔断器
  - Factory 工厂模式选取数据源

使用方式：
  from data_providers import DataProviderFactory
  provider = DataProviderFactory.get("CRYPTO")
  price = provider.get_price("BTC/USDT")
"""

from .base import BaseDataProvider
from .factory import DataProviderFactory
from .rate_limiter import TokenBucketRateLimiter
from .circuit_breaker import CircuitBreaker

__all__ = [
    "BaseDataProvider",
    "DataProviderFactory",
    "TokenBucketRateLimiter",
    "CircuitBreaker",
]
