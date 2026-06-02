"""
Binance 交易所适配器

支持：
- 现货交易
- USDT-M 合约（Futures）
- 测试网
"""

import time
import hmac
import hashlib
from typing import Optional, Literal
from .base import (
    ExchangeAdapter, Balance, Position, Order, Ticker, Kline, ExchangeError
)


class BinanceAdapter(ExchangeAdapter):
    """Binance 交易所适配器"""
    
    exchange_id = "binance"
    _base_url = "https://api.binance.com"
    _usdt_future_url = "https://fapi.binance.com"
    _testnet_futures_url = "https://testnet.binancefuture.com"
    
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        testnet: bool = False,
        proxy: Optional[str] = None,
        mode: Literal["spot", "futures"] = "futures"
    ):
        super().__init__(api_key, api_secret, passphrase, testnet, proxy)
        self.mode = mode  # "spot" or "futures"
    
    @property
    def base_url(self) -> str:
        if self.mode == "futures" and self.testnet:
            return self._testnet_futures_url
        elif self.mode == "futures":
            return self._usdt_future_url
        return self._base_url
    
    def _sign_request(self, params: dict) -> dict:
        """HMAC SHA256 签名"""
        timestamp = int(time.time() * 1000)
        params["timestamp"] = timestamp
        params["signature"] = hmac.new(
            self.api_secret.encode("utf-8"),
            "&".join([f"{k}={v}" for k, v in sorted(params.items())]).encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return params
    
    def _headers(self) -> dict:
        return {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }
    
    # ─── 余额查询 ───────────────────────────────────────────────
    
    def get_balance(self) -> list[Balance]:
        """查询账户余额（现货或合约）"""
        if self.mode == "spot":
            return self._spot_balance()
        else:
            return self._futures_balance()
    
    def _spot_balance(self) -> list[Balance]:
        import requests
        params = self._sign_request({})
        resp = requests.get(
            f"{self._base_url}/api/v3/account",
            params=params,
            headers=self._headers(),
            proxies={"https": self.proxy} if self.proxy else None
        )
        data = self._handle_response(resp)
        
        balances = []
        for b in data.get("balances", []):
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            if free > 0 or locked > 0:
                balances.append(Balance(
                    asset=b["asset"],
                    free=free,
                    locked=locked,
                    total=free + locked
                ))
        return balances
    
    def _futures_balance(self) -> list[Balance]:
        import requests
        params = self._sign_request({})
        resp = requests.get(
            f"{self._usdt_future_url}/fapi/v2/account",
            params=params,
            headers=self._headers(),
            proxies={"https": self.proxy} if self.proxy else None
        )
        data = self._handle_response(resp)
        
        return [Balance(
            asset="USDT",
            free=float(data.get("availableBalance", 0)),
            locked=float(data.get("totalInitialMargin", 0)),
            total=float(data.get("totalWalletBalance", 0))
        )]
    
    # ─── 持仓查询 ───────────────────────────────────────────────
    
    def get_positions(self) -> list[Position]:
        """查询持仓"""
        if self.mode == "spot":
            return []  # 现货无持仓概念
        return self._futures_positions()
    
    def _futures_positions(self) -> list[Position]:
        import requests
        params = self._sign_request({})
        resp = requests.get(
            f"{self._usdt_future_url}/fapi/v2/positionRisk",
            params=params,
            headers=self._headers(),
            proxies={"https": self.proxy} if self.proxy else None
        )
        data = self._handle_response(resp)
        
        positions = []
        for pos in data:
            qty = float(pos.get("positionAmt", 0))
            if qty == 0:
                continue
            
            entry_price = float(pos.get("entryPrice", 0))
            mark_price = float(pos.get("markPrice", 0))
            unreal_pnl = float(pos.get("unrealizedProfit", 0))
            leverage = int(pos.get("leverage", 1))
            
            positions.append(Position(
                symbol=pos["symbol"],
                side="LONG" if qty > 0 else "SHORT",
                quantity=abs(qty),
                entry_price=entry_price,
                mark_price=mark_price,
                unrealized_pnl=unreal_pnl,
                unrealized_pnl_pct=(unreal_pnl / (entry_price * abs(qty))) * 100 if entry_price and qty else 0,
                leverage=leverage,
                liquidation_price=float(pos.get("liquidationPrice", 0)) or None,
            ))
        return positions
    
    # ─── 行情 ──────────────────────────────────────────────────
    
    def get_ticker(self, symbol: str) -> Ticker:
        import requests
        resp = requests.get(
            f"{self._base_url}/api/v3/ticker/24hr",
            params={"symbol": symbol.replace("/", "")},
            proxies={"https": self.proxy} if self.proxy else None
        )
        data = self._handle_response(resp)
        
        return Ticker(
            symbol=symbol,
            last_price=float(data["lastPrice"]),
            bid_price=float(data["bidPrice"]),
            ask_price=float(data["askPrice"]),
            volume_24h=float(data["volume"]),
            change_24h_pct=float(data["priceChangePercent"])
        )
    
    def get_klines(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 200,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> list[Kline]:
        import requests
        params = {
            "symbol": symbol.replace("/", ""),
            "interval": timeframe,
            "limit": limit
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        
        resp = requests.get(
            f"{self._base_url}/api/v3/klines",
            params=params,
            proxies={"https": self.proxy} if self.proxy else None
        )
        data = self._handle_response(resp)
        
        klines = []
        for k in data:
            klines.append(Kline(
                time=int(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5])
            ))
        return klines
    
    # ─── 交易 ──────────────────────────────────────────────────
    
    def place_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        order_type: Literal["MARKET", "LIMIT"],
        quantity: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        reduce_only: bool = False,
        position_side: Optional[Literal["LONG", "SHORT"]] = None,
    ) -> Order:
        import requests
        
        # Binance futures 需要 positionSide
        params = {
            "symbol": symbol.replace("/", ""),
            "side": side,
            "type": order_type,
            "quantity": quantity,
        }
        
        if self.mode == "futures":
            params["positionSide"] = position_side or ("LONG" if side == "BUY" else "SHORT")
            params["reduceOnly"] = reduce_only
        
        if order_type == "LIMIT" and price:
            params["price"] = price
            params["timeInForce"] = "GTC"
        
        if stop_price:
            params["stopPrice"] = stop_price
            params["stopPriceLimit"] = stop_price
        
        params = self._sign_request(params)
        resp = requests.post(
            f"{self._usdt_future_url}/fapi/v1/order",
            json=params,
            headers=self._headers(),
            proxies={"https": self.proxy} if self.proxy else None
        )
        data = self._handle_response(resp)
        
        return self._parse_order(data)
    
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        import requests
        params = self._sign_request({
            "symbol": symbol.replace("/", ""),
            "orderId": order_id
        })
        resp = requests.delete(
            f"{self._usdt_future_url}/fapi/v1/order",
            params=params,
            headers=self._headers(),
            proxies={"https": self.proxy} if self.proxy else None
        )
        self._handle_response(resp)
        return True
    
    def get_orders(self, symbol: str, limit: int = 50) -> list[Order]:
        import requests
        params = self._sign_request({
            "symbol": symbol.replace("/", ""),
            "limit": limit
        })
        resp = requests.get(
            f"{self._usdt_future_url}/fapi/v1/allOrders",
            params=params,
            headers=self._headers(),
            proxies={"https": self.proxy} if self.proxy else None
        )
        data = self._handle_response(resp)
        
        return [self._parse_order(o) for o in data]
    
    def _parse_order(self, data: dict) -> Order:
        return Order(
            order_id=str(data["orderId"]),
            symbol=data["symbol"],
            side=data["side"],
            type=data["type"],
            quantity=float(data["origQty"]),
            price=float(data["price"]) if data.get("price") and data["price"] != "0" else None,
            status=data["status"],
            filled_quantity=float(data.get("executedQty", 0)),
            avg_fill_price=float(data.get("avgPrice", 0)) or None,
            created_at=data["updateTime"]
        )