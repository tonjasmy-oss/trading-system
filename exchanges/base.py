"""
交易所适配器基类

定义统一接口，各交易所实现需遵循：
- get_balance() - 查询余额
- get_positions() - 查询持仓
- get_ticker() - 查询当前价格
- place_order() - 下单
- cancel_order() - 撤单
- get_orders() - 查询订单
- get_klines() - 获取 K线数据
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Literal
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


@dataclass
class Balance:
    """账户余额"""
    asset: str
    free: float      # 可用数量
    locked: float   # 锁定数量（挂单/保证金）
    total: float    # 总计


@dataclass
class Position:
    """持仓信息"""
    symbol: str
    side: Literal["LONG", "SHORT"]
    quantity: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    leverage: int
    liquidation_price: Optional[float] = None
    isolated_margin: Optional[float] = None


@dataclass
class Order:
    """订单信息"""
    order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    type: Literal["MARKET", "LIMIT", "STOP", "STOP_MARKET"]
    quantity: float
    price: Optional[float]
    status: Literal["NEW", "PARTIAL_FILLED", "FILLED", "CANCELED", "REJECTED"]
    filled_quantity: float
    avg_fill_price: Optional[float]
    created_at: str


@dataclass
class Ticker:
    """行情数据"""
    symbol: str
    last_price: float
    bid_price: float
    ask_price: float
    volume_24h: float
    change_24h_pct: float


@dataclass
class Kline:
    """K线数据"""
    time: int       # 毫秒时间戳
    open: float
    high: float
    low: float
    close: float
    volume: float


class ExchangeAdapter(ABC):
    """交易所适配器基类"""
    
    # 子类需要设置
    exchange_id: str = "unknown"
    base_url: str = ""
    testnet_url: Optional[str] = None
    
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        testnet: bool = False,
        proxy: Optional[str] = None
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.testnet = testnet
        self.proxy = proxy
        self.session = self._create_session()
    
    def _create_session(self):
        """创建 HTTP 会话"""
        import requests
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "VergeX-Trading-System/1.0"
        })
        if self.proxy:
            session.proxies = {"https": self.proxy, "http": self.proxy}
        return session
    
    @property
    def base_url(self) -> str:
        """获取当前网络的 base URL"""
        if self.testnet and self.testnet_url:
            return self.testnet_url
        return self._base_url
    
    @property
    def _base_url(self) -> str:
        """子类需要覆盖：主网 URL"""
        raise NotImplementedError
    
    def _sign_request(self, params: dict) -> dict:
        """签名请求（子类实现）"""
        raise NotImplementedError
    
    def _handle_response(self, response) -> dict:
        """统一响应处理"""
        if response.status_code == 200 or response.status_code == 200:
            return response.json()
        elif response.status_code == 400:
            error_data = response.json()
            code = error_data.get("code", error_data.get("code", ""))
            msg = error_data.get("msg", error_data.get("message", ""))
            # 常见错误映射
            error_map = {
                "-2015": ("API key invalid or IP not whitelisted", "API Key 无效或 IP 未白名单"),
                "-1022": ("Invalid signature", "签名错误，请检查 API Key 和 Secret"),
                "-1013": ("Invalid symbol", "交易对不存在或已下市"),
                "余额不足": ("Insufficient balance", "余额不足"),
            }
            for err_code, (en_msg, zh_msg) in error_map.items():
                if str(err_code) in str(code) or err_code in msg:
                    raise ExchangeError(en_msg, zh_msg, code)
            raise ExchangeError(msg, msg, code)
        elif response.status_code == 429:
            raise ExchangeError("Rate limit exceeded", "请求频率超限，请稍后重试", "RATE_LIMIT")
        else:
            raise ExchangeError(f"HTTP {response.status_code}", f"请求失败 ({response.status_code})", response.status_code)
    
    # ─── 标准接口 ───────────────────────────────────────────────
    
    @abstractmethod
    def get_balance(self) -> list[Balance]:
        """获取账户余额"""
        pass
    
    @abstractmethod
    def get_positions(self) -> list[Position]:
        """获取当前持仓"""
        pass
    
    @abstractmethod
    def get_ticker(self, symbol: str) -> Ticker:
        """获取当前行情"""
        pass
    
    @abstractmethod
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
        """下单"""
        pass
    
    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """撤单"""
        pass
    
    @abstractmethod
    def get_orders(self, symbol: str, limit: int = 50) -> list[Order]:
        """查询订单历史"""
        pass
    
    @abstractmethod
    def get_klines(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 200,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> list[Kline]:
        """获取 K线数据"""
        pass
    
    def get_server_time(self) -> int:
        """获取交易所服务器时间（毫秒）"""
        import time
        return int(time.time() * 1000)


class ExchangeError(Exception):
    """交易所错误异常"""
    def __init__(self, english: str, chinese: str, code: str):
        self.english = english
        self.chinese = chinese
        self.code = code
        super().__init__(f"[{code}] {chinese}")