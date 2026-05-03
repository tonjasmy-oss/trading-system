"""
持仓管理模块
"""
from typing import List, Dict, Optional
from database import (
    add_position, remove_position, get_positions,
    record_trade, get_trades, get_alerts
)
from stock_api import get_stock
from crypto_api import get_crypto_price

class Portfolio:
    """持仓管理类"""
    
    def __init__(self):
        pass
    
    def buy(self, symbol: str, market: str, quantity: float, price: float) -> bool:
        """买入"""
        try:
            # 记录交易
            record_trade(symbol, market, "BUY", quantity, price)
            # 更新持仓
            add_position(symbol, market, quantity, price)
            return True
        except Exception as e:
            print(f"买入失败: {e}")
            return False
    
    def sell(self, symbol: str, market: str, quantity: float, price: float) -> bool:
        """卖出"""
        try:
            # 记录交易
            record_trade(symbol, market, "SELL", quantity, price)
            # 更新持仓
            return remove_position(symbol, market, quantity)
        except Exception as e:
            print(f"卖出失败: {e}")
            return False
    
    def get_positions(self) -> List[Dict]:
        """获取当前持仓"""
        return get_positions()
    
    def get_position_value(self) -> Dict:
        """计算持仓市值和盈亏"""
        positions = self.get_positions()
        
        total_cost = 0
        total_value = 0
        details = []
        
        for pos in positions:
            sym = pos["symbol"]
            market = pos["market"]
            qty = pos["quantity"]
            avg_cost = pos["avg_price"]
            
            # 获取当前价格
            if market == "CRYPTO":
                price_data = get_crypto_price(sym)
            else:
                price_data = get_stock(sym, market)
            
            if price_data:
                current_price = price_data.get("price", avg_cost)
            else:
                current_price = avg_cost
            
            cost = qty * avg_cost
            value = qty * current_price
            pnl = value - cost
            pnl_pct = (pnl / cost * 100) if cost else 0
            
            total_cost += cost
            total_value += value
            
            details.append({
                "symbol": sym,
                "market": market,
                "quantity": qty,
                "avg_cost": avg_cost,
                "current_price": current_price,
                "cost": cost,
                "value": value,
                "pnl": pnl,
                "pnl_pct": pnl_pct
            })
        
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
        
        return {
            "total_cost": total_cost,
            "total_value": total_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "positions": details
        }
    
    def get_trades(self, limit: int = 50) -> List[Dict]:
        """获取交易历史"""
        return get_trades(limit)
    
    def get_alerts(self, limit: int = 20) -> List[Dict]:
        """获取告警历史"""
        return get_alerts(limit)

if __name__ == "__main__":
    portfolio = Portfolio()
    
    # 测试买入
    print("买入测试:", portfolio.buy("BTC", "CRYPTO", 0.1, 45000))
    
    # 获取持仓
    print("\n当前持仓:", portfolio.get_positions())
    
    # 获取市值
    print("\n持仓市值:", portfolio.get_position_value())
