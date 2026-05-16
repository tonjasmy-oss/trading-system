"""
Weex 交易所适配器 - REST API v3（合约版）
用于 live_trading.py 实盘交易

API 文档: https://www.weex.com/api-doc
参考: ccxt weex.py (Exchange v3)
支持: USDT本位永续合约（Perpetual Swap）
"""

import base64
import hashlib
import hmac
import json
import logging
import socket
import ssl
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, List, Any

import requests
from urllib3.poolmanager import PoolManager
from requests.adapters import HTTPAdapter

# 抑制 SSH 隧道 localhost 自签名证书的 SSL 警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# ============================================================
# 常量 — Weex 合约 API（api-contract.weex.com）
# ============================================================

BASE_URL = "https://api-contract.weex.com"   # 合约 API
TUNNEL_HOST = "127.0.0.1"
TUNNEL_PORT = 8892                            # SSH 隧道：localhost:8892 → api-contract.weex.com:443
TIMEOUT = 15
API_HOST = "api-contract.weex.com"            # CloudFront SNI 所需

# 合约步长表 — Weex 合约最小下单量（stepSize），key 为币种名（不含USDT）
_CONTRACT_STEP_SIZES = {
    "BTC": 0.001,
    "ETH": 0.01,
    "BNB": 0.01,
    "SOL": 0.1,
    "XRP": 1.0,
    "ADA": 1.0,
    "DOGE": 10.0,
    "DOT": 1.0,
    "MATIC": 1.0,
    "AVAX": 0.1,
    "LINK": 0.1,
    "UNI": 0.1,
    "LTC": 0.1,
    "FIL": 0.1,
    "ARB": 1.0,
    "OP": 1.0,
    "SUI": 10.0,
    "ZEC": 0.01,
}

def _round_quantity(symbol: str, quantity: float) -> float:
    """将数量截断到合约步长精度"""
    coin = symbol.upper().split("/")[0] if "/" in symbol else symbol.upper()
    step = _CONTRACT_STEP_SIZES.get(coin, 0.01)
    return float(int(quantity / step) * step)

# 交易对映射：友好符号 → Weex 内部格式（无斜线）
SYMBOL_MAP = {
    "BTC":   "BTCUSDT",
    "ETH":   "ETHUSDT",
    "BNB":   "BNBUSDT",
    "SOL":   "SOLUSDT",
    "XRP":   "XRPUSDT",
    "ADA":   "ADAUSDT",
    "DOGE":  "DOGEUSDT",
    "DOT":   "DOTUSDT",
    "MATIC": "MATICUSDT",
    "AVAX":  "AVAXUSDT",
    "LINK":  "LINKUSDT",
    "UNI":   "UNIUSDT",
    "LTC":   "LTCUSDT",
    "FIL":   "FILUSDT",
    "ARB":   "ARBUSDT",
    "OP":    "OPUSDT",
    "SUI":   "SUIUSDT",
}

# timeframe 映射：ccxt 标准 → Weex
TIMEFRAME_MAP = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "2h":  "2h",
    "4h":  "4h",
    "6h":  "6h",
    "8h":  "8h",
    "12h": "12h",
    "1d":  "1d",
    "1w":  "1w",
}

# 订单状态映射：Weex → 统一
_ORDER_STATUS_MAP = {
    "NEW":             "open",
    "PARTIALLY_FILLED": "open",
    "FILLED":          "filled",
    "CANCELED":        "cancelled",
    "PENDING_CANCEL":  "open",
    "REJECTED":        "cancelled",
    "EXPIRED":         "cancelled",
}


# ============================================================
# CloudFront SNI 修复（合约 API 通过 CloudFront 需要正确的 SNI）
# ============================================================

class _SNIAdapter(HTTPAdapter):
    """强制设置 TLS SNI 为真实主机名，而非 localhost"""
    def init_poolmanager(self, *args, **kwargs):
        kwargs["assert_hostname"] = False
        kwargs["server_hostname"] = API_HOST
        return super().init_poolmanager(*args, **kwargs)


# 全局 session，隧道模式下复用（自动处理 SNI）
_sni_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _sni_session
    if _sni_session is None:
        _sni_session = requests.Session()
        _sni_session.mount("https://", _SNIAdapter())
    return _sni_session


# ============================================================
# 隧道支持（通过 VPS 代理访问 Weex，解决国际访问限制）
# ============================================================

def _is_tunnel_active() -> bool:
    """检测 Weex 隧道是否可用（连接 localhost:8892）"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        result = sock.connect_ex((TUNNEL_HOST, TUNNEL_PORT)) == 0
    finally:
        sock.close()
    return result


def _get_base_url() -> str:
    """获取 API 基础 URL：隧道可用时走本地代理，否则直连"""
    if _is_tunnel_active():
        return f"https://{TUNNEL_HOST}:{TUNNEL_PORT}"
    return BASE_URL


def _get_verify_ssl() -> bool:
    """隧道模式下跳过 SSL 验证（本地转发使用自签名证书）"""
    return not _is_tunnel_active()


def _get_http_session() -> requests.Session:
    """获取 HTTP session：隧道模式用 SNI 修复 session，直连用默认"""
    if _is_tunnel_active():
        return _get_session()
    return requests.Session()


# ============================================================
# 符号转换
# ============================================================

def _to_weex_symbol(symbol: str) -> str:
    """
    'BTC' 或 'BTC/USDT' → 'BTCUSDT'（Weex 内部格式）
    """
    s = symbol.upper()
    if "/" in s:
        return s.replace("/", "")
    pair = SYMBOL_MAP.get(s)
    if pair:
        coin = pair.replace("USDT", "")
        if coin != s:
            raise ValueError(f"SYMBOL_MAP 映射异常: {s} → {pair}（期望 {s}USDT）")
        return pair
    return f"{s}USDT"


def _from_weex_symbol(pair: str) -> str:
    """
    'BTCUSDT' → 'BTC'
    """
    return pair.replace("USDT", "") if pair.endswith("USDT") else pair


def _to_ccxt_symbol(pair: str) -> str:
    """
    'BTCUSDT' → 'BTC/USDT'
    """
    if pair.endswith("USDT"):
        base = pair[:-4]
        return f"{base}/USDT"
    return pair


# ============================================================
# v3 签名（HMAC-SHA256 → Base64）
# ============================================================

def _sign_v3(
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    method: str,
    endpoint: str,
    params: Optional[Dict] = None,
) -> Dict[str, str]:
    """
    构建 v3 认证 Headers

    签名 payload: timestamp + method + '/' + endpoint [+ json_body]
    签名算法:     HMAC-SHA256 → Base64

    Returns:
        dict: HTTP headers
    """
    timestamp = str(int(time.time() * 1000))
    clean_endpoint = endpoint.lstrip('/')
    payload_str = timestamp + method.upper() + '/' + clean_endpoint

    body_json = ""
    if params and method.upper() in ("POST", "DELETE"):
        body_json = json.dumps(params, separators=(",", ":"))
        payload_str += body_json

    signature = hmac.new(
        api_secret.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sign_b64 = base64.b64encode(signature).decode("utf-8")

    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": sign_b64,
        "ACCESS-PASSPHRASE": api_passphrase,
        "ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }
    return headers


# ============================================================
# HTTP 辅助
# ============================================================

def _get(endpoint: str, params: Optional[Dict] = None,
         api_key: str = "", api_secret: str = "", api_passphrase: str = "") -> Optional[Dict]:
    """GET 请求"""
    url = f"{_get_base_url()}{endpoint}"
    sess = _get_http_session()
    try:
        if api_key and api_secret:
            headers = _sign_v3(api_key, api_secret, api_passphrase, "GET", endpoint)
        else:
            headers = {"User-Agent": "trading-system/2.0"}
        # 隧道模式下显式设置 Host 头以通过 CloudFront
        if _is_tunnel_active():
            headers["Host"] = API_HOST
        resp = sess.get(url, params=params, headers=headers,
                        timeout=TIMEOUT, verify=_get_verify_ssl())
        return _parse_response(resp)
    except requests.exceptions.Timeout:
        logger.warning(f"Weex GET 超时: {endpoint}")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"Weex 连接失败: {endpoint}: {e}")
        return None
    except Exception as e:
        logger.error(f"Weex GET 异常: {endpoint}: {e}")
        return None


def _post(endpoint: str, params: Optional[Dict] = None,
          api_key: str = "", api_secret: str = "", api_passphrase: str = "") -> Optional[Dict]:
    """POST 请求"""
    url = f"{_get_base_url()}{endpoint}"
    sess = _get_http_session()
    try:
        headers = _sign_v3(api_key, api_secret, api_passphrase, "POST", endpoint, params or {})
        if _is_tunnel_active():
            headers["Host"] = API_HOST
        body = json.dumps(params or {}, separators=(",", ":"))
        logger.debug(f"Weex POST {endpoint} body={body}")
        resp = sess.post(url, data=body, headers=headers,
                         timeout=TIMEOUT, verify=_get_verify_ssl())
        return _parse_response(resp)
    except requests.exceptions.Timeout:
        logger.warning(f"Weex POST 超时: {endpoint}")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"Weex 连接失败: {endpoint}: {e}")
        return None
    except Exception as e:
        logger.error(f"Weex POST 异常: {endpoint}: {e}")
        return None


def _delete(endpoint: str, params: Optional[Dict] = None,
            api_key: str = "", api_secret: str = "", api_passphrase: str = "") -> Optional[Dict]:
    """DELETE 请求"""
    url = f"{_get_base_url()}{endpoint}"
    sess = _get_http_session()
    try:
        headers = _sign_v3(api_key, api_secret, api_passphrase, "DELETE", endpoint, params or {})
        if _is_tunnel_active():
            headers["Host"] = API_HOST
        body = json.dumps(params or {}, separators=(",", ":"))
        resp = sess.delete(url, data=body, headers=headers,
                           timeout=TIMEOUT, verify=_get_verify_ssl())
        return _parse_response(resp)
    except requests.exceptions.Timeout:
        logger.warning(f"Weex DELETE 超时: {endpoint}")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"Weex 连接失败: {endpoint}: {e}")
        return None
    except Exception as e:
        logger.error(f"Weex DELETE 异常: {endpoint}: {e}")
        return None


def _parse_response(resp: requests.Response) -> Optional[Dict]:
    """解析 v3 响应，检查错误码"""
    try:
        data = resp.json()
    except ValueError:
        logger.error(f"Weex 非JSON响应: {resp.status_code} {resp.text[:200]}")
        return None

    if resp.status_code >= 400:
        logger.warning(f"Weex HTTP {resp.status_code}: {data}")
        return None

    # 部分合约 endpoint（如 balance）直接返回 list，无需 code 检查
    if isinstance(data, list):
        return data

    # v3 错误格式: {"code": -1047, "msg": "API auth failed"}
    code = data.get("code")
    if code is not None and code != 0:
        msg = data.get("msg", "未知错误")
        logger.warning(f"Weex API 错误 (code={code}): {msg}")
        return None

    return data


# ============================================================
# 公开接口 — 无需认证
# ============================================================

def fetch_ticker(symbol: str) -> Optional[Dict]:
    """
    获取单个交易对 24hr 行情

    GET /capi/v3/market/ticker/24hr?symbol=BTCUSDT

    Returns:
        dict: {
            symbol, pair, price, change_24h, change_24h_pct,
            high_24h, low_24h, volume_24h, bid, ask, timestamp
        }
    """
    weex_sym = _to_weex_symbol(symbol)

    data = _get("/capi/v3/market/ticker/24hr", params={"symbol": weex_sym})
    if not data:
        return None

    # 合约 API ticker 可能返回列表，取第一个元素
    ticker = data
    if isinstance(data, list) and len(data) > 0:
        ticker = data[0]

    try:
        return {
            "symbol": _from_weex_symbol(ticker.get("symbol", weex_sym)),
            "pair": _to_ccxt_symbol(ticker.get("symbol", weex_sym)),
            "price": float(ticker.get("lastPrice", 0)),
            "change_24h": float(ticker.get("priceChange", 0)),
            "change_24h_pct": float(ticker.get("priceChangePercent", 0)) / 100,
            "high_24h": float(ticker.get("highPrice", 0)),
            "low_24h": float(ticker.get("lowPrice", 0)),
            "volume_24h": float(ticker.get("volume", 0)),
            "bid": float(ticker.get("bidPrice", 0)),
            "ask": float(ticker.get("askPrice", 0)),
            "open_24h": float(ticker.get("openPrice", 0)),
            "timestamp": datetime.now().isoformat(),
        }
    except (ValueError, TypeError) as e:
        logger.error(f"Weex 解析 ticker 失败: {e}, raw={ticker}")
        return None


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    since: Optional[int] = None,
    limit: int = 100,
) -> Optional[List[Dict]]:
    """
    获取 OHLCV K线数据

    GET /capi/v3/market/klines?symbol=BTCUSDT&interval=1h&limit=100

    Args:
        since: 起始时间戳（毫秒）
        limit: 最大 1000

    Returns:
        list of dict: [{timestamp, open, high, low, close, volume}, ...]
    """
    weex_sym = _to_weex_symbol(symbol)
    interval = TIMEFRAME_MAP.get(timeframe, timeframe)

    params: Dict[str, Any] = {
        "symbol": weex_sym,
        "interval": interval,
        "limit": min(limit, 1000),
    }
    if since is not None:
        params["endTime"] = since

    data = _get("/capi/v3/market/klines", params=params)
    if not data:
        return None

    # 合约 API klines 返回 [[ts, open, high, low, close, vol, ...], ...]
    raw_list = data if isinstance(data, list) else data.get("data", [])
    if not isinstance(raw_list, list):
        logger.warning(f"Weex klines 格式异常: {type(raw_list)}")
        return None

    candles = []
    for item in raw_list:
        try:
            candles.append({
                "timestamp": int(item[0]),
                "open":      float(item[1]),
                "high":      float(item[2]),
                "low":       float(item[3]),
                "close":     float(item[4]),
                "volume":    float(item[5]),
            })
        except (IndexError, ValueError) as e:
            logger.debug(f"跳过异常K线: {item}, error: {e}")
            continue

    logger.info(f"Weex fetch_ohlcv {symbol} {timeframe}: {len(candles)} 条")
    return candles if candles else None


def fetch_order_book(symbol: str, limit: int = 10) -> Optional[Dict]:
    """
    获取订单簿深度

    GET /capi/v3/market/depth?symbol=BTCUSDT&limit=10

    Returns:
        dict: {bids: [[price, qty], ...], asks: [[price, qty], ...]}
    """
    weex_sym = _to_weex_symbol(symbol)
    data = _get("/capi/v3/market/depth", params={"symbol": weex_sym, "limit": limit})
    if not data:
        return None

    return {
        "symbol": symbol.upper(),
        "pair": _to_ccxt_symbol(weex_sym),
        "bids": [[float(b[0]), float(b[1])] for b in data.get("bids", [])],
        "asks": [[float(a[0]), float(a[1])] for a in data.get("asks", [])],
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# 私有接口 — 需 API Key + Secret + Passphrase
# ============================================================

def fetch_balance(
    api_key: str,
    api_secret: str,
    api_passphrase: str = "",
) -> Optional[Dict]:
    """
    获取合约账户余额

    GET /capi/v3/account/balance

    合约 API 响应格式：
        [{"asset":"USDT","balance":"318.48","availableBalance":"318.48",
          "frozen":"0","unrealizePnl":"0"}]

    Returns:
        dict: {
            total: float,
            available: float,
            frozen: float,
            balances: [{"asset": "USDT", "free": ..., "locked": ...}, ...]
        }
    """
    data = _get("/capi/v3/account/balance",
                api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
    if not data:
        return None

    try:
        # 合约 API 返回列表: [{"asset":"USDT", ...}, ...]
        balances_raw = data if isinstance(data, list) else data.get("data", data)
        if not isinstance(balances_raw, list):
            balances_raw = [balances_raw] if isinstance(balances_raw, dict) else []

        balances = []
        total = 0.0
        available = 0.0
        frozen = 0.0

        for b in balances_raw:
            # 合约 API 字段: balance / availableBalance / frozen
            bal = float(b.get("balance", b.get("free", 0)))
            avail = float(b.get("availableBalance", b.get("free", 0)))
            locked = float(b.get("frozen", b.get("locked", 0)))

            balances.append({
                "asset": b.get("asset", ""),
                "free": avail,
                "locked": locked,
            })
            total += bal
            available += avail
            frozen += locked

        return {
            "total": total,
            "available": available,
            "frozen": frozen,
            "balances": balances,
        }
    except Exception as e:
        logger.error(f"Weex 解析余额失败: {e}, raw={data}")
        return None


def create_order(
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    symbol: str,
    side: str,
    order_type: str,
    amount: float,
    price: Optional[float] = None,
    client_order_id: Optional[str] = None,
    reduce_only: bool = False,
    position_side: Optional[str] = None,
) -> Optional[Dict]:
    """
    创建合约订单

    POST /capi/v3/order

    Request body:
        {"symbol":"ETHUSDT","side":"BUY","type":"LIMIT","quantity":"1.5","price":"2000","positionSide":"BOTH"}

    合约额外字段:
        reduceOnly: 仅减仓（平仓模式）
        positionSide: 持仓方向（BOTH=单向模式, LONG/SHORT=双向模式，默认BOTH）

    Response:
        {"code":0, "data":{"orderId":"736557215397183592", ...}}

    Returns:
        dict: {id, symbol, side, type, price, amount, filled, status, created_at, ...}
    """
    weex_sym = _to_weex_symbol(symbol)

    # 数量对齐合约步长
    rounded_qty = _round_quantity(symbol, amount)

    # positionSide: Weex 合约默认单向模式用 LONG/SHORT（根据 side 推断）
    if position_side is None:
        position_side = "LONG" if side.upper() == "BUY" else "SHORT"

    params: Dict[str, Any] = {
        "symbol": weex_sym,
        "side": side.upper(),
        "type": order_type.upper(),
        "quantity": str(rounded_qty),
        "positionSide": position_side,
    }
    if order_type.upper() == "LIMIT" and price is not None:
        params["price"] = str(price)
    if reduce_only:
        params["reduceOnly"] = True
    if client_order_id:
        params["newClientOrderId"] = client_order_id
    else:
        params["newClientOrderId"] = str(uuid.uuid4())

    data = _post("/capi/v3/order", params=params,
                 api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
    if not data:
        return None

    # 合约 API 响应: {"code":0, "data":{...}}
    order_data = data.get("data", data) if isinstance(data, dict) else {}

    try:
        return {
            "id": str(order_data.get("orderId", "")),
            "client_order_id": order_data.get("clientOrderId", ""),
            "symbol": symbol,
            "side": side.lower(),
            "type": order_type.lower(),
            "price": float(price or 0),
            "amount": float(rounded_qty),
            "filled": 0.0,
            "status": "open",
            "created_at": datetime.fromtimestamp(
                order_data.get("transactTime", 0) / 1000
            ).isoformat() if order_data.get("transactTime") else "",
            "raw": order_data,
        }
    except Exception as e:
        logger.error(f"Weex 解析订单失败: {e}, raw={data}")
        return None


def cancel_order(
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    order_id: str,
    symbol: str,
) -> bool:
    """
    撤销订单

    DELETE /capi/v3/order
    Body: {"symbol":"ETHUSDT","orderId":"736557215397183592"}
    """
    weex_sym = _to_weex_symbol(symbol)
    params = {"symbol": weex_sym, "orderId": order_id}

    data = _delete("/capi/v3/order", params=params,
                   api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
    if data is None:
        return False
    return data.get("code", -1) == 0


def fetch_open_orders(
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    symbol: Optional[str] = None,
) -> List[Dict]:
    """
    查询活跃订单

    GET /capi/v3/openOrders?symbol=ETHUSDT
    """
    params = {}
    if symbol:
        params["symbol"] = _to_weex_symbol(symbol)

    data = _get("/capi/v3/openOrders", params=params,
                api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
    if not data:
        return []

    # 响应可能直接是 list 或 {"data": [...]}
    orders_raw = data if isinstance(data, list) else data.get("data", [])
    if not isinstance(orders_raw, list):
        return []

    orders = []
    for o in orders_raw:
        try:
            sym = o.get("symbol", "")
            orders.append({
                "id": str(o.get("orderId", "")),
                "client_order_id": o.get("clientOrderId", ""),
                "symbol": _from_weex_symbol(sym),
                "pair": _to_ccxt_symbol(sym),
                "side": o.get("side", "").lower(),
                "type": o.get("type", "").lower(),
                "price": float(o.get("price", 0)),
                "amount": float(o.get("origQty", 0)),
                "filled": float(o.get("executedQty", 0)),
                "status": _ORDER_STATUS_MAP.get(o.get("status", ""), o.get("status", "").lower()),
                "created_at": datetime.fromtimestamp(
                    o.get("time", 0) / 1000
                ).isoformat() if o.get("time") else "",
            })
        except Exception as e:
            logger.debug(f"Weex 跳过异常订单: {e}, raw={o}")
            continue

    return orders


def fetch_order(
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    order_id: str,
    symbol: Optional[str] = None,
) -> Optional[Dict]:
    """
    查询单个订单

    GET /capi/v3/order?orderId=736557215397183592&symbol=ETHUSDT
    """
    params: Dict[str, str] = {"orderId": str(order_id)}
    if symbol:
        params["symbol"] = _to_weex_symbol(symbol)

    data = _get("/capi/v3/order", params=params,
                api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
    if not data:
        return None

    # 合约 API 响应: {"code":0, "data":{...}}
    order_data = data.get("data", data) if isinstance(data, dict) else {}

    try:
        sym = order_data.get("symbol", "")
        return {
            "id": str(order_data.get("orderId", "")),
            "client_order_id": order_data.get("clientOrderId", ""),
            "symbol": _from_weex_symbol(sym),
            "pair": _to_ccxt_symbol(sym),
            "side": order_data.get("side", "").lower(),
            "type": order_data.get("type", "").lower(),
            "price": float(order_data.get("price", 0)),
            "amount": float(order_data.get("origQty", 0)),
            "filled": float(order_data.get("executedQty", 0)),
            "avg_price": float(order_data.get("avgPrice", 0) or order_data.get("price", 0)),
            "status": _ORDER_STATUS_MAP.get(order_data.get("status", ""), order_data.get("status", "").lower()),
            "created_at": datetime.fromtimestamp(
                order_data.get("time", 0) / 1000
            ).isoformat() if order_data.get("time") else "",
        }
    except Exception as e:
        logger.error(f"Weex 解析订单失败: {e}, raw={data}")
        return None


# ============================================================
# 集成到 live_trading.py 的简化接口
# ============================================================

def get_price(symbol: str) -> Optional[float]:
    """获取单个币种价格（供 live_trading.py 使用）"""
    ticker = fetch_ticker(symbol)
    return ticker["price"] if ticker else None


def get_candles(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 100,
) -> Optional[List[Dict]]:
    """获取 K线数据（供策略使用）"""
    return fetch_ohlcv(symbol, timeframe, None, limit)


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Weex 合约 API v3 适配器测试")
    print("=" * 60)

    # 公开接口测试（无需 API Key）
    print("\n--- fetch_ticker ---")
    for sym in ["BTC", "ETH", "SOL"]:
        t = fetch_ticker(sym)
        if t:
            print(f"  {sym}: ${t['price']:,.2f} "
                  f"(24h: {t['change_24h_pct']:+.2f}% "
                  f"Bid:{t.get('bid',0):,.2f} Ask:{t.get('ask',0):,.2f})")
        else:
            print(f"  {sym}: 获取失败")

    print("\n--- fetch_ohlcv ---")
    candles = fetch_ohlcv("BTC", "1h", limit=5)
    if candles:
        print(f"  BTC 1h K线: {len(candles)} 条")
        for c in candles[-3:]:
            ts = datetime.fromtimestamp(c["timestamp"] / 1000)
            print(f"    {ts} O:{c['open']:.2f} H:{c['high']:.2f} "
                  f"L:{c['low']:.2f} C:{c['close']:.2f} V:{c['volume']:.2f}")
    else:
        print("  K线获取失败")

    print("\n--- fetch_order_book ---")
    ob = fetch_order_book("ETH", limit=3)
    if ob:
        print(f"  ETH 买一: {ob['bids'][0] if ob['bids'] else 'N/A'}")
        print(f"  ETH 卖一: {ob['asks'][0] if ob['asks'] else 'N/A'}")

    print("\n✅ Weex 合约 v3 适配器测试完成")
