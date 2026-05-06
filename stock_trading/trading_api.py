"""
股票实盘交易接口 - Alpaca (美股) + TigerOpen (港股/A股)
"""

import os
from typing import Optional, Dict

# ========== Alpaca (美股) ==========
# pip3 install alpaca-trade-api


class AlpacaTrader:
    """Alpaca 美股交易接口（支持美股/ETF/加密货币）"""

    def __init__(self, api_key: str = None, secret_key: str = None, paper: bool = True):
        """
        Args:
            api_key: Alpaca API Key
            secret_key: Alpaca Secret Key
            paper: True=模拟盘, False=实盘
        """
        import alpaca_trade_api as tradeapi

        self.api_key = api_key or os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_SECRET_KEY")
        self.paper = paper
        base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        # Lazy init - only connect when actually used
        self._api = None
        self._base_url = base_url

    @property
    def api(self):
        import alpaca_trade_api as tradeapi
        if self._api is None:
            if not self.api_key or not self.secret_key:
                raise ValueError("Alpaca API key/secret not configured. Set ALPACA_API_KEY / ALPACA_SECRET_KEY env vars.")
            self._api = tradeapi.REST(self.api_key, self.secret_key, base_url=self._base_url, api_version='v2')
        return self._api

    def get_account(self) -> Dict:
        """获取账户信息"""
        try:
            account = self.api.get_account()
            return {
                "status": account.status,
                "cash": float(account.cash),
                "equity": float(account.equity),
                "buying_power": float(account.buying_power),
            }
        except Exception as e:
            return {"error": str(e)}

    def get_position(self, symbol: str) -> Optional[Dict]:
        """获取持仓"""
        try:
            pos = self.api.get_position(symbol)
            return {
                "symbol": pos.symbol,
                "qty": float(pos.qty),
                "avg_price": float(pos.avg_entry_cost),
                "market_value": float(pos.market_value),
            }
        except Exception:
            return None

    def get_all_positions(self) -> list:
        """获取所有持仓"""
        try:
            positions = self.api.list_positions()
            return [{
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_price": float(p.avg_entry_cost),
                "market_value": float(p.market_value),
            } for p in positions]
        except Exception as e:
            return []

    def buy_market(self, symbol: str, qty: int) -> Dict:
        """市价买入"""
        try:
            order = self.api.submit_order(symbol, qty, "buy", "market", "day")
            return {"status": "submitted", "order_id": order.id, "symbol": symbol, "qty": qty}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def sell_market(self, symbol: str, qty: int) -> Dict:
        """市价卖出"""
        try:
            order = self.api.submit_order(symbol, qty, "sell", "market", "day")
            return {"status": "submitted", "order_id": order.id, "symbol": symbol, "qty": qty}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def cancel_order(self, order_id: str) -> Dict:
        """取消订单"""
        try:
            self.api.cancel_order(order_id)
            return {"status": "cancelled", "order_id": order_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_open_orders(self) -> list:
        """获取未成交订单"""
        try:
            orders = self.api.list_orders(status='open')
            return [{
                "id": o.id,
                "symbol": o.symbol,
                "qty": float(o.qty),
                "side": o.side,
                "type": o.type,
                "status": o.status,
            } for o in orders]
        except Exception as e:
            return []


# ========== 老虎证券 TigerOpen (港股/A股) ==========
# pip3 install tigeropen
# 持仓支持: https://quant.itigerus.com/


class TigerTrader:
    """老虎证券港股/A股交易接口"""

    def __init__(self, tiger_id: str = None, api_key: str = None, private_key: str = None, paper: bool = True):
        """
        Args:
            tiger_id: 老虎账号 (如 "123456")
            api_key: API Key (also used as app_id)
            private_key: RSA私钥 (pem 格式字符串)
            paper: True=模拟盘, False=实盘
        """
        import os as _os
        from tigeropen.tiger_open_config import TigerOpenClientConfig
        from tigeropen.trade.trade_client import TradeClient

        # 设置环境变量（tigeropen SDK 从环境变量读取配置）
        if tiger_id:
            _os.environ['TIGEROPEN_TIGER_ID'] = tiger_id
        if api_key:
            _os.environ['TIGEROPEN_LICENSE'] = api_key  # license = app_id
        if private_key:
            _os.environ['TIGEROPEN_PRIVATE_KEY'] = private_key

        self.tiger_id = tiger_id or _os.getenv('TIGEROPEN_TIGER_ID')
        self.api_key = api_key or _os.getenv('TIGEROPEN_LICENSE')
        self.private_key = private_key or _os.getenv('TIGEROPEN_PRIVATE_KEY')
        self.paper = paper

        # Lazy init - only connect when actually used
        self._client = None

        if not self.private_key:
            print("WARNING: TIGEROPEN_PRIVATE_KEY not set. TigerTrader will not be usable until API key is provided.")

    @property
    def client(self):
        if self._client is None:
            if not self.private_key:
                raise ValueError("TigerOpen private key not configured. Set TIGEROPEN_PRIVATE_KEY env var or pass private_key argument.")
            from tigeropen.tiger_open_config import TigerOpenClientConfig
            from tigeropen.trade.trade_client import TradeClient
            config = TigerOpenClientConfig()
            self._client = TradeClient(config)
        return self._client

    def get_account(self) -> Dict:
        """获取账户信息"""
        try:
            accounts = self.client.get_managed_accounts()
            if not accounts:
                return {"error": "No accounts found"}
            account = accounts[0]
            return {
                "account": account.account,
                "capability": account.capability,
                "status": account.status,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_positions(self, market: str = "ALL") -> list:
        """获取持仓 market: ALL/HK/US/CN"""
        try:
            from tigeropen.common.consts import Market
            market_map = {"HK": Market.HK, "US": Market.US, "CN": Market.CN, "ALL": Market.ALL}
            positions = self.client.get_positions(market=market_map.get(market, Market.ALL))
            return [{
                "symbol": p.contract.symbol if hasattr(p.contract, 'symbol') else str(p),
                "quantity": p.quantity,
                "avg_cost": p.average_cost,
            } for p in positions]
        except Exception as e:
            return [{"error": str(e)}]

    def buy(self, symbol: str, quantity: int, market: str = "HK") -> Dict:
        """买入 market: HK/US/CN"""
        try:
            from tigeropen.common.consts import Market
            market_map = {"HK": Market.HK, "US": Market.US, "CN": Market.CN}
            contracts = self.client.get_contracts(symbol, market_map.get(market, Market.HK))
            if not contracts:
                return {"status": "error", "message": f"No contract for {symbol} in {market}"}
            contract = contracts[0]
            order = self.client.submit_order(contract, quantity, "BUY")
            return {"status": "submitted", "order_id": order.order_id, "symbol": symbol}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def sell(self, symbol: str, quantity: int, market: str = "HK") -> Dict:
        """卖出 market: HK/US/CN"""
        try:
            from tigeropen.common.consts import Market
            market_map = {"HK": Market.HK, "US": Market.US, "CN": Market.CN}
            contracts = self.client.get_contracts(symbol, market_map.get(market, Market.HK))
            if not contracts:
                return {"status": "error", "message": f"No contract for {symbol} in {market}"}
            contract = contracts[0]
            order = self.client.submit_order(contract, quantity, "SELL")
            return {"status": "submitted", "order_id": order.order_id, "symbol": symbol}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# ========== 统一入口 ==========

def create_trader(trader_type: str = "alpaca", **kwargs) -> object:
    """
    工厂函数：创建交易接口
    trader_type: "alpaca" (美股) / "tiger" (港/A股)
    """
    if trader_type == "alpaca":
        return AlpacaTrader(**kwargs)
    elif trader_type == "tiger":
        return TigerTrader(**kwargs)
    else:
        raise ValueError(f"Unknown trader type: {trader_type}")


if __name__ == "__main__":
    print("=" * 60)
    print("实盘交易接口")
    print("=" * 60)
    print()
    print("AlpacaTrader 用法:")
    print("  1. 注册 https://app.alpaca.markets/")
    print("  2. 设置环境变量 ALPACA_API_KEY / ALPACA_SECRET_KEY")
    print("  3. 创建: AlpacaTrader(paper=True)  # 模拟盘")
    print()
    print("TigerTrader 用法:")
    print("  1. 注册 https://quant.itigerus.com/")
    print("  2. 设置环境变量 TIGER_ID / TIGER_API_KEY / TIGER_PRIVATE_KEY")
    print("  3. 创建: TigerTrader(paper=True)  # 模拟盘")
    print()
    print("示例:")
    print("  alpaca = AlpacaTrader(paper=True)")
    print("  print(alpaca.get_account())")
    print()
    print("  tiger = TigerTrader(paper=True)")
    print("  print(tiger.get_account())")
    print("=" * 60)