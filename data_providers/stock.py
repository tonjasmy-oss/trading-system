"""
股票数据源 - 封装 A股/港股/美股 行情获取
参考 QuantDinger 的 app/data_sources/cn_stock.py + hk_stock.py + us_stock.py

数据源：
  - A股：新浪财经 / 东方财富
  - 港股：新浪财经
  - 美股：新浪财经 / Yahoo Finance
"""

import sys
import os
import time
import logging
import requests
from typing import Optional, List, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .base import BaseDataProvider, PriceData, OHLCVBar
from .rate_limiter import TokenBucketRateLimiter
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

SINA_BASE = "https://hq.sinajs.cn"
EAST_MONEY_BASE = "https://push2.eastmoney.com"


class StockDataProvider(BaseDataProvider):
    """
    股票数据源（A股 / 港股 / 美股）

    自动根据 symbol 前缀判断市场：
      - 6xxxxx -> CN (上交所)
      - 0xxxxx / 3xxxxx -> CN (深交所)
      - sh600000 / sz000001 -> CN
      - 港股代码（5位） -> HK
      - 美股代码（字母） -> US
    """

    def __init__(
        self,
        rate_limit: float = 5.0,
        rate_burst: int = 8,
        failure_threshold: int = 5,
        recovery_timeout: float = 120.0,
    ):
        super().__init__(name="stock")
        self.rate_limiter = TokenBucketRateLimiter(
            rate=rate_limit, burst=rate_burst, name="stock"
        )
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            name="stock",
        )
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
        })

    @staticmethod
    def _classify_market(symbol: str) -> str:
        """根据代码判断市场"""
        s = symbol.strip().upper()
        if s.startswith(("SH", "SZ")):
            return "CN"
        if s.startswith("6") and len(s) == 6:
            return "CN"
        if s.startswith(("0", "3")) and len(s) == 6:
            return "CN"
        if s.isdigit() and len(s) == 5:
            return "HK"
        if s.isalpha() and 1 <= len(s) <= 5:
            return "US"
        return "CN"

    def _parse_sina_response(self, text: str, symbol: str, market: str) -> Optional[Dict]:
        """解析新浪股票行情响应"""
        if not text or "FAILED" in text:
            return None

        idx = text.find('="')
        if idx >= 0:
            data_str = text[idx + 2:]
            # Remove trailing quote/semicolon
            data_str = data_str.rstrip('";\n ')
        else:
            data_str = text

        fields = data_str.split(",")
        if len(fields) < 4:
            return None

        name = fields[0]
        price = float(fields[3]) if fields[3] else 0.0
        prev_close = float(fields[2]) if len(fields) > 2 and fields[2] else price
        open_price = float(fields[1]) if fields[1] else price
        high = float(fields[4]) if len(fields) > 4 and fields[4] else price
        low = float(fields[5]) if len(fields) > 5 and fields[5] else price
        volume = fields[8] if len(fields) > 8 else "0"

        change = price - prev_close if price and prev_close else 0
        change_pct = (change / prev_close * 100) if prev_close else 0

        return {
            "symbol": symbol,
            "market": market,
            "name": name,
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "timestamp": str(int(time.time() * 1000)),
        }

    def _fetch_sina_stock(self, symbol: str, market: str) -> Optional[Dict]:
        """从新浪获取股票行情"""
        try:
            if market == "CN":
                if symbol.lower().startswith(("sh", "sz")):
                    sina_code = symbol.lower()
                elif symbol.startswith("6"):
                    sina_code = "sh" + symbol
                else:
                    sina_code = "sz" + symbol
            elif market == "HK":
                sina_code = "rt_hk" + symbol
            elif market == "US":
                sina_code = "gb_" + symbol.lower()
            else:
                return None

            url = SINA_BASE + "/list=" + sina_code
            resp = self._session.get(
                url,
                headers={"Referer": "https://finance.sina.com.cn"},
                timeout=10,
            )
            if resp.status_code != 200:
                return None

            return self._parse_sina_response(resp.text, symbol, market)
        except Exception as e:
            logger.error("[StockProvider] sina {} ({}) failed: {}".format(symbol, market, e))
            return None

    def _fetch_price(self, symbol: str) -> Optional[PriceData]:
        """获取实时价格"""
        if not self.circuit_breaker.allow_request():
            logger.warning("[StockProvider] breaker open, skip {}".format(symbol))
            return None
        if not self.rate_limiter.acquire():
            logger.warning("[StockProvider] rate limited, skip {}".format(symbol))
            return None

        market = self._classify_market(symbol)
        data = self._fetch_sina_stock(symbol, market)
        if data is None:
            self.circuit_breaker.on_failure()
            return None

        self.circuit_breaker.on_success()
        return PriceData(
            symbol=data["symbol"],
            market=data["market"],
            name=data["name"],
            price=data["price"],
            prev_close=data["prev_close"],
            change=data["change"],
            change_pct=data["change_pct"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            volume=data["volume"],
            timestamp=data["timestamp"],
        )

    def _fetch_ohlcv(
        self, symbol: str, timeframe: str = "1d", limit: int = 100
    ) -> Optional[List[OHLCVBar]]:
        """获取股票 K 线（东方财富）"""
        if not self.circuit_breaker.allow_request():
            return None
        if not self.rate_limiter.acquire():
            return None

        try:
            market = self._classify_market(symbol)
            secid_map = {
                "CN": ("1." + symbol) if symbol.startswith("6") else ("0." + symbol),
                "HK": "116." + symbol,
                "US": "105." + symbol,
            }
            secid = secid_map.get(market, "1." + symbol)
            url = (
                EAST_MONEY_BASE + "/api/qt/stock/kline/get"
                "?secid=" + secid + "&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56,f57"
                "&klt=101&fqt=1&end=20500101&lmt=" + str(limit)
            )
            resp = self._session.get(url, timeout=10)
            data = resp.json()
            if data.get("data") and data["data"].get("klines"):
                bars = []
                for line in data["data"]["klines"]:
                    parts = line.split(",")
                    if len(parts) >= 6:
                        ts = int(time.mktime(
                            time.strptime(parts[0], "%Y-%m-%d")
                        )) * 1000
                        bars.append(OHLCVBar(
                            timestamp=ts,
                            open=float(parts[1]),
                            close=float(parts[2]),
                            high=float(parts[3]),
                            low=float(parts[4]),
                            volume=float(parts[5]),
                        ))
                self.circuit_breaker.on_success()
                return bars
            self.circuit_breaker.on_failure()
            return None
        except Exception as e:
            logger.error("[StockProvider] ohlcv {} failed: {}".format(symbol, e))
            self.circuit_breaker.on_failure()
            return None

    def get_market_type(self) -> str:
        return "STOCK"

    def get_status(self) -> dict:
        status = super().get_status()
        status.update({
            "rate_limiter": self.rate_limiter.get_stats(),
            "circuit_breaker": self.circuit_breaker.get_stats(),
        })
        return status
