"""
股票数据统一接口 - A股/港股/美股
使用 akshare/tushare/yfinance 获取数据
"""

import akshare as ak
import yfinance as yf
import pandas as pd
from typing import Optional, Dict, List
from datetime import datetime, timedelta

# A股/港股 symbol格式: "600000.SH" (沪市), "000001.SZ" (深市), "00700.HK" (港股)
# 美股 symbol格式: "AAPL", "TSLA" (直接用ticker)


def get_a_stock_ohlcv(symbol: str, start_date: str = None, end_date: str = None, 
                       period: str = "daily", adjust: str = "qfq") -> Optional[pd.DataFrame]:
    """
    获取A股K线数据
    symbol: e.g. "600000.SH" (沪市) or "000001.SZ" (深市)
    period: choice of {'daily', 'weekly', 'monthly'}
    start_date/end_date: YYYYMMDD format, e.g. "20240101"
    adjust: "qfq" (前复权), "hfq" (后复权), "" (不复权)
    """
    try:
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        
        code = symbol.replace(".SH", "").replace(".SZ", "")
        df = ak.stock_zh_a_hist(
            symbol=code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust
        )
        if df is not None and not df.empty:
            df['date'] = pd.to_datetime(df['日期'])
            df.set_index('date', inplace=True)
            df.rename(columns={
                '开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low',
                '成交量': 'volume', '成交额': 'turnover', '涨跌幅': 'pct_change'
            }, inplace=True)
            return df
        return None
    except Exception as e:
        print(f"获取A股 {symbol} 数据失败: {e}")
        return None


def get_hk_stock_ohlcv(symbol: str, adjust: str = "qfq") -> Optional[pd.DataFrame]:
    """
    获取港股K线数据（最近365个交易日）
    symbol: e.g. "00700.HK"
    adjust: "" (不复权), "qfq" (前复权)
    """
    try:
        code = symbol.replace(".HK", "")
        df = ak.stock_hk_daily(symbol=code, adjust=adjust)
        if df is not None and not df.empty:
            # 新浪返回的格式: date, open, high, low, close, volume
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
            return df.tail(365)  # 只取最近365个交易日
        return None
    except Exception as e:
        print(f"获取港股 {symbol} 数据失败: {e}")
        return None


def get_us_stock_ohlcv(symbol: str, period: str = "1y", interval: str = "1d") -> Optional[pd.DataFrame]:
    """
    获取美股K线数据
    symbol: e.g. "AAPL", "TSLA"
    period: "1d","5d","1mo","3mo","6mo","1y","2y","5y","max"
    interval: "1m","5m","15m","1h","1d"
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df is not None and not df.empty:
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df
        return None
    except Exception as e:
        print(f"获取美股 {symbol} 数据失败: {e}")
        return None


def get_stock_ohlcv(symbol: str, market: str = "auto", **kwargs) -> Optional[pd.DataFrame]:
    """
    统一入口，根据symbol格式自动判断市场
    market: "auto", "cn", "hk", "us"
    """
    if market == "auto":
        if symbol.endswith(".SH") or symbol.endswith(".SZ"):
            market = "cn"
        elif symbol.endswith(".HK"):
            market = "hk"
        else:
            market = "us"
    
    if market == "cn":
        return get_a_stock_ohlcv(symbol, **kwargs)
    elif market == "hk":
        return get_hk_stock_ohlcv(symbol, **kwargs)
    elif market == "us":
        return get_us_stock_ohlcv(symbol, **kwargs)
    return None


def get_a_stock_realtime(symbol: str) -> Optional[Dict]:
    """获取A股实时行情"""
    try:
        spot = ak.stock_zh_a_spot_em()
        code = symbol.replace(".SH","").replace(".SZ","")
        row = spot[spot['代码'] == code]
        if not row.empty:
            return row.iloc[0].to_dict()
    except Exception as e:
        print(f"获取A股实时 {symbol} 失败: {e}")
    return None


def get_hk_stock_realtime(symbol: str) -> Optional[Dict]:
    """获取港股实时行情"""
    try:
        spot = ak.stock_hk_spot_em()
        code = symbol.replace(".HK","")
        row = spot[spot['代码'] == code]
        if not row.empty:
            return row.iloc[0].to_dict()
    except Exception as e:
        print(f"获取港股实时 {symbol} 失败: {e}")
    return None


def get_us_stock_realtime(symbol: str) -> Optional[Dict]:
    """获取美股实时行情"""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        return {
            "symbol": symbol,
            "price": info.get("last_price"),
            "change": info.get("last_extended_hours_destination_price"),
            "market_cap": info.get("market_cap"),
            "currency": "USD"
        }
    except Exception as e:
        print(f"获取美股实时 {symbol} 失败: {e}")
    return None


if __name__ == "__main__":
    # 测试
    print("测试A股数据...")
    df = get_a_stock_ohlcv("600000.SH", period="daily")
    print(f"A股 600000.SH: {len(df)} 行" if df is not None else "失败")
    
    print("测试港股数据...")
    df = get_hk_stock_ohlcv("00700.HK")
    print(f"港股 00700.HK: {len(df)} 行" if df is not None else "失败")
    
    print("测试美股数据...")
    df = get_us_stock_ohlcv("AAPL", period="1mo")
    print(f"美股 AAPL: {len(df)} 行" if df is not None else "失败")
