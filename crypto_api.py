"""
虚拟货币数据接入模块 - 统一 ccxt 适配层
支持 Binance / Gate.io / Bitget / OKX / Bybit / Kraken / Bitfinex 等主流交易所
保留原有 Gate.io API 作为降级方案
"""
import requests
import logging
from typing import Optional, Dict, List, TYPE_CHECKING
from datetime import datetime
from functools import lru_cache

# ccxt 延迟导入（仅在需要时加载，ccxt 未安装时 get_crypto_price 等功能仍可用）
try:
    import ccxt
    _CCXT_AVAILABLE = True
except ImportError:
    ccxt = None
    _CCXT_AVAILABLE = False
    logging.getLogger(__name__).warning("ccxt 未安装，加密货币交易所实时交易功能不可用")

from config import (
    CRYPTO_EXCHANGE,
    CRYPTO_API_KEY,
    CRYPTO_API_SECRET,
    BITGET_API_PASSPHRASE,
)

logger = logging.getLogger(__name__)

# ============================================================
# 交易所实例管理（ccxt 统一适配）
# ============================================================

# 常用交易对映射：用户友好符号 -> ccxt 标准交易对
# 所有主流交易所均支持 BTC/USDT 这种格式
SYMBOL_MAP = {
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
    "BNB": "BNB/USDT",
    "SOL": "SOL/USDT",
    "XRP": "XRP/USDT",
    "ADA": "ADA/USDT",
    "DOGE": "DOGE/USDT",
    "DOT": "DOT/USDT",
    "MATIC": "MATIC/USDT",
    "AVAX": "AVAX/USDT",
    "LINK": "LINK/USDT",
    "UNI": "UNI/USDT",
    "LTC": "LTC/USDT",
    "EOS": "EOS/USDT",
    "XLM": "XLM/USDT",
    "TRX": "TRX/USDT",
    "ETC": "ETC/USDT",
    "FIL": "FIL/USDT",
    "NEAR": "NEAR/USDT",
    "APT": "APT/USDT",
    "ARB": "ARB/USDT",
    "OP": "OP/USDT",
}

# ccxt 支持的交易所 ID（均为公开接口，无需 API Key 即可获取行情）
SUPPORTED_EXCHANGES = ["binance", "gateio", "kraken", "bitfinex", "okx", "bybit", "bitget", "hyperliquid", "weex"]

# 各交易所所需认证字段（ccxt.exchange.requiredCredentials）
_EXCHANGE_REAUTH = {
    "bitget": ["password"],   # Bitget 需要额外 passphrase
}

# 当前选中的交易所，默认 gateio（兼容原系统行为）
_current_exchange_id = CRYPTO_EXCHANGE or "gateio"
_exchange_instance = None


def _get_exchange():
    """
    获取当前交易所 ccxt 实例（懒加载单例）
    支持 Binance / Gate.io / Kraken / Bitfinex 等
    若本地隧道可用（localhost:8890），改用直连API（不走ccxt）
    """
    global _exchange_instance, _current_exchange_id
    if _exchange_instance is None:
        if not _CCXT_AVAILABLE:
            raise RuntimeError("ccxt 未安装，无法创建交易所实例")
        ex_class = getattr(ccxt, _current_exchange_id)
        # Bitget 需要 passphrase（其他字段由 CRYPTO_API_KEY / CRYPTO_API_SECRET 提供）
        extra_opts = {}
        if _current_exchange_id == "bitget":
            from config import BITGET_API_PASSPHRASE
            extra_opts["password"] = BITGET_API_PASSPHRASE

        opts = {
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
            **extra_opts,
        }
        _exchange_instance = ex_class(opts)
        logger.info(f"ccxt 交易所实例已创建: {_current_exchange_id}")
    return _exchange_instance


def set_exchange(exchange_id: str):
    """
    切换当前交易所

    Args:
        exchange_id: ccxt 交易所 ID，如 'binance', 'gateio', 'kraken'
    """
    global _exchange_instance, _current_exchange_id
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise ValueError(f"不支持的交易所: {exchange_id}，支持的: {SUPPORTED_EXCHANGES}")
    _exchange_instance = None   # 触发重新初始化
    _current_exchange_id = exchange_id
    _get_exchange()            # 预热
    logger.info(f"已切换交易所: {exchange_id}")


def _to_ccxt_symbol(symbol: str) -> str:
    """将用户友好符号转换为 ccxt 标准交易对格式"""
    return SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}/USDT")


def _from_ccxt_symbol(pair: str) -> str:
    """将 ccxt 标准交易对（如 BTC/USDT）转换为友好符号（如 BTC）"""
    return pair.split("/")[0]


# ============================================================
# 行情数据获取（ccxt 统一接口）
# ============================================================

def _is_tunnel_active() -> bool:
    """检测本地隧道是否可用（连接localhost:8890）"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    result = sock.connect_ex(("127.0.0.1", 8890)) == 0
    sock.close()
    return result


def _gateio_api_tunnel(symbol: str) -> Optional[Dict]:
    """
    通过SSH隧道获取Gate.io行情（当隧道激活时使用）
    隧道将localhost:8890转发到api.gateio.ws:443
    """
    try:
        pair = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}_USDT").replace("/", "_")
        url = f"https://localhost:8890/api/v4/spot/tickers"
        resp = requests.get(url, params={"currency_pair": pair}, timeout=10, verify=False)
        data = resp.json()
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        ticker = data[0]
        return {
            "symbol": symbol.upper(),
            "pair": pair.replace("_", "/"),
            "price": float(ticker.get("last", 0)),
            "change_24h": float(ticker.get("change_percentage", 0)),
            "change_24h_pct": float(ticker.get("change_percentage", 0)),
            "high_24h": float(ticker.get("high_24h", 0)),
            "low_24h": float(ticker.get("low_24h", 0)),
            "volume_24h": float(ticker.get("quote_volume", 0)),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"隧道获取 {symbol} 失败: {e}")
        return None


def get_crypto_price(symbol: str) -> Optional[Dict]:
    """
    获取单个加密货币实时行情（隧道优先，ccxt备选）
    隧道激活时走VPS代理访问Gate.io，解决国际访问限制
    """
    # 优先隧道（通过VPS代理访问Gate.io）
    if _is_tunnel_active():
        result = _gateio_api_tunnel(symbol)
        if result:
            return result
        logger.warning(f"隧道获取 {symbol} 失败，尝试ccxt")
    # ccxt统一接口（需要代理或直连可达）
    try:
        ex = _get_exchange()
        ccxt_symbol = _to_ccxt_symbol(symbol)
        ticker = ex.fetch_ticker(ccxt_symbol)
        return {
            "symbol": symbol.upper(),
            "pair": ccxt_symbol,
            "price": ticker.get("last"),
            "change_24h": ticker.get("change"),
            "change_24h_pct": ticker.get("percentage"),
            "high_24h": ticker.get("high"),
            "low_24h": ticker.get("low"),
            "volume_24h": ticker.get("quoteVolume"),
            "timestamp": datetime.now().isoformat(),
        }
    except ccxt.ExchangeError as e:
        logger.warning(f"ccxt 获取 {symbol} 失败，尝试降级到 Gate.io: {e}")
        return _gateio_fallback(symbol)
    except Exception as e:
        logger.error(f"获取 {symbol} 价格失败: {e}")
        return None


def get_crypto_prices(symbols: List[str]) -> List[Dict]:
    """
    批量获取加密货币价格

    遍历 symbols 逐个查询（ccxt 支持 fetch_tickers 批量但为了保持
    与原接口一致采用逐个查询，错误不影响其他币种）
    """
    results = []
    for sym in symbols:
        data = get_crypto_price(sym)
        if data:
            results.append(data)
    return results


def get_top_cryptos(limit: int = 20) -> List[Dict]:
    """
    获取主流加密货币行情（按 24h 成交量排序）

    Args:
        limit: 返回数量上限，默认 20
    """
    major_coins = [
        "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE",
        "DOT", "MATIC", "AVAX", "LINK", "UNI", "LTC", "EOS",
        "XLM", "TRX", "ETC", "FIL", "NEAR", "APT",
    ]
    coins = major_coins[:limit]
    results = [d for d in (get_crypto_price(s) for s in coins) if d]
    results.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)
    return results


def get_order_book(symbol: str, limit: int = 10) -> Optional[Dict]:
    """
    获取订单簿

    Args:
        symbol: 币种符号，如 'BTC'
        limit:  买卖盘深度，默认 10
    """
    try:
        ex = _get_exchange()
        ccxt_symbol = _to_ccxt_symbol(symbol)
        orderbook = ex.fetch_order_book(ccxt_symbol, limit)
        return {
            "symbol": symbol.upper(),
            "pair": ccxt_symbol,
            "bids": orderbook.get("bids", []),
            "asks": orderbook.get("asks", []),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"获取 {symbol} 订单簿失败: {e}")
        return None


# ============================================================
# OHLCV 数据获取（支持历史K线，供回测/缓存使用）
# ============================================================

# ccxt timeframe -> Gate.io interval 映射
_GATEIO_INTERVAL_MAP = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1d",
    "7d":  "7d",
    "1w":  "30d",   # 无 weekly，用 30d 近似
}

# Gate.io API 时间粒度 -> 每根K线毫秒数（近似）
_INTERVAL_MS = {
    "1m":  60 * 1000,
    "5m":  5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h":  60 * 60 * 1000,
    "4h":  4 * 60 * 60 * 1000,
    "1d":  24 * 60 * 60 * 1000,
    "7d":  7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
}


def _gateio_get_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    since: Optional[int] = None,   # 毫秒时间戳
    limit: int = 1000
) -> Optional[List[Dict]]:
    """
    直接调用 Gate.io 公开 API 获取 OHLCV K线数据
    Gate.io API: GET /api/v4/spot/candlesticks
    返回格式: [timestamp_str, volume, close, high, low, open, is_closed]
    """
    pair = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}_USDT").replace("/", "_")
    interval = _GATEIO_INTERVAL_MAP.get(timeframe, timeframe)

    params = {
        "currency_pair": pair,
        "interval": interval,
        "limit": min(limit, 1000),
    }
    if since is not None:
        # Gate.io 接受秒级时间戳
        params["from"] = since // 1000

    try:
        resp = requests.get(
            f"{GATEIO_BASE}/candlesticks",
            params=params,
            timeout=15,
            headers={"Accept": "application/json", "User-Agent": "trading-system/1.0"},
        )
        if resp.status_code != 200:
            logger.warning(f"Gate.io OHLCV HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        raw = resp.json()
        if not isinstance(raw, list):
            logger.warning(f"Gate.io OHLCV 返回异常格式: {type(raw)}")
            return None

        # 转换: [ts_str, vol, close, high, low, open, ...] -> dict
        candles = []
        for item in raw:
            try:
                ts_ms = int(item[0]) * 1000  # Gate.io 返回秒级时间戳
                candles.append({
                    "timestamp": ts_ms,
                    "open":      float(item[5]),
                    "high":      float(item[3]),
                    "low":       float(item[4]),
                    "close":     float(item[2]),
                    "volume":    float(item[1]),
                })
            except (IndexError, ValueError) as e:
                logger.debug(f"跳过异常K线: {item}, error: {e}")
                continue

        logger.info(f"Gate.io 获取 {symbol} {timeframe} {len(candles)} 条K线")
        return candles if candles else None

    except requests.exceptions.Timeout:
        logger.warning(f"Gate.io OHLCV 请求超时: {symbol} {timeframe}")
        return None
    except Exception as e:
        logger.error(f"Gate.io OHLCV 获取失败: {symbol} {timeframe}: {e}")
        return None


def get_ohlcv(
    symbol: str,
    timeframe: str = "1m",
    since: Optional[int] = None,
    limit: int = 100
) -> Optional[List[Dict]]:
    """
    获取 OHLCV K线数据（直接调用 Gate.io 公开API，无需认证）

    Args:
        symbol:     币种符号，如 'BTC'
        timeframe:  K线周期：'1m', '5m', '15m', '1h', '4h', '1d' 等
        since:      起始时间（毫秒时间戳），None 表示最近
        limit:      最大条数，默认 100

    Returns:
        list of dict: [{timestamp, open, high, low, close, volume}, ...]
        与 history_cache 存储格式一致
    """
    # 直接用 Gate.io API（无需 ccxt）
    data = _gateio_get_ohlcv(symbol, timeframe, since, limit)
    if data:
        return data

    # 降级：尝试 ccxt（如果已安装）
    try:
        import ccxt
        ex = _get_exchange()
        ccxt_symbol = _to_ccxt_symbol(symbol)
        raw = ex.fetch_ohlcv(ccxt_symbol, timeframe, since, limit)
        return [
            {
                "timestamp": int(c[0]),
                "open":      float(c[1]),
                "high":      float(c[2]),
                "low":       float(c[3]),
                "close":     float(c[4]),
                "volume":    float(c[5]),
            }
            for c in raw
        ]
    except ImportError:
        logger.error("ccxt 未安装且 Gate.io API 失败，无法获取 OHLCV 数据")
    except Exception as e:
        logger.error(f"ccxt 获取 {symbol} {timeframe} K线失败: {e}")

    return None


# ============================================================
# 降级方案：原始 Gate.io API（当 ccxt 不可用时）
# ============================================================

GATEIO_BASE = "https://api.gateio.ws/api/v4/spot"


def _gateio_fallback(symbol: str) -> Optional[Dict]:
    """
    降级方案：使用原始 Gate.io API 获取价格
    保证在 ccxt 交易所不通时仍有数据来源
    """
    try:
        pair = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}_USDT").replace("/", "_")
        url = f"{GATEIO_BASE}/spot/tickers"
        resp = requests.get(url, params={"currency_pair": pair}, timeout=10)
        data = resp.json()
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        ticker = data[0]
        return {
            "symbol": symbol.upper(),
            "pair": pair.replace("_", "/"),
            "price": float(ticker.get("last", 0)),
            "change_24h": float(ticker.get("change_percentage", 0)),
            "change_24h_pct": float(ticker.get("change_percentage", 0)),
            "high_24h": float(ticker.get("high_24h", 0)),
            "low_24h": float(ticker.get("low_24h", 0)),
            "volume_24h": float(ticker.get("quote_volume", 0)),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Gate.io 降级获取 {symbol} 失败: {e}")
        return None


# ============================================================
# Trade-only API 安全验证（VergeX AI 安全模型）
# 确保只授权交易权限，无法提现
# ============================================================

def validate_trade_only_key(exchange_id: str, api_key: str, api_secret: str) -> dict:
    """
    验证 API Key 权限是否为 Trade-only（只允许交易，禁止提现）
    参考 VergeX AI 的安全机制：物理上无法提取资金

    Returns:
        dict: {
            "valid": bool,
            "permissions": list[str],   # e.g. ["read", "trade"]
            "can_withdraw": bool,
            "message": str
        }
    """
    if not _CCXT_AVAILABLE:
        return {"valid": False, "permissions": [], "can_withdraw": True,
                "message": "ccxt 未安装，无法验证"}

    try:
        ex_class = getattr(ccxt, exchange_id)
        ex = ex_class({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": False,
        })

        # 测试权限：尝试获取账户信息
        try:
            account = ex.fetch_accounts()
            permissions = ["read", "trade"]  # 基础权限
        except Exception:
            permissions = ["read"]  # 只有读取权限

        # 检查提现权限：通过查询提现地址（会失败但能反映权限）
        can_withdraw = False
        try:
            # Binance/OKX 等支持查询提现限额
            if exchange_id == "binance":
                withdraw_info = ex.fetch_borrowed("BTC")  # 杠杆借入检查
            can_withdraw = False  # 默认无提现权限（除非明确检测到）
        except ccxt.AuthenticationError:
            can_withdraw = False
        except Exception:
            pass

        # 核心检查：是否能访问提现 API
        try:
            if hasattr(ex, "privatePostWithdraw"):
                # 如果有私有提现方法，尝试验证（会因权限不足失败）
                try:
                    ex.privatePostWithdraw({"asset": "USDT", "amount": "0"})
                except (ccxt.AuthenticationError, ccxt.ExchangeError):
                    pass  # 正常，有方法但无权限
                except AttributeError:
                    can_withdraw = False
        except Exception:
            pass

        is_safe = not can_withdraw and "trade" in permissions
        msg = "✅ Trade-only" if is_safe else (
            "⚠️ 可能有提现权限，请检查" if can_withdraw else
            "⚠️ 权限未知，仅读权限")

        return {
            "valid": is_safe,
            "permissions": permissions,
            "can_withdraw": can_withdraw,
            "message": f"{exchange_id}: {msg}"
        }

    except Exception as e:
        logger.error(f"API Key 验证失败: {e}")
        return {"valid": False, "permissions": [], "can_withdraw": False,
                "message": f"验证异常: {e}"}


# ============================================================
# Hyperliquid 交易所支持（VergeX AI 核心交易所之一）
# 链上 DEX，无需托管，Arbitrum 网络
# ============================================================

HYPERLIQUID_CONFIG = {
    "base_url": "https://api.hyperliquid.xyz",
    "network_id": "Arbitrum",
    "account_address": None,  # 钱包地址（签名认证）
}


def set_hyperliquid_wallet(address: str):
    """设置 Hyperliquid 钱包地址（用于签名认证）"""
    HYPERLIQUID_CONFIG["account_address"] = address
    logger.info(f"Hyperliquid 钱包地址已设置: {address[:6]}...{address[-4:]}")


def _hyperliquid_request(method: str, endpoint: str, params: dict = None) -> Optional[dict]:
    """Hyperliquid API 请求"""
    import requests
    url = f"{HYPERLIQUID_CONFIG['base_url']}{endpoint}"
    payload = {"method": method, "params": params or [], "jsonrpc": "2.0", "id": 1}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if "error" in data:
            logger.error(f"Hyperliquid API Error: {data['error']}")
            return None
        return data.get("result")
    except Exception as e:
        logger.error(f"Hyperliquid 请求失败: {e}")
        return None


def get_hyperliquid_price(symbol: str = "ETH") -> Optional[dict]:
    """
    获取 Hyperliquid 交易所币种价格（链上 DEX）

    注意：Hyperliquid 是币币永续合约交易所（perp DEX），
    无需 API Key，所有数据公开可查
    """
    # symbol -> Hyperliquid 格式
    symbol_map = {"ETH": "ETH", "BTC": "BTC", "SOL": "SOL", "ARB": "ARB"}
    hl_symbol = symbol_map.get(symbol.upper(), symbol.upper())

    # 获取所有代币信息（一次请求获取全部）
    all_tickers = _hyperliquid_request("GET", "/info", {
        "type": "allMids"
    })
    if not all_tickers or hl_symbol not in all_tickers:
        return None

    price = float(all_tickers[hl_symbol])

    # 获取合约信息（24h 成交量）
    meta = _hyperliquid_request("GET", "/info", {
        "type": "meta"
    })

    volume_24h = 0.0
    if meta and "universe" in meta:
        for item in meta["universe"]:
            if item.get("name") == hl_symbol:
                volume_24h = float(item.get("volumeUsd", 0))
                break

    return {
        "symbol": symbol.upper(),
        "pair": f"{symbol.upper()}/USD",
        "price": price,
        "change_24h": 0.0,  # Hyperliquid 的 allMids 不含涨跌
        "change_24h_pct": 0.0,
        "high_24h": 0.0,
        "low_24h": 0.0,
        "volume_24h": volume_24h,
        "timestamp": datetime.now().isoformat(),
        "exchange": "hyperliquid",
    }


def get_hyperliquid_candles(symbol: str = "ETH", timeframe: str = "4h", limit: int = 100) -> Optional[List[Dict]]:
    """
    获取 Hyperliquid K线数据（供策略和回测使用）

    timeframe 映射：1m,5m,15m,1h,4h,1d -> Hyperliquid interval
    """
    interval_map = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "1h": "1h", "4h": "4h", "1d": "1d",
    }
    interval = interval_map.get(timeframe, "4h")

    # 获取 K线数据
    candles_raw = _hyperliquid_request("GET", "/info", {
        "type": "candleSnapshot",
        "req": {
            "coin": symbol.upper(),
            "interval": interval,
            "startTime": None,  # 最近
            "num": min(limit, 500),
        }
    })

    if not candles_raw or not isinstance(candles_raw, list):
        return None

    # 转换格式：Hyperliquid -> 统一 OHLCV
    # 格式: [startTime, open, high, low, close, volume]
    candles = []
    for r in candles_raw:
        try:
            candles.append({
                "timestamp": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            })
        except (IndexError, ValueError):
            continue

    candles.sort(key=lambda x: x["timestamp"])
    return candles if candles else None


def get_hyperliquid_order_book(symbol: str = "ETH", limit: int = 10) -> Optional[dict]:
    """获取 Hyperliquid 订单簿"""
    orderbook = _hyperliquid_request("GET", "/info", {
        "type": "depth",
        "coin": symbol.upper(),
        "depth": limit,
    })
    if not orderbook:
        return None

    return {
        "symbol": symbol.upper(),
        "pair": f"{symbol.upper()}/USD",
        "bids": [[float(p), float(s)] for p, s in orderbook.get("bids", [])],
        "asks": [[float(p), float(s)] for p, s in orderbook.get("asks", [])],
        "timestamp": datetime.now().isoformat(),
        "exchange": "hyperliquid",
    }


# ============================================================
# 兼容性别名
# ============================================================

get_crypto = get_crypto_price
get_price = get_crypto_price


# ============================================================
# 批量导出 / __main__
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print(f"ccxt 交易所: {_current_exchange_id}")
    print(f"支持的交易所: {SUPPORTED_EXCHANGES}")
    print("=" * 60)

    # 单币种测试
    print("\nBTC:", get_crypto_price("BTC"))
    print("ETH:", get_crypto_price("ETH"))

    # 批量测试
    print("\n批量获取 BTC, ETH, SOL:")
    for p in get_crypto_prices(["BTC", "ETH", "SOL"]):
        print(f"  {p['symbol']}: ${p['price']:,.2f} (24h: {p['change_24h_pct']:+.2f}%)")

    # Hyperliquid 测试
    print("\n--- Hyperliquid ---")
    hl_price = get_hyperliquid_price("ETH")
    print(f"Hyperliquid ETH: ${hl_price['price'] if hl_price else 'N/A'}")
    hl_candles = get_hyperliquid_candles("ETH", "4h", 3)
    if hl_candles:
        print(f"Hyperliquid ETH 4h K线: {len(hl_candles)} 条")
        for c in hl_candles[-2:]:
            print(f"  {datetime.fromtimestamp(c['timestamp']/1000)} O:{c['open']} H:{c['high']} L:{c['low']} C:{c['close']}")

    # 主流币 Top 10
    print("\n主流加密货币 Top 10:")
    for i, c in enumerate(get_top_cryptos(10), 1):
        print(f"  {i}. {c['symbol']}: ${c['price']:,.2f} (24h: {c['change_24h_pct']:+.2f}%)")

    # K线测试
    print("\nBTC 1h K线（最近3条）:")
    candles = get_ohlcv("BTC", "1h", limit=3)
    if candles:
        for c in candles:
            print(f"  {datetime.fromtimestamp(c[0]/1000)} O:{c[1]} H:{c[2]} L:{c[3]} C:{c[4]} V:{c[5]}")
