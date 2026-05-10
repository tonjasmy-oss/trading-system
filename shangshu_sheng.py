"""
尚书省 - 交易执行调度层（参考金策智算"三省六部"架构）
=====================================================

定位：执行调度 + 资金清算。所有经过门下省审核的交易指令，由尚书省统一执行。
支持多交易所适配（Binance / Gate.io / Bybit / Hyperliquid）

执行流程：
  中书省信号 → 门下省审核 → ✅通过 → 尚书省执行 → 记录刑部交易流水

使用方式：
  shangshu = ShangshuSheng(exchange="binance", api_key=..., api_secret=...)
  result = await shangshu.execute_open(symbol="ETH/USDT", side="buy",
                                        quantity=0.5, order_type="market")
  if result.success:
      menxia.record_open(symbol, ...)  # 回调门下省记录
"""

import time
import logging
import asyncio
import sqlite3
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# 延迟导入 ccxt（仅实盘模式需要）
try:
    import ccxt
    _CCXT_AVAILABLE = True
except ImportError:
    ccxt = None
    _CCXT_AVAILABLE = False
    logger.warning("ccxt 未安装，尚书省实盘交易功能不可用")


# ============================================================
# 交易所配置映射
# ============================================================

EXCHANGE_CONFIGS = {
    "binance": {
        "id": "binance",
        "name": "Binance",
        "spot_markets": "https://api.binance.com/api/v3/exchangeInfo",
        "rate_limit": 1200,  # ms
        "min_order_value": 10,  # USDT
    },
    "gateio": {
        "id": "gateio",
        "name": "Gate.io",
        "spot_markets": "https://api.gateio.ws/api/v4/spot/currency_pairs",
        "rate_limit": 1500,
        "min_order_value": 1,
    },
    "bybit": {
        "id": "bybit",
        "name": "Bybit",
        "spot_markets": "https://api.bybit.com/v5/market/instruments-info",
        "rate_limit": 100,
        "min_order_value": 10,
    },
    "hyperliquid": {
        "id": "hyperliquid",
        "name": "Hyperliquid",
        "spot_markets": "https://api.hyperliquid.xyz/info",
        "rate_limit": 500,
        "min_order_value": 0,
    },
    "weex": {
        "id": "weex",
        "name": "Weex",
        "spot_markets": "https://api-spot.weex.com/api/v3/market/ticker/24hr",
        "rate_limit": 500,
        "min_order_value": 1,
    },
}

# ccxt symbol format -> exchange-specific format
_SYMBOL_FORMAT = {
    "binance":   lambda s: s,           # ETH/USDT 直接用
    "gateio":    lambda s: s.replace("/", "_"),   # ETH_USDT
    "bybit":     lambda s: s.replace("/", ""),    # ETHUSDT
    "hyperliquid": lambda s: s.split("/")[0],     # ETH (perpetual)
    "weex":      lambda s: s,           # BTC/USDT 格式
}


# ============================================================
# 执行结果
# ============================================================

@dataclass
class ExecutionResult:
    """交易执行结果"""
    success: bool
    order_id: str
    symbol: str
    side: str              # BUY / SELL
    quantity: float
    exec_price: float     # 实际成交价
    exec_type: str         # market / limit / stop_loss / take_profit
    commission: float      # 手续费（USDT）
    message: str          # 成功/失败消息
    raw_response: Optional[Dict] = None
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class PositionInfo:
    """持仓信息（从交易所查询）"""
    symbol: str
    side: str             # long / short
    size: float            # 持仓数量
    entry_price: float
    unrealized_pnl: float
    leverage: float = 1.0


# ============================================================
# 交易所适配器基类
# ============================================================

class ExchangeAdapter:
    """交易所执行适配器基类"""

    def __init__(self, exchange_id: str, api_key: str = "", api_secret: str = "",
                 testnet: bool = False):
        self.exchange_id = exchange_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self._exchange = None

    def _get_exchange(self):
        if not _CCXT_AVAILABLE:
            raise RuntimeError("ccxt 未安装，无法执行实盘交易")
        if self._exchange is None:
            ex_class = getattr(ccxt, self.exchange_id)
            config = {
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
            if self.testnet and hasattr(ex_class, "set_sandbox_mode"):
                config["testnet"] = True
            self._exchange = ex_class(config)
            logger.info(f"[尚书省] 交易所实例: {self.exchange_id} "
                       f"{'(测试网)' if self.testnet else '(实盘)'}")
        return self._exchange

    def _format_symbol(self, symbol: str) -> str:
        """转换为交易所特定格式"""
        formatter = _SYMBOL_FORMAT.get(self.exchange_id, lambda s: s)
        return formatter(symbol)

    async def place_order(self, symbol: str, side: str, order_type: str,
                         quantity: float, price: Optional[float] = None,
                         params: Optional[Dict] = None) -> ExecutionResult:
        """下单（异步包装）"""
        raise NotImplementedError

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消挂单"""
        raise NotImplementedError

    async def get_balance(self, asset: str = "USDT") -> float:
        """查询余额"""
        raise NotImplementedError

    async def get_position(self, symbol: str) -> Optional[PositionInfo]:
        """查询持仓"""
        raise NotImplementedError

    async def get_order_status(self, order_id: str, symbol: str) -> Optional[Dict]:
        """查询订单状态"""
        raise NotImplementedError


class BinanceAdapter(ExchangeAdapter):
    """Binance 交易所适配器"""

    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False):
        super().__init__("binance", api_key, api_secret, testnet)

    async def place_order(self, symbol: str, side: str, order_type: str,
                         quantity: float, price: Optional[float] = None,
                         params: Optional[Dict] = None) -> ExecutionResult:
        ex = self._get_exchange()
        ccxt_sym = self._format_symbol(symbol)
        order_type = order_type.upper()

        try:
            if order_type == "MARKET":
                order = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ex.create_order(ccxt_sym, "market", side.lower(),
                                           quantity)
                )
            elif order_type == "LIMIT":
                order = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ex.create_order(ccxt_sym, "limit", side.lower(),
                                           quantity, price)
                )
            elif order_type == "STOP_LOSS":
                params = params or {}
                params["stopPrice"] = price
                order = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ex.create_order(ccxt_sym, "stop_loss_limit",
                                           side.lower(), quantity, price, params)
                )
            else:
                return ExecutionResult(False, "", symbol, side, quantity, 0,
                                      order_type, 0, f"不支持的订单类型: {order_type}")

            fills = order.get("filled", []) or order.get("trades", [])
            total_fee = sum(float(f.get("fee", 0)) for f in fills)
            avg_price = order.get("average") or (
                sum(float(f["price"]) * float(f["traded"]) for f in fills) /
                max(sum(float(f["traded"]) for f in fills), 1) if fills else 0
            )

            return ExecutionResult(
                success=True,
                order_id=str(order["id"]),
                symbol=symbol,
                side=side,
                quantity=float(order.get("amount", quantity)),
                exec_price=float(avg_price or order.get("price", 0) or price or 0),
                exec_type=order_type.lower(),
                commission=total_fee,
                message="成功",
                raw_response=order,
            )
        except Exception as e:
            logger.error(f"[尚书省] Binance 下单失败: {e}")
            return ExecutionResult(False, "", symbol, side, quantity, 0,
                                  order_type, 0, str(e))

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        ex = self._get_exchange()
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ex.cancel_order(order_id, self._format_symbol(symbol))
            )
            return True
        except Exception as e:
            logger.error(f"[尚书省] 取消订单失败: {e}")
            return False

    async def get_balance(self, asset: str = "USDT") -> float:
        ex = self._get_exchange()
        try:
            bal = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ex.fetch_balance())
            return float(bal.get(asset, {}).get("free", 0))
        except Exception as e:
            logger.error(f"[尚书省] 查询余额失败: {e}")
            return 0.0

    async def get_position(self, symbol: str) -> Optional[PositionInfo]:
        return None  # 现货不需要 position query

    async def get_order_status(self, order_id: str, symbol: str) -> Optional[Dict]:
        ex = self._get_exchange()
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ex.fetch_order(order_id, self._format_symbol(symbol))
            )
        except Exception:
            return None


class GateioAdapter(ExchangeAdapter):
    """Gate.io 交易所适配器"""

    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False):
        super().__init__("gateio", api_key, api_secret, testnet)

    def _format_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "_")

    async def place_order(self, symbol: str, side: str, order_type: str,
                         quantity: float, price: Optional[float] = None,
                         params: Optional[Dict] = None) -> ExecutionResult:
        ex = self._get_exchange()
        ccxt_sym = self._format_symbol(symbol)

        try:
            if order_type == "MARKET":
                order = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ex.create_order(ccxt_sym, "market", side.lower(), quantity)
                )
            elif order_type == "LIMIT":
                order = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ex.create_order(ccxt_sym, "limit", side.lower(),
                                           quantity, price)
                )
            else:
                return ExecutionResult(False, "", symbol, side, quantity, 0,
                                      order_type, 0, f"不支持: {order_type}")

            fills = order.get("trades", [])
            total_fee = sum(float(f.get("fee", 0)) for f in fills)
            avg_price = (sum(float(f["price"]) * float(f["amount"])
                         for f in fills) / max(sum(float(f["amount"])
                         for f in fills), 1) if fills else price or 0)

            return ExecutionResult(
                success=True,
                order_id=str(order["id"]),
                symbol=symbol,
                side=side,
                quantity=float(order.get("amount", quantity)),
                exec_price=float(avg_price),
                exec_type=order_type.lower(),
                commission=total_fee,
                message="成功",
                raw_response=order,
            )
        except Exception as e:
            logger.error(f"[尚书省] Gate.io 下单失败: {e}")
            return ExecutionResult(False, "", symbol, side, quantity, 0,
                                  order_type, 0, str(e))

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        ex = self._get_exchange()
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ex.cancel_order(order_id, self._format_symbol(symbol))
            )
            return True
        except Exception as e:
            logger.error(f"[尚书省] 取消订单失败: {e}")
            return False

    async def get_balance(self, asset: str = "USDT") -> float:
        ex = self._get_exchange()
        try:
            bal = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ex.fetch_balance())
            return float(bal.get(asset, {}).get("free", 0))
        except Exception as e:
            logger.error(f"[尚书省] 查询余额失败: {e}")
            return 0.0

    async def get_position(self, symbol: str) -> Optional[PositionInfo]:
        return None

    async def get_order_status(self, order_id: str, symbol: str) -> Optional[Dict]:
        ex = self._get_exchange()
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ex.fetch_order(order_id, self._format_symbol(symbol))
            )
        except Exception:
            return None


class HyperliquidAdapter(ExchangeAdapter):
    """Hyperliquid 永续合约适配器"""

    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False):
        super().__init__("hyperliquid", api_key, api_secret, testnet)

    async def place_order(self, symbol: str, side: str, order_type: str,
                         quantity: float, price: Optional[float] = None,
                         params: Optional[Dict] = None) -> ExecutionResult:
        if not _CCXT_AVAILABLE:
            return ExecutionResult(False, "", symbol, side, quantity, 0,
                                  order_type, 0, "ccxt 未安装")

        ex = self._get_exchange()
        ccxt_sym = symbol.split("/")[0]  # Hyperliquid 用 ETH 而不是 ETH/USDT

        try:
            if order_type == "MARKET":
                order = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ex.create_order(ccxt_sym, "market", side.lower(), quantity)
                )
            elif order_type == "LIMIT":
                order = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ex.create_order(ccxt_sym, "limit", side.lower(),
                                           quantity, price)
                )
            else:
                return ExecutionResult(False, "", symbol, side, quantity, 0,
                                      order_type, 0, f"不支持: {order_type}")

            avg_price = float(order.get("average", 0) or price or 0)
            return ExecutionResult(
                success=True,
                order_id=str(order["id"]),
                symbol=symbol,
                side=side,
                quantity=float(order.get("amount", quantity)),
                exec_price=avg_price,
                exec_type=order_type.lower(),
                commission=float(order.get("fee", 0)),
                message="成功",
                raw_response=order,
            )
        except Exception as e:
            logger.error(f"[尚书省] Hyperliquid 下单失败: {e}")
            return ExecutionResult(False, "", symbol, side, quantity, 0,
                                  order_type, 0, str(e))

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        ex = self._get_exchange()
        try:
            ccxt_sym = symbol.split("/")[0]
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: ex.cancel_order(order_id, ccxt_sym))
            return True
        except Exception as e:
            logger.error(f"[尚书省] 取消订单失败: {e}")
            return False

    async def get_balance(self, asset: str = "USDT") -> float:
        ex = self._get_exchange()
        try:
            bal = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ex.fetch_balance())
            return float(bal.get(asset, {}).get("free", 0))
        except Exception as e:
            logger.error(f"[尚书省] 查询余额失败: {e}")
            return 0.0

    async def get_position(self, symbol: str) -> Optional[PositionInfo]:
        ex = self._get_exchange()
        try:
            ccxt_sym = symbol.split("/")[0]
            pos = ex.fetch_positions([ccxt_sym])
            if pos:
                p = pos[0]
                return PositionInfo(
                    symbol=symbol,
                    side=p.get("side", "long"),
                    size=float(p.get("contracts", 0)),
                    entry_price=float(p.get("entryPrice", 0)),
                    unrealized_pnl=float(p.get("unrealizedPnl", 0)),
                    leverage=float(p.get("leverage", 1)),
                )
        except Exception as e:
            logger.error(f"[尚书省] 查询持仓失败: {e}")
        return None

    async def get_order_status(self, order_id: str, symbol: str) -> Optional[Dict]:
        ex = self._get_exchange()
        try:
            ccxt_sym = symbol.split("/")[0]
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: ex.fetch_order(order_id, ccxt_sym))
        except Exception:
            return None


class WeexAdapter(ExchangeAdapter):
    """Weex 交易所适配器（独立 REST API，非 ccxt）"""

    def __init__(self, api_key: str = "", api_secret: str = "",
                 api_passphrase: str = "", testnet: bool = False):
        super().__init__("weex", api_key, api_secret, testnet)
        self.api_passphrase = api_passphrase

    def _get_exchange(self):
        """Weex 不使用 ccxt，直接返回自身占位"""
        return self

    async def place_order(self, symbol: str, side: str, order_type: str,
                         quantity: float, price: Optional[float] = None,
                         params: Optional[Dict] = None) -> ExecutionResult:
        from weex import create_order as weex_create_order

        def _do():
            return weex_create_order(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_passphrase=self.api_passphrase,
                symbol=symbol,
                side=side.lower(),
                order_type=order_type.lower(),
                amount=quantity,
                price=price,
            )

        try:
            result = await asyncio.get_event_loop().run_in_executor(None, _do)
            if result and result.get("id"):
                return ExecutionResult(
                    success=True,
                    order_id=result["id"],
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    exec_price=float(result.get("price", price or 0)),
                    exec_type=order_type.lower(),
                    commission=0.0,
                    message="成功",
                    raw_response=result,
                )
            else:
                return ExecutionResult(False, "", symbol, side, quantity, 0,
                                      order_type.lower(), 0, "Weex API 返回空")
        except Exception as e:
            logger.error(f"[尚书省] Weex 下单失败: {e}")
            return ExecutionResult(False, "", symbol, side, quantity, 0,
                                  order_type.lower(), 0, str(e))

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        from weex import cancel_order as weex_cancel

        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: weex_cancel(self.api_key, self.api_secret, self.api_passphrase, order_id, symbol)
            )
        except Exception as e:
            logger.error(f"[尚书省] Weex 取消订单失败: {e}")
            return False

    async def get_balance(self, asset: str = "USDT") -> float:
        from weex import fetch_balance as weex_balance

        try:
            bal = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: weex_balance(self.api_key, self.api_secret, self.api_passphrase)
            )
            if bal and "balances" in bal:
                for b in bal["balances"]:
                    if b.get("asset") == asset:
                        return float(b.get("free", 0))
            return 0.0
        except Exception as e:
            logger.error(f"[尚书省] Weex 查询余额失败: {e}")
            return 0.0

    async def get_position(self, symbol: str) -> Optional[PositionInfo]:
        return None  # 现货

    async def get_order_status(self, order_id: str, symbol: str) -> Optional[Dict]:
        from weex import fetch_open_orders

        try:
            orders = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: fetch_open_orders(self.api_key, self.api_secret, self.api_passphrase, symbol)
            )
            for o in orders:
                if o.get("id") == order_id:
                    return o
            return None
        except Exception:
            return None


# ============================================================
# 尚书省主调度器
# ============================================================

_ADAPTERS = {
    "binance":    BinanceAdapter,
    "gateio":     GateioAdapter,
    "hyperliquid": HyperliquidAdapter,
    "weex":       WeexAdapter,
}


class ShangshuSheng:
    """
    尚书省 - 交易执行调度

    职责：
      1. 统一入口：execute_open() / execute_close()
      2. 交易所适配：根据 config 选择 Adapter
      3. 交易记录：所有成交写入 xingbu_trades
      4. 资金清算：定期同步账户余额
      5. 断线重连：订单超时重试

    不负责：
      - 风控审核（门下省负责）
      - 信号生成（中书省负责）
    """

    def __init__(self, exchange: str = "binance",
                 api_key: str = "", api_secret: str = "",
                 api_passphrase: str = "",
                 testnet: bool = True,
                 db_path: str = "trading_system.db"):
        if exchange not in _ADAPTERS:
            raise ValueError(f"不支持的交易所: {exchange}，支持: {list(_ADAPTERS.keys())}")

        self.exchange = exchange
        self.testnet = testnet
        self.db_path = db_path
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase

        adapter_cls = _ADAPTERS[exchange]
        if exchange == "weex":
            self._adapter = adapter_cls(api_key, api_secret, api_passphrase, testnet)
        else:
            self._adapter = adapter_cls(api_key, api_secret, testnet)

        self._init_db()
        logger.info(f"[尚书省] 初始化: {exchange} "
                   f"{'(测试网)' if testnet else '(实盘)'}")

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shangshu_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                agent_id TEXT,
                symbol TEXT,
                side TEXT,
                quantity REAL,
                exec_price REAL,
                exec_type TEXT,
                commission REAL,
                success INTEGER,
                message TEXT,
                exchange TEXT,
                is_testnet INTEGER,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            )
        """)
        conn.commit()
        conn.close()

    # ======================== 执行 API ========================

    async def execute_open(self, symbol: str, side: str,
                          quantity: float, order_type: str = "market",
                          price: Optional[float] = None,
                          agent_id: str = "default",
                          stop_loss: Optional[float] = None,
                          take_profit: Optional[float] = None) -> ExecutionResult:
        """
        执行开仓（订单必须先经过门下省审核）
        """
        logger.info(f"[尚书省] 执行开仓: {symbol} {side} × {quantity} "
                   f"@ {price or '市价'} ({order_type})")

        result = await self._adapter.place_order(
            symbol=symbol,
            side=side.upper(),
            order_type=order_type.upper(),
            quantity=quantity,
            price=price,
        )

        self._record_execution(result, agent_id)
        return result

    async def execute_close(self, symbol: str, side: str,
                           quantity: float, order_type: str = "market",
                           price: Optional[float] = None,
                           agent_id: str = "default",
                           reason: str = "signal") -> ExecutionResult:
        """
        执行平仓
        """
        logger.info(f"[尚书省] 执行平仓: {symbol} {side} × {quantity} "
                   f"@ {price or '市价'} 原因:{reason}")

        result = await self._adapter.place_order(
            symbol=symbol,
            side=side.upper(),
            order_type=order_type.upper(),
            quantity=quantity,
            price=price,
        )

        self._record_execution(result, agent_id)
        return result

    async def cancel_open_order(self, order_id: str, symbol: str) -> bool:
        """取消挂单"""
        return await self._adapter.cancel_order(order_id, symbol)

    async def get_balance(self, asset: str = "USDT") -> float:
        """查询账户余额"""
        return await self._adapter.get_balance(asset)

    async def get_executions(self, limit: int = 50,
                             symbol: Optional[str] = None) -> List[Dict]:
        """查询执行历史"""
        conn = sqlite3.connect(self.db_path)
        if symbol:
            rows = conn.execute("""
                SELECT order_id, agent_id, symbol, side, quantity, exec_price,
                       exec_type, commission, success, message, exchange, created_at
                FROM shangshu_executions
                WHERE symbol = ?
                ORDER BY created_at DESC LIMIT ?
            """, (symbol, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT order_id, agent_id, symbol, side, quantity, exec_price,
                       exec_type, commission, success, message, exchange, created_at
                FROM shangshu_executions
                ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
        conn.close()
        cols = ["order_id", "agent_id", "symbol", "side", "quantity",
                "exec_price", "exec_type", "commission", "success",
                "message", "exchange", "created_at"]
        return [dict(zip(cols, r)) for r in rows]

    def is_testnet(self) -> bool:
        return self.testnet

    def get_adapter(self) -> ExchangeAdapter:
        return self._adapter

    # ======================== 私有方法 ========================

    def _record_execution(self, result: ExecutionResult, agent_id: str):
        """记录成交到数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO shangshu_executions
            (order_id, agent_id, symbol, side, quantity, exec_price,
             exec_type, commission, success, message, exchange, is_testnet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result.order_id,
            agent_id,
            result.symbol,
            result.side,
            result.quantity,
            result.exec_price,
            result.exec_type,
            result.commission,
            1 if result.success else 0,
            result.message,
            self.exchange,
            1 if self.testnet else 0,
        ))
        conn.commit()
        conn.close()

        if result.success:
            logger.info(f"[尚书省] 成交: {result.symbol} {result.side} "
                        f"×{result.quantity} @ ${result.exec_price:.4f} "
                        f"手续费:${result.commission:.4f}")
        else:
            logger.error(f"[尚书省] 下单失败: {result.message}")
