"""
Hyperliquid DEX 适配器

Hyperliquid 是一个去中心化交易所，通过钱包签名交互。
"""

from typing import Optional, Literal
from .base import ExchangeAdapter, Balance, Position, Order, Ticker, Kline, ExchangeError


class HyperliquidAdapter(ExchangeAdapter):
    """Hyperliquid DEX 适配器"""
    
    exchange_id = "hyperliquid"
    _base_url = "https://api.hyperliquid.xyz"
    _testnet_url = "https://api.hyperliquid-testnet.xyz"
    
    @property
    def base_url(self) -> str:
        return self._testnet_url if self.testnet else self._base_url
    
    def _sign_request(self, params: dict) -> dict:
        """Hyperliquid 使用离线签名，需要钱包交互"""
        # TODO: 实现 Hyperliquid 签名
        return params
    
    def get_balance(self) -> list[Balance]:
        # TODO: 实现 Hyperliquid 余额查询
        return []
    
    def get_positions(self) -> list[Position]:
        # TODO: 实现 Hyperliquid 持仓查询
        return []
    
    def get_ticker(self, symbol: str) -> Ticker:
        # TODO: 实现 Hyperliquid 行情查询
        return Ticker(symbol=symbol, last_price=0, bid_price=0, ask_price=0, volume_24h=0, change_24h_pct=0)
    
    def place_order(self, symbol: str, side: Literal["BUY", "SELL"], order_type: Literal["MARKET", "LIMIT"], quantity: float, price: Optional[float] = None, stop_price: Optional[float] = None, reduce_only: bool = False, position_side: Optional[Literal["LONG", "SHORT"]] = None) -> Order:
        raise NotImplementedError("Hyperliquid adapter not fully implemented")
    
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        raise NotImplementedError("Hyperliquid adapter not fully implemented")
    
    def get_orders(self, symbol: str, limit: int = 50) -> list[Order]:
        raise NotImplementedError("Hyperliquid adapter not fully implemented")
    
    def get_klines(self, symbol: str, timeframe: str = "4h", limit: int = 200, start_time: Optional[int] = None, end_time: Optional[int] = None) -> list[Kline]:
        raise NotImplementedError("Hyperliquid adapter not fully implemented")