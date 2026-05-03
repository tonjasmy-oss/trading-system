"""
股票数据接入模块 - A股/港股/美股
使用国内可访问数据源：东方财富(A股)、新浪财经(港股/美股)
"""
import requests
from typing import Optional, Dict, List
from datetime import datetime
import logging
import re

logger = logging.getLogger(__name__)

# A股数据（东方财富）
EAST_MONEY_BASE = "https://push2.eastmoney.com"
# 新浪财经（港股/美股）
SINA_BASE = "https://hq.sinajs.cn"

def _parse_sina_cn_stock(fields: list, symbol: str) -> Optional[Dict]:
    """解析新浪A股数据"""
    try:
        # 新浪A股格式: 0=name, 1=current, 2=prev_close(?), 3=open, 4=high, 5=low
        # 之后是price/volume交替数据
        if len(fields) < 6:
            return None
        
        name = fields[0]
        price = float(fields[1]) if fields[1] else 0
        prev_close = float(fields[2]) if fields[2] else 0
        open_price = float(fields[3]) if fields[3] else 0
        high = float(fields[4]) if fields[4] else 0
        low = float(fields[5]) if fields[5] else 0
        
        # 提取成交量（第一个volume字段在位置7）
        volume = fields[7] if len(fields) > 7 else "0"
        
        change = price - prev_close if price and prev_close else 0
        change_pct = (change / prev_close * 100) if prev_close else 0
        
        return {
            "symbol": symbol,
            "market": "CN",
            "name": name,
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"解析新浪A股数据失败: {e}")
        return None


def get_a_stock(symbol: str) -> Optional[Dict]:
    """获取A股实时行情（新浪财经）"""
    try:
        # 判断交易所：6开头沪市（sh），0/3开头深市（sz）
        if symbol.startswith("6"):
            sina_symbol = f"sh{symbol}"
        else:
            sina_symbol = f"sz{symbol}"
        
        url = f"{SINA_BASE}/list={sina_symbol}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=10)
        
        return _parse_sina_stock(resp.text, symbol, "CN")
    except Exception as e:
        logger.error(f"获取A股 {symbol} 失败: {e}")
        return None

def _parse_sina_us_stock(fields: list, symbol: str) -> Optional[Dict]:
    """解析新浪美股数据"""
    try:
        # 新浪美股格式字段（已知）：
        # 0=name, 1=price, 2=change, 4=open, 5=high, 6=low, 7=52w_high, 8=52w_low
        # 26=prev_close, 27=volume, 29=avg_volume
        if len(fields) < 30:
            return None
        
        name = fields[0]
        price = float(fields[1]) if fields[1] else 0
        change = float(fields[2]) if fields[2] else 0
        open_price = float(fields[4]) if fields[4] else 0
        high = float(fields[5]) if fields[5] else 0
        low = float(fields[6]) if fields[6] else 0
        low_52w = float(fields[7]) if len(fields) > 7 and fields[7] else 0
        high_52w = float(fields[8]) if len(fields) > 8 and fields[8] else 0
        prev_close = float(fields[26]) if len(fields) > 26 and fields[26] else 0
        volume = fields[27] if len(fields) > 27 else "0"
        
        if not prev_close and price:
            prev_close = price - change
        change_pct = (change / prev_close * 100) if prev_close else 0
        
        return {
            "symbol": symbol,
            "market": "US",
            "name": name,
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "open": open_price,
            "high": high,
            "low": low,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "volume": volume,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"解析新浪美股数据失败: {e}")
        return None


def _parse_sina_hk_stock(fields: list, symbol: str) -> Optional[Dict]:
    """解析新浪港股数据"""
    try:
        # 新浪港股格式: 0=name, 1=full_name, 2=prev_close, 3=open, 4=current, 5=high, 6=low, 7=volume
        if len(fields) < 8:
            return None
        
        name = fields[0]
        prev_close = float(fields[2]) if fields[2] else 0
        price = float(fields[4]) if fields[4] else 0
        high = float(fields[5]) if fields[5] else 0
        low = float(fields[6]) if fields[6] else 0
        volume = fields[7] if fields[7] else "0"
        
        change = price - prev_close if price and prev_close else 0
        change_pct = (change / prev_close * 100) if prev_close else 0
        
        return {
            "symbol": symbol,
            "market": "HK",
            "name": name,
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "high": high,
            "low": low,
            "volume": volume,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"解析新浪港股数据失败: {e}")
        return None


def _parse_sina_stock(response_text: str, symbol: str, market: str) -> Optional[Dict]:
    """解析新浪财经返回的股票数据"""
    try:
        match = re.search(r'"([^"]+)"', response_text)
        if not match:
            return None
        
        fields = match.group(1).split(',')
        
        if market == "US":
            return _parse_sina_us_stock(fields, symbol)
        elif market == "HK":
            return _parse_sina_hk_stock(fields, symbol)
        elif market == "CN":
            return _parse_sina_cn_stock(fields, symbol)
        return None
    except Exception as e:
        logger.error(f"解析新浪数据失败: {e}")
        return None

def get_us_stock(symbol: str) -> Optional[Dict]:
    """获取美股实时行情（新浪财经）"""
    try:
        # 新浪美股格式: gb_aapl
        sina_symbol = f"gb_{symbol.lower()}"
        url = f"{SINA_BASE}/list={sina_symbol}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=10)
        
        return _parse_sina_stock(resp.text, symbol.upper(), "US")
    except Exception as e:
        logger.error(f"获取美股 {symbol} 失败: {e}")
        return None

def get_hk_stock(symbol: str) -> Optional[Dict]:
    """获取港股实时行情（新浪财经）"""
    try:
        # 港股需要在代码前加0补齐5位: 00700
        if len(symbol) == 4:
            symbol = symbol.zfill(5)
        elif len(symbol) == 3:
            symbol = symbol.zfill(5)
        
        # 新浪港股格式: hk00700
        sina_symbol = f"hk{symbol}"
        url = f"{SINA_BASE}/list={sina_symbol}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=10)
        
        return _parse_sina_stock(resp.text, symbol, "HK")
    except Exception as e:
        logger.error(f"获取港股 {symbol} 失败: {e}")
        return None

def get_stock(symbol: str, market: str = "US") -> Optional[Dict]:
    """统一入口"""
    if market.upper() in ("CN", "A", "SH", "SZ"):
        return get_a_stock(symbol)
    elif market.upper() == "HK":
        return get_hk_stock(symbol)
    elif market.upper() == "US":
        return get_us_stock(symbol)
    return None

def batch_get_stocks(symbols: List[str], market: str = "US") -> List[Dict]:
    """批量获取股票行情"""
    results = []
    for sym in symbols:
        data = get_stock(sym, market)
        if data:
            results.append(data)
    return results

if __name__ == "__main__":
    print("=" * 50)
    print("测试A股:")
    print("  浦发银行:", get_a_stock("600000"))
    print("  平安银行:", get_a_stock("000001"))
    print("=" * 50)
    print("测试港股:")
    print("  腾讯控股:", get_hk_stock("00700"))
    print("  阿里巴巴:", get_hk_stock("09988"))
    print("=" * 50)
    print("测试美股:")
    print("  苹果:", get_us_stock("AAPL"))
    print("  特斯拉:", get_us_stock("TSLA"))
    print("=" * 50)
