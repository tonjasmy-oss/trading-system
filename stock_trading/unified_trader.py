"""
统一股票交易引擎 - A股/港股/美股
整合多券商API：Alpaca(美股) / 老虎证券(港股/A股) / 富途(备用)

使用方式：
  from unified_trader import StockTrader
  trader = StockTrader(market="us")  # 美股
  trader = StockTrader(market="hk")  # 港股
  trader = StockTrader(market="cn")  # A股
"""

import os
import time
import logging
from typing import Optional, Dict, List
from enum import Enum

logger = logging.getLogger(__name__)


class Market(Enum):
    CN = "cn"   # A股
    HK = "hk"   # 港股
    US = "us"   # 美股


class OrderType(Enum):
    MARKET = "market"    # 市价单
    LIMIT = "limit"      # 限价单


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# ============================================================
# 券商接口基类
# ============================================================

class BrokerInterface:
    """券商接口基类"""
    
    def __init__(self, paper: bool = True):
        self.paper = paper
    
    def is_connected(self) -> bool:
        raise NotImplementedError
    
    def buy(self, symbol: str, quantity: int, order_type: OrderType = OrderType.MARKET, 
            limit_price: float = None) -> Dict:
        raise NotImplementedError
    
    def sell(self, symbol: str, quantity: int, order_type: OrderType = OrderType.MARKET,
             limit_price: float = None) -> Dict:
        raise NotImplementedError
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        raise NotImplementedError
    
    def get_positions(self) -> List[Dict]:
        raise NotImplementedError
    
    def get_account(self) -> Dict:
        raise NotImplementedError
    
    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError


# ============================================================
# Alpaca 美股券商
# ============================================================

class AlpacaBroker(BrokerInterface):
    """Alpaca - 美股券商（免费API）"""
    
    def __init__(self, api_key: str = None, secret_key: str = None, paper: bool = True):
        super().__init__(paper)
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self.base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.api = None
        self._connect()
    
    def _connect(self):
        if not self.api_key or not self.secret_key:
            logger.warning("Alpaca API密钥未配置，使用模拟模式")
            return
        try:
            import alpaca_trade_api as tradeapi
            self.api = tradeapi.REST(self.api_key, self.secret_key, self.base_url)
            # 测试连接
            self.api.get_account()
            logger.info("Alpaca 连接成功")
        except Exception as e:
            logger.error(f"Alpaca 连接失败: {e}")
            self.api = None
    
    def is_connected(self) -> bool:
        return self.api is not None
    
    def buy(self, symbol: str, quantity: int, order_type: OrderType = OrderType.MARKET,
            limit_price: float = None) -> Dict:
        if not self.api:
            return {"status": "error", "message": "Alpaca未连接"}
        try:
            side = "buy"
            if order_type == OrderType.MARKET:
                order = self.api.submit_order(symbol, quantity, side, "market", "day")
            else:
                order = self.api.submit_order(symbol, quantity, side, "limit", "day", 
                                              limit_price=limit_price)
            return {
                "status": "submitted",
                "order_id": order.id,
                "symbol": symbol,
                "quantity": quantity,
                "side": side,
                "order_type": order_type.value
            }
        except Exception as e:
            logger.error(f"Alpaca buy 失败: {e}")
            return {"status": "error", "message": str(e)}
    
    def sell(self, symbol: str, quantity: int, order_type: OrderType = OrderType.MARKET,
             limit_price: float = None) -> Dict:
        if not self.api:
            return {"status": "error", "message": "Alpaca未连接"}
        try:
            side = "sell"
            if order_type == OrderType.MARKET:
                order = self.api.submit_order(symbol, quantity, side, "market", "day")
            else:
                order = self.api.submit_order(symbol, quantity, side, "limit", "day",
                                              limit_price=limit_price)
            return {
                "status": "submitted",
                "order_id": order.id,
                "symbol": symbol,
                "quantity": quantity,
                "side": side,
                "order_type": order_type.value
            }
        except Exception as e:
            logger.error(f"Alpaca sell 失败: {e}")
            return {"status": "error", "message": str(e)}
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        if not self.api:
            return None
        try:
            pos = self.api.get_position(symbol)
            return {
                "symbol": pos.symbol,
                "quantity": float(pos.qty),
                "avg_price": float(pos.avg_entry_cost),
                "market_value": float(pos.market_value),
                "unrealized_pl": float(pos.unrealized_pl)
            }
        except Exception:
            return None
    
    def get_positions(self) -> List[Dict]:
        if not self.api:
            return []
        try:
            positions = self.api.list_positions()
            return [{
                "symbol": p.symbol,
                "quantity": float(p.qty),
                "avg_price": float(p.avg_entry_cost),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl)
            } for p in positions]
        except Exception as e:
            logger.error(f"Alpaca get_positions 失败: {e}")
            return []
    
    def get_account(self) -> Dict:
        if not self.api:
            return {"cash": 0, "portfolio_value": 0, "status": "not_connected"}
        try:
            acc = self.api.get_account()
            return {
                "cash": float(acc.cash),
                "portfolio_value": float(acc.portfolio_value),
                "buying_power": float(acc.buying_power),
                "status": acc.status
            }
        except Exception as e:
            logger.error(f"Alpaca get_account 失败: {e}")
            return {"error": str(e)}


# ============================================================
# 老虎证券 - 港股/A股
# ============================================================

class TigerBroker(BrokerInterface):
    """老虎证券 - 港股/美股/A股通
    
    注册链接: https://www.tigerbrokers.com/finv2/join
    API文档: https://quant.itigerus.com/
    """
    
    def __init__(self, tiger_id: str = None, api_key: str = None, api_secret: str = None, 
                 paper: bool = True):
        super().__init__(paper)
        self.tiger_id = tiger_id or os.getenv("TIGER_ID")
        self.api_key = api_key or os.getenv("TIGER_API_KEY")
        self.api_secret = api_secret or os.getenv("TIGER_API_SECRET")
        self.client = None
        self._connect()
    
    def _connect(self):
        if not self.api_key:
            logger.warning("老虎证券API密钥未配置，使用模拟模式")
            return
        try:
            from tigeropen.tiger_open_python_client import TigerOpenPythonClient
            self.client = TigerOpenPythonClient(
                api_key=self.api_key, 
                api_secret=self.api_secret
            )
            logger.info("老虎证券连接成功")
        except ImportError:
            logger.warning("tigeropen未安装，请运行: pip3 install tigeropen")
            self.client = None
        except Exception as e:
            logger.error(f"老虎证券连接失败: {e}")
            self.client = None
    
    def is_connected(self) -> bool:
        return self.client is not None
    
    def _get_market_code(self, market: Market) -> str:
        mapping = {Market.CN: "CN", Market.HK: "HK", Market.US: "US"}
        return mapping.get(market, "HK")
    
    def buy(self, symbol: str, quantity: int, order_type: OrderType = OrderType.MARKET,
            limit_price: float = None, market: Market = Market.HK) -> Dict:
        if not self.client:
            return {"status": "simulated", "message": "模拟买入", "symbol": symbol, 
                    "quantity": quantity, "price": limit_price}
        try:
            market_code = self._get_market_code(market)
            contract = self.client.get_contract(symbol, market_code)
            order_type_str = "MARKET" if order_type == OrderType.MARKET else "LIMIT"
            order = self.client.submit_order(contract, quantity, "BUY", 
                                            order_type=order_type_str,
                                            limit_price=limit_price)
            return {
                "status": "submitted",
                "order_id": order.id,
                "symbol": symbol,
                "quantity": quantity
            }
        except Exception as e:
            logger.error(f"老虎证券buy失败: {e}")
            return {"status": "error", "message": str(e)}
    
    def sell(self, symbol: str, quantity: int, order_type: OrderType = OrderType.MARKET,
             limit_price: float = None, market: Market = Market.HK) -> Dict:
        if not self.client:
            return {"status": "simulated", "message": "模拟卖出", "symbol": symbol,
                    "quantity": quantity, "price": limit_price}
        try:
            market_code = self._get_market_code(market)
            contract = self.client.get_contract(symbol, market_code)
            order_type_str = "MARKET" if order_type == OrderType.MARKET else "LIMIT"
            order = self.client.submit_order(contract, quantity, "SELL",
                                            order_type=order_type_str,
                                            limit_price=limit_price)
            return {
                "status": "submitted",
                "order_id": order.id,
                "symbol": symbol,
                "quantity": quantity
            }
        except Exception as e:
            logger.error(f"老虎证券sell失败: {e}")
            return {"status": "error", "message": str(e)}
    
    def get_position(self, symbol: str, market: Market = Market.HK) -> Optional[Dict]:
        if not self.client:
            return None
        try:
            positions = self.client.get_positions()
            market_code = self._get_market_code(market)
            for pos in positions:
                if pos.contract.symbol == symbol and pos.contract.market == market_code:
                    return {
                        "symbol": pos.contract.symbol,
                        "quantity": pos.quantity,
                        "avg_price": pos.avg_cost,
                        "market_value": pos.market_value
                    }
            return None
        except Exception as e:
            logger.error(f"老虎证券get_position失败: {e}")
            return None
    
    def get_positions(self, market: Market = None) -> List[Dict]:
        if not self.client:
            return []
        try:
            positions = self.client.get_positions()
            result = []
            for pos in positions:
                result.append({
                    "symbol": pos.contract.symbol,
                    "market": pos.contract.market,
                    "quantity": pos.quantity,
                    "avg_price": pos.avg_cost,
                    "market_value": pos.market_value
                })
            return result
        except Exception as e:
            logger.error(f"老虎证券get_positions失败: {e}")
            return []
    
    def get_account(self) -> Dict:
        if not self.client:
            return {"cash": 0, "portfolio_value": 0, "status": "not_connected"}
        try:
            accounts = self.client.get_accounts()
            if accounts:
                acc = accounts[0]
                return {
                    "cash": acc.cash,
                    "portfolio_value": acc.portfolio_value,
                    "currency": acc.currency
                }
            return {}
        except Exception as e:
            logger.error(f"老虎证券get_account失败: {e}")
            return {"error": str(e)}


# ============================================================
# 富途证券 (备用)
# ============================================================

class FutuBroker(BrokerInterface):
    """富途证券 - 港股/A股/美股
    
    注册链接: https://www.futunn.com/
    API文档: https://openapi.futunn.com/
    """
    
    def __init__(self, account_id: str = None, app_id: str = None, app_secret: str = None,
                 paper: bool = True):
        super().__init__(paper)
        self.account_id = account_id or os.getenv("FUTU_ACCOUNT_ID")
        self.app_id = app_id or os.getenv("FUTU_APP_ID")
        self.app_secret = app_secret or os.getenv("FUTU_APP_SECRET")
        self.client = None
        self._connect()
    
    def _connect(self):
        if not self.app_id:
            logger.warning("富途API密钥未配置，使用模拟模式")
            return
        try:
            # 富途API初始化（示例）
            # from futu import OpenQuoteContext, OpenTradeContext
            # self.quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
            logger.info("富途连接成功（模拟）")
        except Exception as e:
            logger.error(f"富途连接失败: {e}")
    
    def is_connected(self) -> bool:
        return self.client is not None
    
    def buy(self, symbol: str, quantity: int, order_type: OrderType = OrderType.MARKET,
            limit_price: float = None) -> Dict:
        return {"status": "simulated", "message": "富途模拟买入", "symbol": symbol, "quantity": quantity}
    
    def sell(self, symbol: str, quantity: int, order_type: OrderType = OrderType.MARKET,
             limit_price: float = None) -> Dict:
        return {"status": "simulated", "message": "富途模拟卖出", "symbol": symbol, "quantity": quantity}
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        return None
    
    def get_positions(self) -> List[Dict]:
        return []
    
    def get_account(self) -> Dict:
        return {"cash": 0, "portfolio_value": 0, "status": "not_connected"}


# ============================================================
# 统一交易引擎
# ============================================================

class StockTrader:
    """统一股票交易引擎
    
    使用示例：
    
    # 美股
    trader = StockTrader(market="us")
    trader.buy("AAPL", 10)
    
    # 港股
    trader = StockTrader(market="hk")
    trader.buy("00700.HK", 100)
    
    # A股
    trader = StockTrader(market="cn")
    trader.buy("600000.SH", 1000)
    """
    
    _instances = {}
    
    def __init__(self, market: str = "us", broker: str = "auto", paper: bool = True):
        """
        初始化股票交易引擎
        market: "us", "hk", "cn"
        broker: "auto", "alpaca", "tiger", "futu"
        paper: True=模拟盘, False=实盘
        """
        self.market = market
        self.paper = paper
        self.broker: BrokerInterface = None
        self._init_broker(broker)
        
        # 注册到全局实例
        StockTrader._instances[market] = self
    
    def _init_broker(self, broker: str):
        if broker == "auto":
            if self.market == "us":
                broker = "alpaca"
            elif self.market in ("hk", "cn"):
                broker = "tiger"
        
        if broker == "alpaca":
            self.broker = AlpacaBroker(paper=self.paper)
        elif broker == "tiger":
            self.broker = TigerBroker(paper=self.paper)
        elif broker == "futu":
            self.broker = FutuBroker(paper=self.paper)
        else:
            raise ValueError(f"未知券商: {broker}")
    
    def is_connected(self) -> bool:
        return self.broker and self.broker.is_connected()
    
    def buy(self, symbol: str, quantity: int, order_type: str = "market",
            limit_price: float = None) -> Dict:
        """买入股票"""
        ot = OrderType.MARKET if order_type == "market" else OrderType.LIMIT
        return self.broker.buy(symbol, quantity, ot, limit_price)
    
    def sell(self, symbol: str, quantity: int, order_type: str = "market",
             limit_price: float = None) -> Dict:
        """卖出股票"""
        ot = OrderType.MARKET if order_type == "market" else OrderType.LIMIT
        return self.broker.sell(symbol, quantity, ot, limit_price)
    
    def get_position(self, symbol: str = None) -> Optional[Dict]:
        """获取持仓"""
        if symbol:
            return self.broker.get_position(symbol)
        return None
    
    def get_positions(self) -> List[Dict]:
        """获取所有持仓"""
        return self.broker.get_positions()
    
    def get_account(self) -> Dict:
        """获取账户信息"""
        return self.broker.get_account()
    
    @classmethod
    def get_trader(cls, market: str) -> Optional['StockTrader']:
        """获取指定市场的交易实例"""
        return cls._instances.get(market)
    
    @classmethod
    def get_all_positions(cls) -> Dict[str, List[Dict]]:
        """获取所有市场的持仓"""
        result = {}
        for market, trader in cls._instances.items():
            result[market] = trader.get_positions()
        return result


# ============================================================
# 模拟交易（无API密钥时使用）
# ============================================================

class SimulatedStockTrader:
    """模拟股票交易（无需API密钥）"""
    
    def __init__(self, initial_cash: float = 100000.0):
        self.cash = initial_cash
        self.positions = {}  # symbol -> {quantity, avg_price}
        self.orders = []
    
    def buy(self, symbol: str, quantity: int, price: float = None) -> Dict:
        """模拟买入"""
        if price is None:
            # 使用默认值（实际应该获取实时价格）
            price = 100.0
        cost = price * quantity
        if cost > self.cash:
            return {"status": "rejected", "message": "资金不足"}
        
        if symbol in self.positions:
            old_qty = self.positions[symbol]["quantity"]
            old_price = self.positions[symbol]["avg_price"]
            new_qty = old_qty + quantity
            new_price = (old_price * old_qty + price * quantity) / new_qty
            self.positions[symbol] = {"quantity": new_qty, "avg_price": new_price}
        else:
            self.positions[symbol] = {"quantity": quantity, "avg_price": price}
        
        self.cash -= cost
        order_id = f"SIM_{len(self.orders)}"
        self.orders.append({"id": order_id, "symbol": symbol, "side": "buy", 
                           "quantity": quantity, "price": price})
        return {"status": "filled", "order_id": order_id, "symbol": symbol, 
                "quantity": quantity, "price": price}
    
    def sell(self, symbol: str, quantity: int, price: float = None) -> Dict:
        """模拟卖出"""
        if symbol not in self.positions:
            return {"status": "rejected", "message": "无持仓"}
        
        pos = self.positions[symbol]
        if pos["quantity"] < quantity:
            return {"status": "rejected", "message": "持仓不足"}
        
        if price is None:
            price = pos["avg_price"]
        
        revenue = price * quantity
        self.cash += revenue
        pos["quantity"] -= quantity
        if pos["quantity"] == 0:
            del self.positions[symbol]
        
        order_id = f"SIM_{len(self.orders)}"
        self.orders.append({"id": order_id, "symbol": symbol, "side": "sell",
                           "quantity": quantity, "price": price})
        return {"status": "filled", "order_id": order_id, "symbol": symbol,
                "quantity": quantity, "price": price}
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        if symbol in self.positions:
            return {"symbol": symbol, **self.positions[symbol]}
        return None
    
    def get_positions(self) -> List[Dict]:
        return [{"symbol": s, **p} for s, p in self.positions.items()]
    
    def get_account(self) -> Dict:
        total_value = sum(p["quantity"] * p["avg_price"] for p in self.positions.values())
        return {
            "cash": self.cash,
            "positions_value": total_value,
            "total_value": self.cash + total_value,
            "position_count": len(self.positions)
        }


# ============================================================
# 导出
# ============================================================

__all__ = [
    "StockTrader", "SimulatedStockTrader",
    "AlpacaBroker", "TigerBroker", "FutuBroker",
    "Market", "OrderType", "OrderSide", "OrderStatus"
]


if __name__ == "__main__":
    # 测试
    print("=== 股票交易引擎测试 ===")
    
    # 测试Alpaca
    print("\n--- Alpaca (美股) ---")
    trader = StockTrader(market="us", broker="alpaca", paper=True)
    print(f"连接状态: {trader.is_connected()}")
    print(f"账户信息: {trader.get_account()}")
    
    # 测试老虎证券
    print("\n--- 老虎证券 (港股/A股) ---")
    trader = StockTrader(market="hk", broker="tiger", paper=True)
    print(f"连接状态: {trader.is_connected()}")
    print(f"账户信息: {trader.get_account()}")
    
    # 测试模拟交易
    print("\n--- 模拟交易 ---")
    sim = SimulatedStockTrader(initial_cash=100000)
    print(f"初始资金: {sim.cash}")
    result = sim.buy("AAPL", 100, price=150)
    print(f"买入结果: {result}")
    print(f"当前资金: {sim.cash}")
    print(f"持仓: {sim.get_positions()}")
