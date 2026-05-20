"""
外汇数据源 - Forex Data Provider
基于 Yahoo Finance 或免费 API
"""

import time
import logging
import requests
from typing import Optional, List

from .base import BaseDataProvider, PriceData, OHLCVBar
from .rate_limiter import TokenBucketRateLimiter
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class ForexProvider(BaseDataProvider):
    """外汇数据源"""

    FOREX_PAIRS = {
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD",
        "USD/CAD", "NZD/USD", "EUR/JPY", "GBP/JPY",
    }

    def __init__(self, rate_limit=5.0, rate_burst=8):
        super().__init__(name="forex")
        self.rate_limiter = TokenBucketRateLimiter(rate=rate_limit, burst=rate_burst, name="forex")
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=120.0, name="forex")

    def _fetch_price(self, symbol: str) -> Optional[PriceData]:
        if not self.circuit_breaker.allow_request() or not self.rate_limiter.acquire():
            return None
        try:
            pair = symbol.replace("/", "").upper()
            if "/" not in symbol:
                pair = symbol[:3] + symbol[-3:]
            url = f"https://financialmodelingprep.com/api/v3/quote/{pair}?apikey=demo"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                self.circuit_breaker.on_failure()
                return None
            data = resp.json()
            if not data or not isinstance(data, list):
                self.circuit_breaker.on_failure()
                return None
            d = data[0]
            self.circuit_breaker.on_success()
            return PriceData(
                symbol=symbol, market="FOREX", name=d.get("name", symbol),
                price=float(d.get("price", 0)), prev_close=float(d.get("previousClose", 0)),
                change=float(d.get("change", 0)), change_pct=float(d.get("changesPercentage", 0)),
                timestamp=str(int(time.time() * 1000)),
            )
        except Exception as e:
            logger.error(f"[Forex] {symbol} failed: {e}")
            self.circuit_breaker.on_failure()
            return None

    def _fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 100) -> Optional[List[OHLCVBar]]:
        # Forex OHLCV via yfinance fallback
        try:
            import yfinance as yf
            ticker = symbol.replace("/", "") + "=X"
            hist = yf.download(ticker, period=f"{limit}d", progress=False)
            if hist.empty:
                return None
            bars = []
            for idx, row in hist.iterrows():
                ts = int(idx.timestamp() * 1000)
                bars.append(OHLCVBar(timestamp=ts, open=float(row["Open"]), high=float(row["High"]),
                                     low=float(row["Low"]), close=float(row["Close"]), volume=float(row["Volume"])))
            return bars
        except Exception:
            return None

    def get_market_type(self) -> str:
        return "FOREX"
