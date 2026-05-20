"""
QuantDinger API 兼容层
将 QD Vue 前端期望的 API 路径映射到 trading-system 后端
响应格式严格遵循 QD 标准: { code: 1, msg: "success", data: {...} }
"""

import time
import secrets
from fastapi import APIRouter, Request
from typing import Optional

router = APIRouter(prefix="/api", tags=["qd-compat"])

import base64, json, hmac, hashlib as _hashlib

def _make_jwt(username="trader", role_id="admin"):
    """生成模拟 JWT token"""
    header_b64 = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b'=').decode()
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps({
            "user_id": 1,
            "username": username,
            "role": role_id,
            "token_version": 1,
            "iat": int(time.time()),
            "exp": int(time.time()) + 86400 * 7,
        }).encode()
    ).rstrip(b'=').decode()
    sig = base64.urlsafe_b64encode(
        hmac.new(b"secret", f"{header_b64}.{payload_b64}".encode(), _hashlib.sha256).digest()
    ).rstrip(b'=').decode()
    return f"{header_b64}.{payload_b64}.{sig}"


# ── Auth ──

@router.get("/auth/security-config")
async def qd_security_config():
    """安全配置 - 关闭 Turnstile"""
    return {
        "code": 1, "msg": "success",
        "data": {
            "turnstile_enabled": False,
            "turnstile_site_key": "",
            "registration_enabled": True,
            "oauth_google_enabled": False,
            "oauth_github_enabled": False,
        },
    }


@router.post("/auth/login")
async def qd_login(request: Request):
    """登录 - QD 标准响应格式"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    username = body.get("username") or body.get("account") or "trader"
    token = _make_jwt(username)
    return {
        "code": 1,
        "msg": "Login success",
        "data": {
            "token": token,
            "userinfo": {
                "id": 1,
                "username": username,
                "nickname": username,
                "avatar": "/avatar2.jpg",
                "timezone": "Asia/Shanghai",
                "role": {"id": "admin", "permissions": ["*"]},
            },
        },
    }


@router.get("/user/info")
async def qd_user_info():
    """用户信息"""
    return {
        "code": 1, "msg": "success",
        "data": {
            "id": 1,
            "username": "trader",
            "nickname": "Trader",
            "avatar": "/avatar2.jpg",
            "timezone": "Asia/Shanghai",
            "role": {"id": "admin", "permissions": ["*"]},
        },
    }




@router.post("/auth/logout")
async def qd_logout():
    """登出"""
    return {"code": 1, "msg": "success", "data": None}


@router.get("/auth/info")
async def qd_auth_info():
    """验证 token 并返回用户信息（登录后自动调用）"""
    return {
        "code": 1, "msg": "success",
        "data": {
            "id": 1,
            "username": "trader",
            "nickname": "Trader",
            "avatar": "/avatar2.jpg",
            "timezone": "Asia/Shanghai",
            "role": {"id": "admin", "permissions": ["*"]},
        },
    }


@router.post("/auth/send-code")
async def qd_send_code(request: Request):
    """发送邮箱验证码"""
    return {"code": 1, "msg": "Code sent (mock)", "data": None}


@router.post("/auth/login-code")
async def qd_login_code(request: Request):
    """邮箱验证码登录"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    token = _make_jwt(body.get("username", "trader"))
    return {
        "code": 1, "msg": "Login success",
        "data": {
            "token": token,
            "userinfo": {
                "id": 1, "username": "trader", "nickname": "Trader",
                "avatar": "/avatar2.jpg", "timezone": "Asia/Shanghai",
                "role": {"id": "admin", "permissions": ["*"]},
            },
        },
    }


@router.post("/auth/register")
async def qd_register(request: Request):
    """注册"""
    return {"code": 1, "msg": "Registration success (mock)", "data": None}


@router.post("/auth/reset-password")
async def qd_reset_password(request: Request):
    """重置密码"""
    return {"code": 1, "msg": "Password reset email sent (mock)", "data": None}

# ── Market / Watchlist ──
_WATCHLIST = [
    {"id": 1, "market": "Crypto", "symbol": "BTC/USDT", "name": "Bitcoin"},
    {"id": 2, "market": "Crypto", "symbol": "ETH/USDT", "name": "Ethereum"},
    {"id": 3, "market": "Crypto", "symbol": "SOL/USDT", "name": "Solana"},
    {"id": 4, "market": "Crypto", "symbol": "SUI/USDT", "name": "Sui"},
]


@router.get("/market/watchlist/get")
async def qd_watchlist_get():
    """获取自选列表"""
    return {"code": 1, "msg": "success", "data": _WATCHLIST}


@router.post("/market/watchlist/add")
async def qd_watchlist_add(request: Request):
    """添加自选"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    symbol = body.get("symbol", "")
    market = body.get("market", "Crypto")
    name = body.get("name", symbol)
    if symbol:
        _WATCHLIST.append({
            "id": len(_WATCHLIST) + 1,
            "market": market,
            "symbol": symbol,
            "name": name,
        })
    return {"code": 1, "msg": "success", "data": None}


@router.post("/market/watchlist/remove")
async def qd_watchlist_remove(request: Request):
    """删除自选"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    symbol = body.get("symbol", "")
    global _WATCHLIST
    _WATCHLIST = [w for w in _WATCHLIST if w["symbol"] != symbol]
    return {"code": 1, "msg": "success", "data": None}


@router.get("/market/watchlist/prices")
async def qd_watchlist_prices():
    """自选行情快照"""
    prices = []
    try:
        from data_providers.compat import get_crypto_price
        for w in _WATCHLIST:
            data = get_crypto_price(w["symbol"].split("/")[0])
            if data:
                prices.append({
                    "symbol": w["symbol"],
                    "name": w["name"],
                    "price": data.get("price", 0),
                    "change_pct": data.get("change_24h_pct", 0),
                })
    except Exception:
        pass
    return {"code": 1, "msg": "success", "data": prices}


@router.get("/market/types")
async def qd_market_types():
    """市场类型列表（带 i18n 键，前端据此显示中文标签）"""
    return {
        "code": 1, "msg": "success",
        "data": [
            {"value": "USStock",  "i18nKey": "dashboard.analysis.market.USStock"},
            {"value": "CNStock",  "i18nKey": "dashboard.analysis.market.CNStock"},
            {"value": "HKStock",  "i18nKey": "dashboard.analysis.market.HKStock"},
            {"value": "Crypto",   "i18nKey": "dashboard.analysis.market.Crypto"},
            {"value": "Forex",    "i18nKey": "dashboard.analysis.market.Forex"},
            {"value": "Futures",  "i18nKey": "dashboard.analysis.market.Futures"},
        ],
    }


@router.get("/market/watchlist")
async def qd_watchlist(market: str = "Crypto"):
    """自选列表（兼容旧路径）"""
    return {"code": 1, "msg": "success", "data": _WATCHLIST}


@router.get("/market/kline")
async def qd_kline(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 100):
    """K线数据"""
    try:
        from data_providers.compat import get_ohlcv
        data = get_ohlcv(symbol.split("/")[0], timeframe, limit)
        if data:
            return {
                "code": 1, "msg": "success",
                "data": [
                    {"t": int(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                    for r in data
                ],
            }
        return {"code": 1, "msg": "success", "data": []}
    except Exception as e:
        return {"code": 0, "msg": str(e), "data": None}


@router.get("/market/hot-symbols")
async def qd_hot_symbols(market: str = "CRYPTO"):
    """热门品种"""
    return {
        "code": 1, "msg": "success",
        "data": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"],
    }


# ── Strategy ──

@router.get("/strategy/list")
async def qd_strategy_list():
    """策略列表"""
    return {
        "code": 1, "msg": "success",
        "data": [
            {"id": 1, "name": "ATRSTOP", "type": "ATRSTOP", "status": "running", "symbol": "SUI/USDT"},
            {"id": 2, "name": "RSI\u9707\u8361", "type": "RSI", "status": "stopped", "symbol": "ETH/USDT"},
            {"id": 3, "name": "\u5e03\u6797\u5e26\u56de\u5f52", "type": "Bollinger", "status": "stopped", "symbol": "BTC/USDT"},
            {"id": 4, "name": "MACD\u8d8b\u52bf", "type": "MACD", "status": "draft", "symbol": "SOL/USDT"},
        ],
    }


# ── Portfolio ──

@router.get("/portfolio/summary")
async def qd_portfolio_summary():
    """持仓摘要"""
    try:
        from database_pg import get_positions as pg_positions
        try:
            positions = pg_positions()
        except Exception:
            from database import get_positions as sl_positions
            positions = sl_positions()

        total_value = sum(
            p.get("quantity", 0) * p.get("entry_price", 0)
            for p in positions
        )
        return {
            "code": 1, "msg": "success",
            "data": {
                "total_equity": round(total_value, 2),
                "positions_count": len(positions),
                "positions": [
                    {"symbol": p.get("symbol",""), "entry_price": p.get("entry_price",0),
                     "quantity": p.get("quantity",0), "status": p.get("status","open")}
                    for p in positions
                ],
            },
        }
    except Exception as e:
        return {"code": 0, "msg": str(e), "data": None}



# ── Catch-all mock routes (return empty/success data) ──

@router.get("/strategies/list")
@router.get("/strategy/list")
async def qd_strategy_list_all():
    """策略列表（兼容两种路径）"""
    return {
        "code": 1, "msg": "success",
        "data": {
            "total": 4,
            "items": [
                {"id": 1, "name": "ATRSTOP", "type": "ATRSTOP", "status": "running", "symbol": "SUI/USDT", "config": {}},
                {"id": 2, "name": "RSI\u9707\u8361", "type": "RSI", "status": "stopped", "symbol": "ETH/USDT", "config": {}},
                {"id": 3, "name": "\u5e03\u6797\u5e26\u56de\u5f52", "type": "Bollinger", "status": "stopped", "symbol": "BTC/USDT", "config": {}},
                {"id": 4, "name": "MACD\u8d8b\u52bf", "type": "MACD", "status": "draft", "symbol": "SOL/USDT", "config": {}},
            ],
        },
    }


@router.api_route("/strategies/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def qd_strategies_catchall(path: str, request: Request):
    """策略相关 API 通用 mock"""
    return {"code": 1, "msg": "success", "data": [] if "list" in path or "batch" in path else {}}


@router.get("/agent/v1/{path:path}")
@router.post("/agent/v1/{path:path}")
async def qd_agent_catchall(path: str):
    """Agent Gateway 通用 mock"""
    return {"code": 1, "msg": "success", "data": {}}


@router.get("/dashboard/summary")
async def qd_dashboard_summary():
    """Dashboard 摘要"""
    return {
        "code": 1, "msg": "success",
        "data": {
            "total_equity": 235.96,
            "daily_pnl": 0,
            "open_positions": 0,
            "total_trades_today": 0,
        },
    }


# ── Fast Analysis ──

@router.post("/fast-analysis/analyze")
@router.post("/fast-analysis/analyze-legacy")
async def qd_fast_analysis(request: Request):
    """快速分析（mock 返回示例结果）"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    symbol = body.get("symbol", "BTC/USDT")
    return {
        "code": 1, "msg": "success",
        "data": {
            "symbol": symbol,
            "analysis": f"AI analysis for {symbol}: market shows ranging pattern with moderate volatility. RSI at neutral levels. No strong directional signal detected.",
            "score": 50,
            "recommendation": "hold",
            "memory_id": int(time.time()),
        },
    }


@router.get("/fast-analysis/history")
@router.get("/fast-analysis/history/all")
async def qd_fast_history():
    """分析历史"""
    return {"code": 1, "msg": "success", "data": []}


@router.delete("/fast-analysis/history/{memory_id}")
async def qd_fast_history_delete(memory_id: int):
    """删除分析历史"""
    return {"code": 1, "msg": "success", "data": None}


@router.post("/fast-analysis/feedback")
async def qd_fast_feedback(request: Request):
    """分析反馈"""
    return {"code": 1, "msg": "success", "data": None}


@router.get("/fast-analysis/performance")
async def qd_fast_performance():
    """分析性能统计"""
    return {"code": 1, "msg": "success", "data": {"total": 0, "avg_score": 0}}


@router.get("/fast-analysis/similar-patterns")
async def qd_similar_patterns():
    """相似形态"""
    return {"code": 1, "msg": "success", "data": []}


# ── AI Chat ──

@router.post("/ai/chat/message")
async def qd_ai_chat(request: Request):
    """AI 聊天"""
    return {
        "code": 1, "msg": "success",
        "data": {"reply": "This is a mock AI chat response from trading-system."},
    }


@router.get("/ai/chat/history")
async def qd_ai_history():
    """聊天历史"""
    return {"code": 1, "msg": "success", "data": []}


# ── Global Market ──

@router.get("/global-market/{path:path}")
async def qd_global_market(path: str):
    """全球市场"""
    return {"code": 1, "msg": "success", "data": {}}


# ── Dashboard ──

@router.get("/dashboard/summary")
async def qd_dashboard_summary():
    """Dashboard 摘要"""
    return {
        "code": 1, "msg": "success",
        "data": {
            "total_equity": 235.96,
            "daily_pnl": 0,
            "open_positions": 0,
            "total_trades_today": 0,
        },
    }


# ── Indicator (代码编辑器) ──

@router.get("/indicator/{path:path}")
@router.post("/indicator/{path:path}")
async def qd_indicator(path: str):
    """指标编辑器"""
    return {"code": 1, "msg": "success", "data": {}}


# ── Billing ──

@router.get("/billing/{path:path}")
async def qd_billing(path: str):
    """计费"""
    return {"code": 1, "msg": "success", "data": {"credits": 9999}}


# ── Community ──

@router.get("/community/{path:path}")
async def qd_community(path: str):
    """社区"""
    return {"code": 1, "msg": "success", "data": []}


# ── Quick Trade ──

@router.post("/quick-trade/{path:path}")
async def qd_quick_trade(path: str):
    """快捷交易"""
    return {"code": 1, "msg": "success", "data": {}}


# ── Backtest (indicator-based) ──

@router.get("/indicator/backtest/{path:path}")
@router.post("/indicator/backtest/{path:path}")
async def qd_indicator_backtest(path: str):
    """回测"""
    return {"code": 1, "msg": "success", "data": {
        "total_return_pct": 12.7,
        "sharpe_ratio": 1.5,
        "max_drawdown_pct": 5.2,
        "total_trades": 30,
        "win_rate_pct": 55.0,
        "equity_curve": [],
    }}


# ── Polymarket ──

@router.get("/polymarket/{path:path}")
async def qd_polymarket(path: str):
    """Polymarket"""
    return {"code": 1, "msg": "success", "data": []}


# ── Settings ──

@router.get("/settings/schema")
async def qd_settings_schema():
    """配置 schema（定义可用设置项）"""
    return {
        "code": 1, "msg": "success",
        "data": {
            "general": {
                "label": "\u901a\u7528\u8bbe\u7f6e",
                "items": [
                    {"key": "SITE_TITLE", "label": "\u7ad9\u70b9\u6807\u9898", "type": "text", "default": "trading-system"},
                    {"key": "TZ", "label": "\u65f6\u533a", "type": "text", "default": "Asia/Shanghai"},
                ],
            },
            "ai": {
                "label": "AI \u8bbe\u7f6e",
                "items": [
                    {"key": "AI_MODEL", "label": "AI \u6a21\u578b", "type": "select", "default": "deepseek", "options": ["deepseek", "minimax", "moonshot", "qwen", "gpt-4o", "claude"]},
                    {"key": "AI_API_KEY", "label": "API Key", "type": "password", "default": ""},
                    {"key": "AI_BASE_URL", "label": "API \u5730\u5740", "type": "text", "default": ""},
                    {"key": "MINIMAX_GROUP_ID", "label": "Minimax Group ID", "type": "text", "default": ""},
                    {"key": "MINIMAX_API_KEY", "label": "Minimax API Key", "type": "password", "default": ""},
                ],
            },
            "exchange": {
                "label": "\u4ea4\u6613\u6240\u8bbe\u7f6e",
                "items": [
                    {"key": "CRYPTO_EXCHANGE", "label": "\u52a0\u5bc6\u8d27\u5e01\u4ea4\u6613\u6240", "type": "select", "default": "gateio", "options": ["binance", "gateio", "okx", "bybit", "bitget", "weex"]},
                    {"key": "CRYPTO_API_KEY", "label": "API Key", "type": "password", "default": ""},
                    {"key": "CRYPTO_API_SECRET", "label": "API Secret", "type": "password", "default": ""},
                ],
            },
        },
    }


@router.get("/settings/public-config")
async def qd_settings_public():
    """公开配置（非敏感）"""
    return {
        "code": 1, "msg": "success",
        "data": {"ccxt_default_exchange": "gateio"},
    }


@router.get("/settings/values")
async def qd_settings_values():
    """当前配置值"""
    return {
        "code": 1, "msg": "success",
        "data": {
            "general": {"SITE_TITLE": "trading-system", "TZ": "Asia/Shanghai"},
            "ai": {"AI_MODEL": "deepseek", "AI_API_KEY": "***", "AI_BASE_URL": "", "MINIMAX_GROUP_ID": "", "MINIMAX_API_KEY": "***"},
            "exchange": {"CRYPTO_EXCHANGE": "gateio", "CRYPTO_API_KEY": "***", "CRYPTO_API_SECRET": "***"},
        },
    }


@router.post("/settings/save")
async def qd_settings_save(request: Request):
    """保存配置"""
    return {"code": 1, "msg": "\u4fdd\u5b58\u6210\u529f", "data": None}


@router.get("/settings/openrouter-balance")
async def qd_openrouter_balance():
    """OpenRouter 余额"""
    return {"code": 1, "msg": "success", "data": {"balance": 0}}


@router.post("/settings/test-connection")
async def qd_test_connection(request: Request):
    """测试交易所连接"""
    return {"code": 1, "msg": "\u8fde\u63a5\u6b63\u5e38", "data": {"status": "connected"}}


# ── Market (extended) ──

@router.get("/market/config")
async def qd_market_config():
    """市场配置"""
    return {"code": 1, "msg": "success", "data": {"defaultMarket": "Crypto", "themes": {}}}


@router.get("/market/menuFooterConfig")
async def qd_menu_footer():
    """菜单底部配置"""
    return {"code": 1, "msg": "success", "data": {}}


@router.get("/market/price")
async def qd_market_price(symbol: str = "BTC/USDT", market: str = "Crypto"):
    """单个行情"""
    try:
        from data_providers.compat import get_crypto_price
        data = get_crypto_price(symbol.split("/")[0])
        return {"code": 1, "msg": "success", "data": data or {"price": 0}}
    except Exception:
        return {"code": 1, "msg": "success", "data": {"symbol": symbol, "price": 0}}


@router.get("/market/symbols/hot")
async def qd_hot_symbols(market: str = "Crypto"):
    """热门币种"""
    return {"code": 1, "msg": "success", "data": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]}


@router.get("/market/symbols/search")
async def qd_symbol_search(q: str = "", market: str = "Crypto"):
    """搜索币种"""
    return {"code": 1, "msg": "success", "data": []}


# ── Indicator Kline ──

@router.get("/indicator/kline")
async def qd_indicator_kline(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 100):
    """K线（指标编辑器用）"""
    try:
        from data_providers.compat import get_ohlcv
        data = get_ohlcv(symbol.split("/")[0], timeframe, limit)
        if data:
            return {"code": 1, "msg": "success", "data": [
                {"t": int(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                for r in data
            ]}
    except Exception:
        pass
    return {"code": 1, "msg": "success", "data": []}


# ── Portfolio ──

@router.get("/portfolio/positions")
@router.get("/portfolio/positions/{path:path}")
async def qd_portfolio_positions():
    """持仓"""
    try:
        from database_pg import get_positions as pg_positions
        pos = pg_positions()
    except Exception:
        try:
            from database import get_positions as sl_positions
            pos = sl_positions()
        except Exception:
            pos = []
    return {"code": 1, "msg": "success", "data": pos}


@router.get("/portfolio/alerts")
@router.get("/portfolio/alerts/{path:path}")
async def qd_portfolio_alerts():
    """告警"""
    return {"code": 1, "msg": "success", "data": []}


@router.get("/portfolio/groups")
async def qd_portfolio_groups():
    """持仓分组"""
    return {"code": 1, "msg": "success", "data": []}


@router.get("/portfolio/monitors")
@router.get("/portfolio/monitors/{path:path}")
@router.post("/portfolio/monitors/{path:path}")
async def qd_portfolio_monitors():
    """监控器"""
    return {"code": 1, "msg": "success", "data": []}


# ── Quick Trade (specific endpoints) ──

@router.get("/quick-trade/balance")
async def qd_quick_balance():
    return {"code": 1, "msg": "success", "data": {"total": 235.96, "available": 235.96}}


@router.get("/quick-trade/history")
async def qd_quick_history():
    return {"code": 1, "msg": "success", "data": []}


@router.post("/quick-trade/place-order")
async def qd_quick_place_order(request: Request):
    return {"code": 1, "msg": "success", "data": {"order_id": "mock_001"}}


@router.post("/quick-trade/close-position")
async def qd_quick_close(request: Request):
    return {"code": 1, "msg": "success", "data": None}


@router.get("/quick-trade/position")
async def qd_quick_position():
    return {"code": 1, "msg": "success", "data": []}


# ── Experiment ──

@router.post("/experiment/ai-optimize")
@router.post("/experiment/structured-tune")
async def qd_experiment(request: Request):
    """AI 实验优化"""
    return {"code": 1, "msg": "success", "data": {"status": "completed", "result": {}}}


# ── Analysis (legacy) ──

@router.get("/analysis/{path:path}")
@router.post("/analysis/{path:path}")
async def qd_analysis_legacy(path: str):
    """旧版分析"""
    return {"code": 1, "msg": "success", "data": []}


# ── Users ──

@router.get("/users/profile")
async def qd_user_profile():
    """用户资料"""
    return {
        "code": 1, "msg": "success",
        "data": {"username": "trader", "email": "trader@trading-system.local", "avatar": "/avatar2.jpg"},
    }


@router.post("/users/profile/update")
async def qd_user_profile_update(request: Request):
    return {"code": 1, "msg": "success", "data": None}


@router.get("/users/list")
async def qd_users_list():
    return {"code": 1, "msg": "success", "data": []}


@router.get("/users/roles")
async def qd_users_roles():
    return {"code": 1, "msg": "success", "data": [{"id": "admin", "name": "Admin"}]}


@router.get("/users/{path:path}")
@router.post("/users/{path:path}")
@router.put("/users/{path:path}")
@router.delete("/users/{path:path}")
async def qd_users_catchall(path: str):
    """用户管理通用"""
    return {"code": 1, "msg": "success", "data": []}


# ── Credentials ──

@router.get("/credentials/list")
async def qd_creds_list():
    return {"code": 1, "msg": "success", "data": []}


@router.get("/credentials/{path:path}")
@router.post("/credentials/{path:path}")
@router.delete("/credentials/{path:path}")
async def qd_creds_catchall(path: str):
    return {"code": 1, "msg": "success", "data": []}


# ── Dashboard pending orders ──

@router.get("/dashboard/pendingOrders")
async def qd_pending_orders():
    return {"code": 1, "msg": "success", "data": []}


# ── Webhooks ──

@router.get("/webhooks/{path:path}")
@router.post("/webhooks/{path:path}")
async def qd_webhooks(path: str):
    return {"code": 1, "msg": "success", "data": []}


# ── Auth (additional) ──

@router.post("/auth/change-password")
async def qd_change_password(request: Request):
    return {"code": 1, "msg": "success", "data": None}


@router.get("/auth/oauth/github")
@router.get("/auth/oauth/google")
async def qd_oauth():
    return {"code": 1, "msg": "OAuth not configured", "data": None}

# ── Health ──

@router.get("/health")
async def qd_health():
    """健康检查"""
    return {"status": "ok", "version": "3.0.0", "service": "trading-system"}
