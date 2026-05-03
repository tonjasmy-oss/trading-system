"""
行情监控与预警引擎
"""
import asyncio
import logging
from typing import Dict, List, Optional, Callable
from datetime import datetime
import time

from stock_api import get_stock, batch_get_stocks
from crypto_api import get_crypto_price, get_crypto_prices
from database import record_alert, get_positions
from feishu_alert import feishu_alert

logger = logging.getLogger(__name__)

class PriceMonitor:
    """价格监控引擎"""
    
    def __init__(self, check_interval: int = 60):
        self.check_interval = check_interval
        self.price_cache: Dict[str, float] = {}
        self.alert_callbacks: List[Callable] = []
        self.running = False
    
    def add_alert_callback(self, callback: Callable):
        """添加告警回调"""
        self.alert_callbacks.append(callback)
    
    def _get_market_key(self, symbol: str, market: str) -> str:
        return f"{symbol}_{market}"
    
    def get_price(self, symbol: str, market: str) -> Optional[float]:
        """获取当前价格"""
        key = self._get_market_key(symbol, market)
        
        try:
            if market.upper() == "CRYPTO":
                data = get_crypto_price(symbol)
            else:
                data = get_stock(symbol, market)
            
            if data and data.get("price"):
                price = data["price"]
                self.price_cache[key] = price
                return price
        except Exception as e:
            logger.error(f"获取价格失败 {symbol}({market}): {e}")
        
        return self.price_cache.get(key)
    
    def check_price_change(self, symbol: str, market: str, threshold: float = 0.05) -> Optional[Dict]:
        """检查价格变动"""
        key = self._get_market_key(symbol, market)
        old_price = self.price_cache.get(key)
        new_price = self.get_price(symbol, market)
        
        if new_price is None:
            return None
        
        if old_price is None:
            # 首次获取，不触发告警
            return None
        
        change_pct = (new_price - old_price) / old_price
        
        if abs(change_pct) >= threshold:
            return {
                "symbol": symbol,
                "market": market,
                "old_price": old_price,
                "new_price": new_price,
                "change_pct": change_pct,
                "threshold": threshold
            }
        
        return None
    
    async def monitor_loop(self, symbols: List[Dict], threshold: float = 0.05):
        """监控循环"""
        self.running = True
        logger.info(f"监控启动: {len(symbols)} 个标的")
        
        while self.running:
            for item in symbols:
                sym = item["symbol"]
                market = item["market"]
                
                # 检查价格变动
                alert_data = self.check_price_change(sym, market, threshold)
                if alert_data:
                    logger.warning(f"价格异动: {sym}({market}) {alert_data['change_pct']:.2%}")
                    
                    # 记录告警
                    record_alert(
                        symbol=sym,
                        market=market,
                        alert_type="PRICE_CHANGE",
                        price=alert_data["new_price"],
                        threshold=threshold,
                        message=f"价格变动 {alert_data['change_pct']:.2%}"
                    )
                    
                    # 发送飞书告警
                    feishu_alert.send_price_alert(
                        symbol=sym,
                        market=market,
                        price=alert_data["new_price"],
                        change_pct=alert_data["change_pct"] * 100,
                        threshold=threshold
                    )
                    
                    # 触发回调
                    for cb in self.alert_callbacks:
                        try:
                            cb(alert_data)
                        except Exception as e:
                            logger.error(f"告警回调失败: {e}")
                
                await asyncio.sleep(1)  # 避免请求过快
            
            await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """停止监控"""
        self.running = False

class PortfolioMonitor:
    """持仓监控 - 监控持仓品种价格"""
    
    def __init__(self, monitor: PriceMonitor = None):
        self.monitor = monitor or PriceMonitor()
    
    def get_watched_symbols(self) -> List[Dict]:
        """获取需要监控的持仓品种"""
        positions = get_positions()
        symbols = []
        for pos in positions:
            symbols.append({
                "symbol": pos["symbol"],
                "market": pos["market"]
            })
        return symbols
    
    async def start(self, threshold: float = 0.05):
        """启动持仓监控"""
        symbols = self.get_watched_symbols()
        if not symbols:
            logger.info("无持仓，无需监控")
            return
        
        await self.monitor.monitor_loop(symbols, threshold)

# 快捷函数
def quick_price_check(symbol: str, market: str = "US") -> Optional[Dict]:
    """快速价格查询"""
    if market.upper() == "CRYPTO":
        return get_crypto_price(symbol)
    else:
        return get_stock(symbol, market)

def quick_alert(symbol: str, market: str, alert_type: str, price: float, threshold: float):
    """快速发送告警"""
    record_alert(symbol, market, alert_type, price, threshold)
    feishu_alert.send_alert(symbol, market, alert_type, price, threshold)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # 测试价格查询
    print("BTC价格:", quick_price_check("BTC", "CRYPTO"))
    print("AAPL价格:", quick_price_check("AAPL", "US"))
    
    # 测试监控
    monitor = PriceMonitor(check_interval=10)
    print("价格监控已初始化")
