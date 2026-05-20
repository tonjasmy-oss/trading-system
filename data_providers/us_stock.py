"""
美股数据源 - US Stock Data Provider
基于 Yahoo Finance / yfinance
"""

import time
import logging
from typing import Optional, List

from .base import BaseDataProvider, PriceData, OHLCVBar
from .rate_limiter import TokenBucketRateLimiter
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    _YFINANCE_AVAILABLE = True
except ImportError:
    yf = None
    _YFINANCE_AVAILABLE = False


class USStockProvider(BaseDataProvider):
    """美股数据源"""

    def __init__(self, rate_limit=5.0, rate_burst=8):
        super().__init__(name="us_stock")
        self.rate_limiter = TokenBucketRateLimiter(rate=rate_limit, burst=rate_burst, name="us_stock")
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=120.0, name="us_stock")

    def _fetch_price(self, symbol: str) -> Optional[PriceData]:
        if not self.circuit_breaker.allow_request() or not self.rate_limiter.acquire():
            return None
        if not _YFINANCE_AVAILABLE:
            logger.warning("yfinance not installed for US stock data")
            return None
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            price = info.get("regularMarketPrice") or info.get("currentPrice") or 0
            prev_close = info.get("previousClose") or price
            self.circuit_breaker.on_success()
            return PriceData(
                symbol=symbol, market="US", name=info.get("shortName", symbol),
                price=float(price), prev_close=float(prev_close),
                change=float(price) - float(prev_close) if price else 0,
                change_pct=((float(price) - float(prev_close)) / float(prev_close) * 100) if prev_close else 0,
                timestamp=str(int(time.time() * 1000)),
            )
        except Exception as e:
            logger.error(f"[USStock] {symbol} failed: {e}")
            self.circuit_breaker.on_failure()
            return None

    def _fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 100) -> Optional[List[OHLCVBar]]:
        if not self.circuit_breaker.allow_request() or not self.rate_limiter.acquire():
            return None
        if not _YFINANCE_AVAILABLE:
            return None
        try:
            hist = yf.download(symbol, period=f"{limit}d", progress=False)
            if hist.empty:
                return None
            bars = []
            for idx, row in hist.iterrows():
                ts = int(idx.timestamp() * 1000)
                bars.append(OHLCVBar(timestamp=ts, open=float(row["Open"]), high=float(row["High"]),
                                     low=float(row["Low"]), close=float(row["Close"]), volume=float(row["Volume"])))
            self.circuit_breaker.on_success()
            return bars
        except Exception as e:
            logger.error(f"[USStock] ohlcv {symbol} failed: {e}")
            self.circuit_breaker.on_failure()
            return None

    def get_market_type(self) -> str:
        return "US_STOCK"
