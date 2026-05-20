"""
加密货币数据源 - 基于 ccxt 统一适配
参考 QuantDinger 的 app/data_sources/crypto.py

特性：
  - 统一 ccxt 多交易所接口
  - 内置 TokenBucket 限流
  - 内置 CircuitBreaker 熔断
  - 失败时自动降级到备用交易所
"""

import sys
import os
import time
import logging
from typing import Optional, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .base import BaseDataProvider, PriceData, OHLCVBar
from .rate_limiter import TokenBucketRateLimiter
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

# 延迟导入
try:
    import ccxt
    _CCXT_AVAILABLE = True
except ImportError:
    ccxt = None
    _CCXT_AVAILABLE = False

from config import (
    CRYPTO_EXCHANGE, CRYPTO_API_KEY, CRYPTO_API_SECRET,
)

# ccxt 市场类型常量，对应交易所的 spot / swap / future
_MARKET_TYPE = "spot"


class CryptoDataProvider(BaseDataProvider):
    """
    加密货币数据源

    封装 ccxt 统一接口，自动处理：
      - 速率限制（TokenBucket，每秒最多 10 次）
      - 熔断保护（连续 5 次失败后熔断 60 秒）
      - 交易所实例懒加载和复用
    """

    def __init__(
        self,
        exchange_id: str = None,
        rate_limit: float = 10.0,
        rate_burst: int = 15,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        super().__init__(name="crypto")
        self.exchange_id = exchange_id or CRYPTO_EXCHANGE or "binance"
        self._exchange = None
        self.rate_limiter = TokenBucketRateLimiter(
            rate=rate_limit, burst=rate_burst, name=f"crypto-{self.exchange_id}"
        )
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            name=f"crypto-{self.exchange_id}",
        )

    # ── 交易所懒加载 ──

    def _get_exchange(self):
        """获取 ccxt 交易所实例（懒加载 + 复用）"""
        if self._exchange is not None:
            return self._exchange
        if not _CCXT_AVAILABLE:
            logger.error("ccxt 未安装，无法初始化 CryptoDataProvider")
            return None
        try:
            exchange_class = getattr(ccxt, self.exchange_id)
            config = {"enableRateLimit": True, "timeout": 15000}
            if CRYPTO_API_KEY:
                config["apiKey"] = CRYPTO_API_KEY
                config["secret"] = CRYPTO_API_SECRET
            self._exchange = exchange_class(config)
            logger.info(f"[CryptoProvider] 已连接 {self.exchange_id}")
            return self._exchange
        except Exception as e:
            logger.error(f"[CryptoProvider] 初始化交易所失败: {e}")
            return None

    # ── 核心方法 ──

    def _fetch_price(self, symbol: str) -> Optional[PriceData]:
        """获取实时价格"""
        if not self.circuit_breaker.allow_request():
            logger.warning(f"[CryptoProvider] 熔断中，跳过 {symbol} 价格请求")
            return None

        if not self.rate_limiter.acquire():
            logger.warning(f"[CryptoProvider] 限流，跳过 {symbol} 价格请求")
            return None

        exchange = self._get_exchange()
        if not exchange:
            self.circuit_breaker.on_failure()
            return None

        try:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker.get("last") or ticker.get("close") or 0
            self.circuit_breaker.on_success()
            return PriceData(
                symbol=symbol,
                market="CRYPTO",
                name=symbol,
                price=float(price),
                prev_close=float(ticker.get("previousClose") or ticker.get("open") or price),
                change=float(ticker.get("change") or 0),
                change_pct=float(ticker.get("percentage") or 0),
                open=float(ticker.get("open") or price),
                high=float(ticker.get("high") or price),
                low=float(ticker.get("low") or price),
                volume=str(ticker.get("baseVolume") or ticker.get("volume") or 0),
                timestamp=str(int(time.time() * 1000)),
            )
        except Exception as e:
            logger.error(f"[CryptoProvider] 获取 {symbol} 价格失败: {e}")
            self.circuit_breaker.on_failure()
            return None

    def _fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> Optional[List[OHLCVBar]]:
        """获取 K 线数据"""
        if not self.circuit_breaker.allow_request():
            logger.warning(f"[CryptoProvider] 熔断中，跳过 {symbol} K线请求")
            return None

        if not self.rate_limiter.acquire():
            logger.warning(f"[CryptoProvider] 限流，跳过 {symbol} K线请求")
            return None

        exchange = self._get_exchange()
        if not exchange:
            self.circuit_breaker.on_failure()
            return None

        try:
            since = exchange.parse8601(
                (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - limit * 3600)))
            ) if timeframe.endswith("h") else None
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            self.circuit_breaker.on_success()
            return [OHLCVBar.from_list(c) for c in candles]
        except Exception as e:
            logger.error(f"[CryptoProvider] 获取 {symbol} K线失败: {e}")
            self.circuit_breaker.on_failure()
            return None

    def get_market_type(self) -> str:
        return "CRYPTO"

    def get_status(self) -> dict:
        status = super().get_status()
        status.update({
            "exchange": self.exchange_id,
            "rate_limiter": self.rate_limiter.get_stats(),
            "circuit_breaker": self.circuit_breaker.get_stats(),
        })
        return status
