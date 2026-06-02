"""
交易所工厂 - 统一创建交易所适配器实例

使用方式：
    from exchanges import create_exchange
    
    binance = create_exchange("binance", api_key="xxx", api_secret="yyy")
    okx = create_exchange("okx", api_key="xxx", api_secret="yyy", passphrase="zzz")
"""

from typing import Optional
from .base import ExchangeAdapter, ExchangeError


# 延迟导入避免循环依赖
_exchange_cache: dict = {}


def create_exchange(
    exchange_id: str,
    api_key: str = "",
    api_secret: str = "",
    passphrase: str = "",
    testnet: bool = False,
    proxy: Optional[str] = None,
    mode: str = "futures",
    **kwargs
) -> ExchangeAdapter:
    """
    工厂函数：根据 exchange_id 创建对应的适配器
    
    Args:
        exchange_id: 交易所标识 (binance, okx, bybit, hyperliquid)
        api_key: API Key
        api_secret: API Secret
        passphrase: 密码（部分交易所需要）
        testnet: 是否使用测试网
        proxy: 代理地址
        mode: 交易模式 ("spot", "futures")
    
    Returns:
        ExchangeAdapter 实例
    """
    cache_key = f"{exchange_id}:{testnet}:{mode}"
    if cache_key in _exchange_cache:
        return _exchange_cache[cache_key]
    
    if exchange_id == "binance":
        from .binance import BinanceAdapter
        adapter = BinanceAdapter(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            proxy=proxy,
            mode=mode
        )
    elif exchange_id == "okx":
        from .okx import OKXAdapter
        adapter = OKXAdapter(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            testnet=testnet,
            proxy=proxy
        )
    elif exchange_id == "bybit":
        from .bybit import BybitAdapter
        adapter = BybitAdapter(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            proxy=proxy
        )
    elif exchange_id == "hyperliquid":
        from .hyperliquid import HyperliquidAdapter
        adapter = HyperliquidAdapter(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            proxy=proxy
        )
    else:
        raise ExchangeError(
            "Unsupported exchange",
            f"不支持的交易所: {exchange_id}",
            "UNSUPPORTED_EXCHANGE"
        )
    
    _exchange_cache[cache_key] = adapter
    return adapter


def clear_exchange_cache():
    """清除交易所实例缓存"""
    global _exchange_cache
    _exchange_cache = {}