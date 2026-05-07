"""
Agent Gateway FastAPI 路由
将 QuantDinger 风格的 Agent API 集成到 FastAPI
"""

from fastapi import APIRouter, Query, Path, Header, HTTPException
from typing import Optional
from datetime import datetime
import time
import hashlib
import os

# ============================================================
# 配置
# ============================================================

_agent_token_env = os.getenv("AGENT_TOKEN")
if not _agent_token_env:
    raise RuntimeError(
        "[Agent Gateway] AGENT_TOKEN environment variable is REQUIRED. "
        "No hardcoded default token is allowed. "
        "Set it before starting: export AGENT_TOKEN=<your-secure-token>"
    )

AGENT_TOKENS = {
    _agent_token_env: {
        "name": "agent_gateway_client",
        "scopes": ["R", "B", "T"],  # R=Read, B=Backtest, T=Trade
        "markets": ["CN", "HK", "US", "CRYPTO"],
    }
}

SCOPE_R, SCOPE_B, SCOPE_T = "R", "B", "T"

agent_router = APIRouter(prefix="/api/agent/v1", tags=["Agent Gateway"])

# ============================================================
# 依赖
# ============================================================

def verify_agent_token(authorization: str = Header(None)) -> dict:
    """验证Agent Token"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    token = authorization.replace("Bearer ", "")
    agent = AGENT_TOKENS.get(token)
    if not agent:
        raise HTTPException(status_code=403, detail="Invalid token")
    return agent


def require_scope(agent: dict, required_scope: str):
    """检查权限范围"""
    if required_scope not in agent.get("scopes", []):
        raise HTTPException(status_code=403, detail=f"Missing required scope: {required_scope}")


# ============================================================
# 路由
# ============================================================

@agent_router.get("/whoami")
async def whoami(agent: dict = verify_agent_token):
    """返回Agent身份"""
    return {
        "data": {
            "name": agent["name"],
            "scopes": agent["scopes"],
            "markets": agent["markets"],
        },
        "timestamp": int(time.time())
    }


@agent_router.get("/markets")
async def list_markets(agent: dict = verify_agent_token):
    """列出允许的市场"""
    require_scope(agent, SCOPE_R)
    
    markets = [
        {"value": "CN", "label": "A股 (China A-shares)"},
        {"value": "HK", "label": "港股 (HK Stocks)"},
        {"value": "US", "label": "美股 (US Stocks)"},
        {"value": "CRYPTO", "label": "加密货币 (Crypto)"},
    ]
    allowed = [m for m in markets if m["value"] in agent.get("markets", [])]
    return {"data": allowed, "timestamp": int(time.time())}


@agent_router.get("/markets/{market}/symbols")
async def market_symbols(
    market: str = Path(...),
    keyword: str = Query("", description="搜索关键字"),
    limit: int = Query(20, ge=1, le=100),
    agent: dict = verify_agent_token
):
    """搜索标的"""
    require_scope(agent, SCOPE_R)
    
    if market not in agent.get("markets", []):
        raise HTTPException(status_code=403, detail=f"Market not allowed: {market}")
    
    hot_symbols = {
        "CN": [("600000", "浦发银行"), ("000001", "平安银行"), ("600519", "贵州茅台"), 
                ("600036", "招商银行"), ("601318", "中国平安"), ("000002", "万科A")],
        "HK": [("00700", "腾讯控股"), ("09988", "阿里巴巴"), ("03690", "美团"),
                ("09888", "小鹏汽车"), ("00941", "中国移动"), ("09999", "网易")],
        "US": [("AAPL", "苹果"), ("TSLA", "特斯拉"), ("NVDA", "英伟达"),
                ("MSFT", "微软"), ("GOOGL", "谷歌"), ("AMZN", "亚马逊")],
        "CRYPTO": [("BTC", "比特币"), ("ETH", "以太坊"), ("BNB", "币安币"),
                   ("SOL", "Solana"), ("XRP", "瑞波币"), ("ADA", "艾达币")],
    }
    
    symbols = hot_symbols.get(market, [])
    if keyword:
        kw = keyword.upper()
        symbols = [(s, n) for s, n in symbols if kw in s or kw in n]
    
    return {
        "data": [{"symbol": s, "name": n} for s, n in symbols[:limit]],
        "timestamp": int(time.time())
    }


@agent_router.get("/klines")
async def klines(
    market: str = Query(..., description="市场代码"),
    symbol: str = Query(..., description="标的代码"),
    timeframe: str = Query("1D", description="K线周期"),
    limit: int = Query(300, ge=1, le=2000),
    agent: dict = verify_agent_token
):
    """获取K线数据"""
    require_scope(agent, SCOPE_R)
    
    if market not in agent.get("markets", []):
        raise HTTPException(status_code=403, detail=f"Market not allowed: {market}")
    
    try:
        if market == "CN":
            from stock_data.stock_api import get_a_stock_ohlcv
            sym_fmt = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
            df = get_a_stock_ohlcv(sym_fmt, period="daily")
        elif market == "HK":
            from stock_data.stock_api import get_hk_stock_ohlcv
            df = get_hk_stock_ohlcv(f"{symbol}.HK")
        elif market == "US":
            from stock_data.stock_api import get_us_stock_ohlcv
            df = get_us_stock_ohlcv(symbol, period="1y")
        elif market == "CRYPTO":
            from crypto_api import get_ohlcv
            df = get_ohlcv(symbol, timeframe=timeframe, limit=limit)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown market: {market}")
        
        if df is None or (hasattr(df, 'empty') and df.empty):
            return {"data": {"market": market, "symbol": symbol, "klines": []}, "timestamp": int(time.time())}
        
        return {
            "data": {
                "market": market, "symbol": symbol,
                "timeframe": timeframe, "count": len(df),
                "klines": df.to_dict("records") if hasattr(df, "to_dict") else []
            },
            "timestamp": int(time.time())
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Kline fetch failed: {str(e)}")


@agent_router.get("/price")
async def price(
    market: str = Query(..., description="市场代码"),
    symbol: str = Query(..., description="标的代码"),
    agent: dict = verify_agent_token
):
    """获取实时价格"""
    require_scope(agent, SCOPE_R)
    
    if market not in agent.get("markets", []):
        raise HTTPException(status_code=403, detail=f"Market not allowed: {market}")
    
    try:
        if market == "CN":
            from stock_data.stock_api import get_a_stock_realtime
            data = get_a_stock_realtime(f"{symbol}.SH")
        elif market == "HK":
            from stock_data.stock_api import get_hk_stock_realtime
            data = get_hk_stock_realtime(f"{symbol}.HK")
        elif market == "US":
            from stock_data.stock_api import get_us_stock_realtime
            data = get_us_stock_realtime(symbol)
        elif market == "CRYPTO":
            from crypto_api import get_crypto_price
            data = get_crypto_price(symbol)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown market: {market}")
        
        return {
            "data": {"market": market, "symbol": symbol, "price": data.get("price") if data else None},
            "timestamp": int(time.time())
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Price fetch failed: {str(e)}")
