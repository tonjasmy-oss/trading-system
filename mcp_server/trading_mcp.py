"""
Trading System MCP Server - 交易系统MCP服务器

参考 QuantDinger quantdinger-mcp 设计
使用 FastMCP 框架

安装运行：
  pip3 install mcp
  python trading_mcp.py

环境变量：
  TRADING_SYSTEM_URL - 交易系统地址（默认 http://localhost:8081）
  MCP_TRANSPORT - 传输方式（stdio/http，默认stdio）
  MCP_HOST - HTTP绑定地址（默认127.0.0.1）
  MCP_PORT - HTTP端口（默认8000）
"""

import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


# ============================================================
# 配置
# ============================================================

BASE_URL = os.getenv("TRADING_SYSTEM_URL", "http://localhost:8081")
TIMEOUT_S = float(os.getenv("TRADING_SYSTEM_TIMEOUT_S", "60"))

_client = httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_S)


def _get(path: str, params: dict = None) -> Any:
    r = _client.get(path, params=params or {})
    return _unwrap(r)


def _post(path: str, json: dict = None) -> Any:
    r = _client.post(path, json=json or {})
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
        "Trading System MCP Server - 多市场量化交易系统。\n"
        "支持 A股/港股/美股/加密货币 的行情、回测、交易功能。\n"
        "所有工具都是只读或回测类，不暴露实盘交易。"
    ),
)


# ───────────────────────────── 市场数据工具 ─────────────────────────────

@mcp.tool()
def whoami() -> dict:
    """返回当前Agent身份信息"""
    return _get("/api/agent/v1/whoami")


@mcp.tool()
def list_markets() -> list:
    """列出所有支持的市场"""
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
def get_portfolio() -> list:
    """获取当前持仓"""
    return _get("/api/positions")


@mcp.tool()
def get_portfolio_value() -> dict:
    """获取账户市值统计"""
    return _get("/api/portfolio/value")


@mcp.tool()
def get_trades(limit: int = 50) -> list:
    """获取交易历史

    Args:
        limit: 返回数量（默认50）
    """
    return _get("/api/trades", params={"limit": limit})


# ───────────────────────────── 回测工具 ─────────────────────────────

@mcp.tool()
def submit_backtest(
    symbol: str,
    market: str = "CRYPTO",
    timeframe: str = "1H",
    start_date: str = "2024-01-01",
    end_date: str = "2024-12-31",
    strategy: str = "RSIStrategy",
    initial_capital: float = 10000,
    commission: float = 0.001,
    slippage: float = 0.0,
) -> dict:
    """提交回测任务

    Args:
        symbol: 标的代码
        market: 市场代码
        timeframe: K线周期
        start_date/end_date: 回测时间范围
        strategy: 策略名称
        initial_capital: 初始资金
        commission: 手续费率
        slippage: 滑点
    """
    # 注意：完整实现需要调用实际的回测API
    return {
        "job_id": f"bt_{symbol}_{int(os.time.time())}",
        "status": "submitted",
        "message": "回测任务已提交（完整回测功能开发中）",
        "params": {
            "symbol": symbol, "market": market, "timeframe": timeframe,
            "start_date": start_date, "end_date": end_date,
            "strategy": strategy, "initial_capital": initial_capital
        }
    }


@mcp.tool()
def get_job(job_id: str) -> dict:
    """查询任务状态

    Args:
        job_id: 任务ID
    """
    # 注意：完整实现需要实际的作业队列
    return {
        "job_id": job_id,
        "status": "pending",
        "message": "任务状态查询功能开发中"
    }


@mcp.tool()
def list_jobs(kind: str = None, limit: int = 50) -> list:
    """列出最近的任务

    Args:
        kind: 任务类型过滤
        limit: 返回数量
    """
    return []


# ───────────────────────────── 工具类 ─────────────────────────────

@mcp.tool()
def get_system_status() -> dict:
    """获取交易系统状态"""
    try:
        return _get("/api/system/status")
    except Exception as e:
        return {"error": str(e), "status": "unavailable"}


@mcp.tool()
def get_sansheng_status() -> dict:
    """获取三省六部架构状态"""
    try:
        return _get("/api/sansheng/status")
    except Exception as e:
        return {"error": str(e), "status": "unavailable"}


# ============================================================
# 传输配置
# ============================================================

def main():
    """入口函数"""
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    
    print(f"[trading-mcp] Starting MCP server...")
    print(f"[trading-mcp] Trading System: {BASE_URL}")
    print(f"[trading-mcp] Transport: {transport}")
    
    if transport == "http":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
