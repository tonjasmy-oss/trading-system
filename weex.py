"""
Weex 交易所适配器 - CCXT 兼容接口
用于 live_trading.py 实盘交易

API 文档: https://weexvip.com/docs/
支持: 币币现货交易
"""

import requests
import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

BASE_URL = "https://api.weex.com"  # Weex API 端点
TIMEOUT = 15

# 交易对映射（用户友好符号 -> Weex 标准交易对）
SYMBOL_MAP = {
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
    "BNB": "BNB/USDT",
    "SOL": "SOL/USDT",
    "XRP": "ETH/USDT",  # 待确认
    "ADA": "ADA/USDT",
    "DOGE": "DOGE/USDT",
    "DOT": "DOT/USDT",
    "MATIC": "MATIC/USDT",
    "AVAX": "AVAX/USDT",
    "LINK": "LINK/USDT",
    "UNI": "UNI/USDT",
    "LTC": "LTC/USDT",
    "FIL": "FIL/USDT",
    "ARB": "ARB/USDT",
    "OP": "OP/USDT",
    "SUI": "SUI/USDT",
}

# timeframe 映射：标准 -> Weex interval
TIMEFRAME_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


# ============================================================
# 签名工具
# ============================================================

def _sign(params: Dict, secret_key: str) -> str:
    """HMAC SHA256 签名"""
    import hmac
    import hashlib
    import json

    # 按 key 字母序排序
    sorted_params = sorted(params.items())
    query_string = "&".join([f"{k}={v}" for k, v in sorted_params])

    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return signature


def _headers(api_key: str, sign: str, timestamp: str) -> Dict:
    """构建带签名的请求头"""
    return {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Sign": sign,
        "X-Timestamp": timestamp,
    }


# ============================================================
# 公开接口（无需认证）
# ============================================================

def fetch_ticker(symbol: str) -> Optional[Dict]:
    """
    获取单个交易对实时行情

    Args:
        symbol: 交易对，如 'BTC/USDT' 或 'BTC'

    Returns:
        dict: {
            symbol, pair, price, change_24h, change_24h_pct,
            high_24h, low_24h, volume_24h, timestamp
        }
    """
    # 规范交易对格式
    pair = _normalize_symbol(symbol)

    try:
        url = f"{BASE_URL}/v1/ticker"
        params = {"symbol": pair}
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        data = resp.json()

        if data.get("code") != 0 or "data" not in data:
            logger.warning(f"Weex fetch_ticker 失败: {data}")
            return None

        ticker = data["data"]
        return {
            "symbol": symbol.upper().replace("/USDT", ""),
            "pair": pair,
            "price": float(ticker.get("last", 0)),
            "change_24h": float(ticker.get("change", 0)),
            "change_24h_pct": float(ticker.get("change_pct", 0)),
            "high_24h": float(ticker.get("high", 0)),
            "low_24h": float(ticker.get("low", 0)),
            "volume_24h": float(ticker.get("volume", 0)),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Weex fetch_ticker({symbol}) 异常: {e}")
        return None


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    since: Optional[int] = None,
    limit: int = 100
) -> Optional[List[Dict]]:
    """
    获取 OHLCV K线数据

    Args:
        symbol:     交易对，如 'BTC/USDT'
        timeframe: K线周期：1m, 5m, 15m, 30m, 1h, 4h, 1d
        since:      起始时间戳（毫秒），None 表示最近
        limit:      最大条数，默认 100

    Returns:
        list of dict: [{timestamp, open, high, low, close, volume}, ...]
    """
    pair = _normalize_symbol(symbol)
    interval = TIMEFRAME_MAP.get(timeframe, timeframe)

    params = {
        "symbol": pair,
        "interval": interval,
        "limit": min(limit, 1000),
    }
    if since is not None:
        params["from"] = since // 1000  # Weex 接受秒级时间戳

    try:
        url = f"{BASE_URL}/v1/klines"
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        data = resp.json()

        if data.get("code") != 0 or "data" not in data:
            logger.warning(f"Weex fetch_ohlcv 失败: {data}")
            return None

        raw_list = data["data"]
        # Weex K线格式: [timestamp, open, high, low, close, volume]
        candles = []
        for item in raw_list:
            try:
                candles.append({
                    "timestamp": int(item[0]) * 1000,  # 转为毫秒
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                })
            except (IndexError, ValueError) as e:
                logger.debug(f"跳过异常K线: {item}, error: {e}")
                continue

        logger.info(f"Weex fetch_ohlcv {symbol} {timeframe}: {len(candles)} 条")
        return candles if candles else None

    except requests.exceptions.Timeout:
        logger.warning(f"Weex fetch_ohlcv 超时: {symbol} {timeframe}")
        return None
    except Exception as e:
        logger.error(f"Weex fetch_ohlcv({symbol}, {timeframe}) 异常: {e}")
        return None


def fetch_balance(api_key: str, api_secret: str) -> Optional[Dict]:
    """
    获取账户余额（需认证）

    Args:
        api_key:    API Key
        api_secret: API Secret

    Returns:
        dict: {
            total: float,          # 总资产（折算USD）
            available: float,      # 可用资金
            frozen: float,         # 冻结资金
            balances: [            # 各币种余额
                {"asset": "USDT", "free": ..., "locked": ...},
                ...
            ]
        }
    """
    timestamp = str(int(datetime.now().timestamp() * 1000))
    params = {"timestamp": timestamp}
    sign = _sign(params, api_secret)

    try:
        url = f"{BASE_URL}/v1/account"
        headers = _headers(api_key, sign, timestamp)
        resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        data = resp.json()

        if data.get("code") != 0 or "data" not in data:
            logger.warning(f"Weex fetch_balance 失败: {data}")
            return None

        account = data["data"]
        balances = []
        for b in account.get("balances", []):
            balances.append({
                "asset": b.get("currency", ""),
                "free": float(b.get("free", 0)),
                "locked": float(b.get("locked", 0)),
            })

        total = sum(float(b.get("total", 0)) for b in balances)
        available = sum(float(b.get("free", 0)) for b in balances)
        frozen = sum(float(b.get("locked", 0)) for b in balances)

        return {
            "total": total,
            "available": available,
            "frozen": frozen,
            "balances": balances,
        }

    except Exception as e:
        logger.error(f"Weex fetch_balance 异常: {e}")
        return None


# ============================================================
# 私有接口（需认证）
# ============================================================

def create_order(
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,       # "buy" 或 "sell"
    order_type: str,  # "limit" 或 "market"
    amount: float,
    price: Optional[float] = None
) -> Optional[Dict]:
    """
    创建订单

    Args:
        api_key:    API Key
        api_secret: API Secret
        symbol:     交易对，如 'BTC/USDT'
        side:       'buy' 或 'sell'
        order_type: 'limit' 或 'market'
        amount:     数量
        price:      限价单价格（市价单可为 None）

    Returns:
        dict: {
            id: str,              # 订单ID
            symbol: str,
            side: str,
            type: str,
            price: float,
            amount: float,
            filled: float,        # 已成交数量
            status: str,           # "open" / "filled" / "cancelled"
            created_at: str,
        }
    """
    pair = _normalize_symbol(symbol)
    timestamp = str(int(datetime.now().timestamp() * 1000))

    params = {
        "symbol": pair,
        "side": side.upper(),
        "type": order_type,
        "amount": str(amount),
        "timestamp": timestamp,
    }
    if price is not None:
        params["price"] = str(price)

    sign = _sign(params, api_secret)

    try:
        url = f"{BASE_URL}/v1/order"
        headers = _headers(api_key, sign, timestamp)
        resp = requests.post(url, json=params, headers=headers, timeout=TIMEOUT)
        data = resp.json()

        if data.get("code") != 0 or "data" not in data:
            logger.warning(f"Weex create_order 失败: {data}")
            return None

        order = data["data"]
        return {
            "id": str(order.get("id", "")),
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "price": float(order.get("price", 0) or price or 0),
            "amount": float(order.get("amount", 0)),
            "filled": float(order.get("filled", 0)),
            "status": _map_order_status(order.get("status", "")),
            "created_at": order.get("created_at", ""),
        }

    except Exception as e:
        logger.error(f"Weex create_order 异常: {e}")
        return None


def cancel_order(
    api_key: str,
    api_secret: str,
    order_id: str,
    symbol: str
) -> bool:
    """
    撤销订单

    Args:
        api_key:    API Key
        api_secret: API Secret
        order_id:   订单ID
        symbol:     交易对

    Returns:
        bool: 成功返回 True
    """
    pair = _normalize_symbol(symbol)
    timestamp = str(int(datetime.now().timestamp() * 1000))

    params = {
        "symbol": pair,
        "order_id": order_id,
        "timestamp": timestamp,
    }
    sign = _sign(params, api_secret)

    try:
        url = f"{BASE_URL}/v1/order/cancel"
        headers = _headers(api_key, sign, timestamp)
        resp = requests.post(url, json=params, headers=headers, timeout=TIMEOUT)
        data = resp.json()

        success = data.get("code") == 0
        if not success:
            logger.warning(f"Weex cancel_order({order_id}) 失败: {data}")
        return success

    except Exception as e:
        logger.error(f"Weex cancel_order({order_id}) 异常: {e}")
        return False


def fetch_open_orders(
    api_key: str,
    api_secret: str,
    symbol: Optional[str] = None
) -> List[Dict]:
    """
    查询活跃订单

    Args:
        api_key:    API Key
        api_secret: API Secret
        symbol:     交易对（可选，None 表示所有交易对）

    Returns:
        list of dict: [{id, symbol, side, type, price, amount, filled, status, created_at}, ...]
    """
    timestamp = str(int(datetime.now().timestamp() * 1000))

    params = {"timestamp": timestamp}
    if symbol:
        params["symbol"] = _normalize_symbol(symbol)

    sign = _sign(params, api_secret)

    try:
        url = f"{BASE_URL}/v1/open_orders"
        headers = _headers(api_key, sign, timestamp)
        resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        data = resp.json()

        if data.get("code") != 0 or "data" not in data:
            logger.warning(f"Weex fetch_open_orders 失败: {data}")
            return []

        orders = []
        for o in data["data"]:
            orders.append({
                "id": str(o.get("id", "")),
                "symbol": _denormalize_symbol(o.get("symbol", "")),
                "side": o.get("side", "").lower(),
                "type": o.get("type", ""),
                "price": float(o.get("price", 0)),
                "amount": float(o.get("amount", 0)),
                "filled": float(o.get("filled", 0)),
                "status": _map_order_status(o.get("status", "")),
                "created_at": o.get("created_at", ""),
            })

        return orders

    except Exception as e:
        logger.error(f"Weex fetch_open_orders 异常: {e}")
        return []


# ============================================================
# 辅助函数
# ============================================================

def _normalize_symbol(symbol: str) -> str:
    """将 'BTC' 或 'BTC/USDT' 转换为 Weex 标准 'BTC/USDT'"""
    s = symbol.upper()
    if "/" not in s:
        # 尝试从 SYMBOL_MAP 映射
        return SYMBOL_MAP.get(s, f"{s}/USDT")
    return s


def _denormalize_symbol(pair: str) -> str:
    """将 Weex 标准 'BTC/USDT' 转换为友好 'BTC'"""
    return pair.split("/")[0] if "/" in pair else pair


def _map_order_status(status: str) -> str:
    """映射 Weex 订单状态为统一状态"""
    status_map = {
        "new": "open",
        "open": "open",
        "partial": "open",
        "filled": "filled",
        "completed": "filled",
        "cancelled": "cancelled",
        "cancel": "cancelled",
    }
    return status_map.get(status.lower(), status)


# ============================================================
# 集成到 live_trading.py 的接口
# ============================================================

def get_price(symbol: str) -> Optional[float]:
    """获取单个币种价格（供 live_trading.py 使用）"""
    ticker = fetch_ticker(symbol)
    return ticker["price"] if ticker else None


def get_candles(symbol: str, timeframe: str = "1h", limit: int = 100) -> Optional[List[Dict]]:
    """获取 K线数据（供策略使用）"""
    return fetch_ohlcv(symbol, timeframe, None, limit)


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Weex 适配器测试")
    print("=" * 60)

    # 公开接口测试（无需 API Key）
    print("\n--- fetch_ticker ---")
    for sym in ["BTC", "ETH", "SOL"]:
        t = fetch_ticker(sym)
        if t:
            print(f"  {sym}: ${t['price']:,.2f} (24h: {t['change_24h_pct']:+.2f}%)")
        else:
            print(f"  {sym}: 获取失败")

    print("\n--- fetch_ohlcv ---")
    candles = fetch_ohlcv("BTC", "1h", limit=5)
    if candles:
        print(f"  BTC 1h K线: {len(candles)} 条")
        for c in candles[-3:]:
            from datetime import datetime as dt
            ts = dt.fromtimestamp(c["timestamp"] / 1000)
            print(f"    {ts} O:{c['open']:.2f} H:{c['high']:.2f} L:{c['low']:.2f} C:{c['close']:.2f}")
    else:
        print("  K线获取失败")

    print("\n✅ Weex 适配器测试完成")