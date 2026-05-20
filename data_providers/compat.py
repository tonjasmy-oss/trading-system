"""
兼容适配层 - Compatibility Adapter
保持原 crypto_api / stock_api 函数签名不变，
底层切换到 DataProviderFactory（带限流+熔断）。

使用方式（在 live_trading.py 中替换 import）：
  from data_providers.compat import get_crypto_price, get_ohlcv, get_stock
"""

import logging
from typing import Optional, List, Dict

from .factory import DataProviderFactory

logger = logging.getLogger(__name__)

# ── 加密货币兼容接口 ──

def get_crypto_price(symbol: str) -> Optional[Dict]:
    """
    获取加密货币实时价格（兼容原 crypto_api.get_crypto_price 签名）

    Args:
        symbol: 代币代码，如 "BTC", "ETH"

    Returns:
        {"symbol": "BTC", "price": 42000.0, "change_24h_pct": 2.5, ...} 或 None
    """
    provider = DataProviderFactory.get("CRYPTO")
    if not provider:
        return None

    # 兼容符号格式：直接传符号或加 /USDT 后缀
    if "/" not in symbol:
        lookup = f"{symbol}/USDT"
    else:
        lookup = symbol

    result = provider.get_price(lookup)
    if not result:
        return None

    return {
        "symbol": result.get("symbol", symbol),
        "price": result.get("price", 0),
        "change_24h_pct": result.get("change_pct", 0),  # 简化：用 change_pct 近似
        "change_24h": result.get("change", 0),
        "high": result.get("high", 0),
        "low": result.get("low", 0),
        "volume": result.get("volume", "0"),
        "timestamp": result.get("timestamp", ""),
    }


def get_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100) -> Optional[List[List]]:
    """
    获取 K 线数据（兼容原 crypto_api.get_ohlcv 签名）

    Args:
        symbol: 代币代码，如 "BTC", "ETH"
        timeframe: K线周期, "1m", "5m", "1h", "4h", "1d"
        limit: 获取条数

    Returns:
        [[ts, open, high, low, close, volume], ...] 或 None
    """
    provider = DataProviderFactory.get("CRYPTO")
    if not provider:
        return None

    if "/" not in symbol:
        lookup = f"{symbol}/USDT"
    else:
        lookup = symbol

    bars = provider.get_ohlcv(lookup, timeframe, limit)
    if not bars:
        return None

    # 转换为 ccxt 格式: [ts, o, h, l, c, v]
    return [
        [b["timestamp"], b["open"], b["high"], b["low"], b["close"], b["volume"]]
        for b in bars
    ]


# ── 股票兼容接口 ──

def get_stock(symbol: str, market: str = "CN") -> Optional[Dict]:
    """
    获取股票实时行情（兼容原 stock_api.get_stock 签名）

    Args:
        symbol: 股票代码，如 "600000", "AAPL"
        market: 市场, "CN" / "HK" / "US"

    Returns:
        {"symbol": "600000", "name": "浦发银行", "price": 9.08, ...} 或 None
    """
    market_map = {"CN": "CN_STOCK", "HK": "HK_STOCK", "US": "US_STOCK"}
    provider_key = market_map.get(market, "CN_STOCK")
    provider = DataProviderFactory.get(provider_key)
    if not provider:
        return None

    result = provider.get_price(symbol)
    if not result:
        return None

    return {
        "symbol": result.get("symbol", symbol),
        "name": result.get("name", symbol),
        "price": result.get("price", 0),
        "prev_close": result.get("prev_close", 0),
        "change": result.get("change", 0),
        "change_pct": result.get("change_pct", 0),
        "open": result.get("open", 0),
        "high": result.get("high", 0),
        "low": result.get("low", 0),
        "volume": result.get("volume", "0"),
        "timestamp": result.get("timestamp", ""),
    }


# ── 扩展接口（新增） ──

def get_provider_status() -> Dict[str, Dict]:
    """获取所有数据源状态"""
    return DataProviderFactory.get_all_status()


def reset_providers():
    """重置所有数据源（用于故障恢复）"""
    DataProviderFactory.reset_all()
