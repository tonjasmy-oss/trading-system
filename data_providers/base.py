"""
数据源抽象基类 - 所有数据源必须实现此接口
参考 QuantDinger 的 app/data_sources/base.py 设计
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import time


@dataclass
class OHLCVBar:
    """标准化 OHLCV K线数据"""
    timestamp: int       # 毫秒时间戳
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    @classmethod
    def from_list(cls, row: list) -> "OHLCVBar":
        """从 ccxt 标准格式 [ts, o, h, l, c, v] 构造"""
        return cls(
            timestamp=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )


@dataclass
class PriceData:
    """标准化实时价格数据"""
    symbol: str
    market: str
    name: str
    price: float
    prev_close: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: str = "0"
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "name": self.name,
            "price": self.price,
            "prev_close": self.prev_close,
            "change": self.change,
            "change_pct": self.change_pct,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "timestamp": self.timestamp,
        }


class BaseDataProvider(ABC):
    """
    数据源抽象基类

    所有数据源（加密货币、A股、美股、港股、外汇等）必须实现此接口。
    子类只需覆盖 _fetch_price / _fetch_ohlcv 两个核心方法，
    基类自动处理限流、熔断和重试逻辑。
    """

    def __init__(self, name: str = "base"):
        self.name = name
        self._last_error: Optional[str] = None
        self._last_error_time: float = 0.0

    # ── 子类必须实现 ──

    @abstractmethod
    def _fetch_price(self, symbol: str) -> Optional[PriceData]:
        """获取实时价格（子类实现）"""
        ...

    @abstractmethod
    def _fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> Optional[List[OHLCVBar]]:
        """获取 K 线数据（子类实现）"""
        ...

    @abstractmethod
    def get_market_type(self) -> str:
        """返回市场类型标识，如 'CRYPTO', 'CN_STOCK', 'US_STOCK'"""
        ...

    # ── 公共接口 ──

    def get_price(self, symbol: str) -> Optional[dict]:
        """获取实时价格（公共接口，带错误记录）"""
        result = self._fetch_price(symbol)
        if result is None:
            self._last_error = f"获取 {symbol} 价格失败"
            self._last_error_time = time.time()
            return None
        return result.to_dict()

    def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> Optional[List[dict]]:
        """获取 K 线数据（公共接口）"""
        result = self._fetch_ohlcv(symbol, timeframe, limit)
        if result is None:
            self._last_error = f"获取 {symbol} K线失败"
            self._last_error_time = time.time()
            return None
        return [bar.to_dict() for bar in result]

    def is_available(self) -> bool:
        """检查数据源是否可用"""
        if self._last_error_time and (time.time() - self._last_error_time) < 30:
            return False  # 30秒内有错误，标记为不可用
        return True

    def get_status(self) -> dict:
        """返回数据源状态"""
        return {
            "name": self.name,
            "market": self.get_market_type(),
            "available": self.is_available(),
            "last_error": self._last_error,
            "last_error_time": self._last_error_time,
        }
