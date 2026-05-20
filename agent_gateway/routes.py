"""
Agent Gateway FastAPI 路由
"""

from fastapi import APIRouter, Header, HTTPException


def create_agent_router() -> APIRouter:
    """创建 Agent Gateway 路由器"""
    router = APIRouter(prefix="/api/agent/v1", tags=["agent"])

    @router.get("/status")
    async def agent_status():
        return {"version": "v1", "status": "operational"}

    @router.get("/markets")
    async def list_markets():
        """列出支持的市场"""
        return {
            "markets": [
                {"id": "CRYPTO", "name": "加密货币", "symbols_count": 200},
                {"id": "CN_STOCK", "name": "A股", "symbols_count": 5000},
                {"id": "US_STOCK", "name": "美股", "symbols_count": 8000},
                {"id": "FOREX", "name": "外汇", "symbols_count": 50},
            ]
        }

    @router.get("/price/{market}/{symbol}")
    async def get_price(market: str, symbol: str):
        """获取实时价格"""
        try:
            from data_providers import DataProviderFactory
            provider = DataProviderFactory.get(market.upper())
            if not provider:
                raise HTTPException(404, f"Unknown market: {market}")
            price = provider.get_price(symbol)
            if not price:
                raise HTTPException(404, f"Price not found: {symbol}")
            return price
        except Exception as e:
            raise HTTPException(500, str(e))

    return router
