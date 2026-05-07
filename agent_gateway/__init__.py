"""
Agent Gateway - AI Agent 接口层
参考 QuantDinger /api/agent/v1 设计

提供：
- /api/agent/v1/whoami - 身份验证
- /api/agent/v1/markets - 市场列表
- /api/agent/v1/symbols - 搜索标的
- /api/agent/v1/klines - K线数据
- /api/agent/v1/price - 实时价格
- /api/agent/v1/backtest - 提交回测
- /api/agent/v1/jobs - 任务状态
"""

from flask import Blueprint, jsonify, request
from functools import wraps
import hashlib
import time
import os

agent_v1_bp = Blueprint("agent_v1", __name__, url_prefix="/api/agent/v1")

# ============================================================
# 简单Token验证（生产环境应使用JWT）
# ============================================================

AGENT_TOKENS = {
    os.getenv("AGENT_TOKEN", "qd_agent_demo"): {
        "name": "demo_agent",
        "scopes": ["R", "B", "T"],  # R=Read, B=Backtest, T=Trade
        "markets": ["CN", "HK", "US", "CRYPTO"],
    }
}

SCOPE_R = "R"   # Read
SCOPE_B = "B"   # Backtest
SCOPE_T = "T"   # Trade


def agent_required(required_scope=None):
    """Agent token验证装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if not token:
                return jsonify({"error": True, "message": "Missing token"}), 401
            
            agent = AGENT_TOKENS.get(token)
            if not agent:
                return jsonify({"error": True, "message": "Invalid token"}), 403
            
            if required_scope and required_scope not in agent.get("scopes", []):
                return jsonify({"error": True, "message": f"Missing scope: {required_scope}"}), 403
            
            request.agent = agent
            return f(*args, **kwargs)
        return decorated
    return decorator


def envelope(data):
    """统一响应格式"""
    return jsonify({"data": data, "timestamp": int(time.time())})


def error(code=400, message="Error", details=None, retriable=False, http=400):
    """统一错误格式"""
    return jsonify({
        "code": code,
        "message": message,
        "details": details,
        "retriable": retriable
    }), http


# ============================================================
# 路由
# ============================================================

@agent_v1_bp.route("/whoami", methods=["GET"])
@agent_required()
def whoami():
    """返回调用token的身份和权限"""
    agent = request.agent
    return envelope({
        "name": agent["name"],
        "scopes": agent["scopes"],
        "markets": agent["markets"],
    })


@agent_v1_bp.route("/markets", methods=["GET"])
@agent_required(SCOPE_R)
def list_markets():
    """列出允许访问的市场"""
    markets = [
        {"value": "CN", "label": "A股 (China A-shares)"},
        {"value": "HK", "label": "港股 (HK Stocks)"},
        {"value": "US", "label": "美股 (US Stocks)"},
        {"value": "CRYPTO", "label": "加密货币 (Crypto)"},
    ]
    allowed = [m for m in markets if m["value"] in request.agent.get("markets", [])]
    return envelope(allowed)


@agent_v1_bp.route("/markets/<market>/symbols", methods=["GET"])
@agent_required(SCOPE_R)
def market_symbols(market: str):
    """搜索市场内的标的"""
    if market not in request.agent.get("markets", []):
        return error(403, f"Market not allowed: {market}", http=403)
    
    keyword = (request.args.get("keyword") or "").strip().upper()
    limit = min(int(request.args.get("limit", 20)), 100)
    
    # 预定义热门标的
    hot_symbols = {
        "CN": [("600000", "浦发银行"), ("000001", "平安银行"), ("600519", "贵州茅台"), 
                ("000002", "万科A"), ("600036", "招商银行"), ("601318", "中国平安")],
        "HK": [("00700", "腾讯控股"), ("09988", "阿里巴巴"), ("03690", "美团"),
                ("09888", "小鹏汽车"), ("09999", "网易"), ("00941", "中国移动")],
        "US": [("AAPL", "苹果"), ("TSLA", "特斯拉"), ("NVDA", "英伟达"),
                ("MSFT", "微软"), ("GOOGL", "谷歌"), ("AMZN", "亚马逊")],
        "CRYPTO": [("BTC", "比特币"), ("ETH", "以太坊"), ("BNB", "币安币"),
                   ("SOL", "Solana"), ("XRP", "瑞波币"), ("ADA", "艾达币")],
    }
    
    symbols = hot_symbols.get(market, [])
    if keyword:
        symbols = [(s, n) for s, n in symbols if keyword in s or keyword in n]
    
    return envelope([{"symbol": s, "name": n} for s, n in symbols[:limit]])


@agent_v1_bp.route("/klines", methods=["GET"])
@agent_required(SCOPE_R)
def klines():
    """获取K线数据"""
    market = request.args.get("market", "").strip()
    symbol = request.args.get("symbol", "").strip()
    timeframe = request.args.get("timeframe", "1D").strip()
    limit = min(int(request.args.get("limit", 300)), 2000)
    
    if not market or not symbol:
        return error(400, "market and symbol are required")
    
    if market not in request.agent.get("markets", []):
        return error(403, f"Market not allowed: {market}", http=403)
    
    try:
        # 调用现有数据接口
        from stock_data.stock_api import get_stock_ohlcv, get_us_stock_ohlcv
        
        if market == "CN":
            symbol_fmt = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
            df = get_stock_ohlcv(symbol_fmt, period="daily")
        elif market == "HK":
            df = get_stock_ohlcv(f"{symbol}.HK")
        elif market == "US":
            df = get_us_stock_ohlcv(symbol, period="1y")
        elif market == "CRYPTO":
            from crypto_api import get_ohlcv
            df = get_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if df is not None:
                return envelope({
                    "market": market, "symbol": symbol,
                    "timeframe": timeframe, "count": len(df),
                    "klines": df.to_dict("records") if hasattr(df, "to_dict") else df
                })
        else:
            return error(400, f"Unknown market: {market}")
        
        if df is None:
            return error(404, f"No data for {symbol}")
        
        return envelope({
            "market": market, "symbol": symbol,
            "timeframe": timeframe, "count": len(df),
            "klines": df.to_dict("records") if hasattr(df, "to_dict") else []
        })
    except Exception as e:
        return error(500, f"Kline fetch failed: {str(e)}", details=str(e), retriable=True, http=502)


@agent_v1_bp.route("/price", methods=["GET"])
@agent_required(SCOPE_R)
def price():
    """获取实时价格"""
    market = request.args.get("market", "").strip()
    symbol = request.args.get("symbol", "").strip()
    
    if not market or not symbol:
        return error(400, "market and symbol are required")
    
    if market not in request.agent.get("markets", []):
        return error(403, f"Market not allowed: {market}", http=403)
    
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
            if data:
                return envelope({
                    "market": market, "symbol": symbol,
                    "price": data.get("price"),
                    "change_24h": data.get("change_24h")
                })
        else:
            return error(400, f"Unknown market: {market}")
        
        if not data:
            return error(404, f"No price for {symbol}")
        
        return envelope({"market": market, "symbol": symbol, "price": data.get("price")})
    except Exception as e:
        return error(500, f"Price fetch failed: {str(e)}", details=str(e), retriable=True, http=502)


# ============================================================
# 注册Blueprint到Flask App
# ============================================================

def register(app):
    """注册Agent Gateway到Flask应用"""
    from . import whoami, markets, klines, price
    app.register_blueprint(agent_v1_bp, url_prefix="/api/agent/v1")
    print("[Agent Gateway] /api/agent/v1 mounted")
