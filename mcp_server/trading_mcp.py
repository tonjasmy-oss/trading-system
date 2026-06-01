"""
Trading System MCP Server v2 — 增强版
======================================

参考 QuantDinger quantdinger-mcp 设计，提供完整的交易系统 MCP 工具集。

新增 (v2):
  - 实验管线工具 (regime detection + strategy experiment)
  - 策略推荐工具 (市场状态 → 策略推荐)
  - 审计日志 (tool call 记录)
  - 流式返回支持 (SSE transport)

安装运行：
  pip3 install mcp httpx
  python trading_mcp.py

环境变量：
  TRADING_SYSTEM_URL - 交易系统地址（默认 http://localhost:8081）
  MCP_TRANSPORT - 传输方式（stdio/http/sse，默认stdio）
  MCP_HOST - HTTP绑定地址（默认127.0.0.1）
  MCP_PORT - HTTP端口（默认8000）
"""

import os
import sys
import json
import time
import logging
from typing import Any, Optional
from datetime import datetime, timezone

import httpx
from mcp.server.fastmcp import FastMCP

# ============================================================
# 配置
# ============================================================

BASE_URL = os.getenv("TRADING_SYSTEM_URL", "http://localhost:8081")
TIMEOUT_S = float(os.getenv("TRADING_SYSTEM_TIMEOUT_S", "120"))
AUDIT_ENABLED = os.getenv("MCP_AUDIT_ENABLED", "true").lower() == "true"

_client = httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_S)
logger = logging.getLogger("trading-mcp")

# 审计日志
_audit_log: list = []
_audit_start_time = time.time()


def _audit(tool_name: str, params: dict, result_type: str, duration_ms: float):
    if not AUDIT_ENABLED:
        return
    entry = {
        "tool": tool_name,
        "params": {k: str(v)[:100] for k, v in (params or {}).items()},
        "result_type": result_type,
        "duration_ms": round(duration_ms, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _audit_log.append(entry)
    if len(_audit_log) > 1000:
        _audit_log.pop(0)


def _timed(func):
    """装饰器：自动计时 + 审计记录"""
    def wrapper(*args, **kwargs):
        t0 = time.time()
        try:
            result = func(*args, **kwargs)
            _audit(func.__name__, kwargs, "ok", (time.time() - t0) * 1000)
            return result
        except Exception as e:
            _audit(func.__name__, kwargs, f"error:{e}", (time.time() - t0) * 1000)
            raise
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


def _get(path: str, params: dict = None) -> Any:
    t0 = time.time()
    try:
        r = _client.get(path, params=params or {})
    except httpx.TimeoutException:
        return {"error": "timeout", "message": f"Request to {path} timed out after {TIMEOUT_S}s"}
    return _unwrap(r)


def _post(path: str, json_data: dict = None) -> Any:
    t0 = time.time()
    try:
        r = _client.post(path, json=json_data or {})
    except httpx.TimeoutException:
        return {"error": "timeout", "message": f"POST {path} timed out after {TIMEOUT_S}s"}
    return _unwrap(r)


def _unwrap(r: httpx.Response) -> Any:
    try:
        body = r.json()
    except Exception:
        return {"error": True, "status": r.status_code, "text": r.text[:500]}
    if r.status_code >= 400:
        return {"error": True, "status": r.status_code, "body": body}
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


# ============================================================
# FastMCP Server
# ============================================================

mcp = FastMCP(
    "trading-system",
    instructions=(
        "Trading System MCP Server v2 — 多市场量化交易系统。\n"
        "功能：市场行情 · 回测分析 · 策略实验 · 市场状态检测 · 策略推荐 · 持仓管理。\n"
        "所有工具都是只读或回测类，不暴露实盘交易（实盘由 live_trading.py daemon 管理）。"
    ),
)


# ============================================================
# 系统工具
# ============================================================

@mcp.tool()
def system_status() -> dict:
    """获取交易系统运行状态（三省六部架构、Agent 列表、监控状态）"""
    return _get("/api/system/status")


@mcp.tool()
def sansheng_status() -> dict:
    """获取三省六部架构详情（门下省风控、尚书省执行、Agent 策略）"""
    return _get("/api/sansheng/status")


@mcp.tool()
def mcp_audit_log(limit: int = 20) -> dict:
    """查看 MCP Server 审计日志（最近 N 条 tool call 记录）

    Args:
        limit: 返回条数（默认20）
    """
    return {
        "total_calls": len(_audit_log),
        "uptime_seconds": round(time.time() - _audit_start_time, 0),
        "recent": _audit_log[-limit:],
    }


# ============================================================
# 市场数据工具
# ============================================================

@mcp.tool()
def list_markets() -> list:
    """列出所有支持的市场（CRYPTO/CN/HK/US）"""
    return _get("/api/agent/v1/markets")


@mcp.tool()
def search_symbols(market: str, keyword: str = "", limit: int = 20) -> list:
    """搜索市场内的标的

    Args:
        market: 市场代码 (CN/HK/US/CRYPTO)
        keyword: 搜索关键字（代码或名称）
        limit: 返回数量（默认20）
    """
    return _get(f"/api/agent/v1/markets/{market}/symbols",
                params={"keyword": keyword, "limit": limit})


@mcp.tool()
def get_klines(market: str, symbol: str, timeframe: str = "1D", limit: int = 300) -> dict:
    """获取K线数据

    Args:
        market: 市场代码 (CN/HK/US/CRYPTO)
        symbol: 标的代码 (e.g. BTC, 600000.SH, 00700.HK)
        timeframe: K线周期 (1m/5m/15m/30m/1H/4H/1D/1W)
        limit: 数量（默认300）
    """
    return _get("/api/agent/v1/klines", params={
        "market": market, "symbol": symbol, "timeframe": timeframe, "limit": limit
    })


@mcp.tool()
def get_price(market: str, symbol: str) -> dict:
    """获取实时价格

    Args:
        market: 市场代码
        symbol: 标的代码
    """
    return _get("/api/agent/v1/price", params={"market": market, "symbol": symbol})


@mcp.tool()
def get_all_prices() -> list:
    """获取所有市场的实时行情"""
    return _get("/api/market/prices")


# ============================================================
# 持仓 & 交易工具
# ============================================================

@mcp.tool()
def get_portfolio() -> list:
    """获取当前持仓"""
    return _get("/api/positions")


@mcp.tool()
def get_portfolio_value() -> dict:
    """获取账户市值统计（总成本/总市值/总PnL）"""
    return _get("/api/portfolio/value")


@mcp.tool()
def get_trades(limit: int = 50) -> list:
    """获取交易历史

    Args:
        limit: 返回数量（默认50）
    """
    return _get("/api/trades", params={"limit": limit})


# ============================================================
# 市场状态 & 策略推荐工具 (P0-1)
# ============================================================

@mcp.tool()
def detect_regime(symbol: str, timeframe: str = "2h") -> dict:
    """检测市场状态 + 获取策略推荐

    Args:
        symbol: 交易对 (e.g. SOL/USDT, ETH/USDT)
        timeframe: K线周期 (默认2h)

    Returns:
        {regime: {trend, volatility, volume, confidence},
         recommendations: [{strategy, fit_score, reason}]}
    """
    return _get("/api/experiment/pipeline/regime",
                params={"symbol": symbol, "timeframe": timeframe})


# ============================================================
# 实验管线工具 (P0-2)
# ============================================================

@mcp.tool()
def run_experiment(
    symbol: str,
    timeframe: str = "2h",
    strategies: str = "DONCHIAN,BOLLINGER,RSI",
) -> dict:
    """运行策略实验管线：Regime → Generate → Backtest → Score → Best

    自动检测市场状态，生成候选参数组合，并行回测，多因子评分，输出最优策略。

    Args:
        symbol: 交易对 (e.g. SOL/USDT)
        timeframe: K线周期
        strategies: 逗号分隔的策略列表 (e.g. "DONCHIAN,BOLLINGER,RSI,ATRSTOP")

    Returns:
        {regime, best: {strategy, params, score, summary}, ranked: [...], duration_seconds}
    """
    return _get("/api/experiment/pipeline/run", params={
        "symbol": symbol,
        "timeframe": timeframe,
        "strategies": strategies,
        "max_workers": 4,
    })


@mcp.tool()
def quick_experiment(symbol: str = "SOL/USDT", timeframe: str = "2h") -> dict:
    """快速实验：对所有 Agent 标的运行实验管线

    Args:
        symbol: 可选，指定单个标的（留空则对所有 Agent 标的运行）
        timeframe: K线周期
    """
    if symbol:
        return _get("/api/experiment/pipeline/run", params={
            "symbol": symbol,
            "timeframe": timeframe,
            "strategies": "DONCHIAN,BOLLINGER,RSI",
            "max_workers": 2,
        })
    return _get("/api/experiment/pipeline/quick",
                params={"symbol": symbol, "timeframe": timeframe})


# ============================================================
# 回测工具
# ============================================================

@mcp.tool()
def list_backtest_strategies() -> dict:
    """列出所有可回测的策略"""
    return _get("/api/backtest/strategies")


@mcp.tool()
def compare_strategies(
    symbol: str = "ETH/USDT",
    timeframe: str = "4h",
    direction: str = "long",
) -> dict:
    """对比多个策略的回测表现

    Args:
        symbol: 交易对
        timeframe: K线周期
        direction: 交易方向 (long/short/both)

    Returns:
        {rankings: [{strategy, return, sharpe, drawdown, win_rate, trades}]}
    """
    return _get("/api/backtest/compare", params={
        "symbol": symbol, "timeframe": timeframe, "direction": direction
    })


# ============================================================
# 复盘工具
# ============================================================

@mcp.tool()
def get_replay_stats() -> dict:
    """获取交易复盘统计数据（胜率/盈亏比/期望值/最大回撤）"""
    return _get("/api/replay/stats")


@mcp.tool()
def get_replay_trades(limit: int = 50) -> list:
    """获取带市场状态标注的交易历史

    Args:
        limit: 返回数量
    """
    return _get("/api/replay/trades", params={"limit": limit})


# ============================================================
# 数据源状态
# ============================================================

@mcp.tool()
def data_providers_status() -> dict:
    """获取所有数据源健康状态（限流/熔断统计）"""
    return _get("/api/data/status")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))

    print(f"Trading System MCP Server v2")
    print(f"  Backend: {BASE_URL}")
    print(f"  Transport: {transport}")
    if transport in ("http", "sse"):
        print(f"  Listening: http://{host}:{port}")
        mcp.run(transport="sse" if transport == "sse" else "streamable-http",
                host=host, port=port)
    else:
        mcp.run(transport="stdio")
