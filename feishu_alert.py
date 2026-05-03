"""
飞书告警推送模块
"""
import requests
import logging
from typing import Optional, Dict, List
from datetime import datetime
from config import FEISHU_WEBHOOK_URL, FEISHU_APP_ID, FEISHU_APP_SECRET

logger = logging.getLogger(__name__)

class FeishuAlert:
    """飞书告警推送类"""
    
    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url or FEISHU_WEBHOOK_URL
    
    def send_text(self, text: str) -> bool:
        """发送文本消息"""
        if not self.webhook_url:
            logger.warning("飞书 Webhook URL 未配置")
            return False
        
        try:
            payload = {
                "msg_type": "text",
                "content": {"text": text}
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"飞书推送失败: {e}")
            return False
    
    def send_alert(self, symbol: str, market: str, alert_type: str, 
                   price: float, threshold: float, message: str = "") -> bool:
        """发送告警消息"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        content = f"""🚨 交易告警
⏰ 时间: {now}
📈 品种: {symbol} ({market})
🔔 类型: {alert_type}
💰 当前价: {price}
🎯 阈值: {threshold}
📝 备注: {message or '无'}"""
        
        return self.send_text(content)
    
    def send_price_alert(self, symbol: str, market: str, price: float, 
                         change_pct: float, threshold: float) -> bool:
        """发送价格变动告警"""
        direction = "📈 上涨" if change_pct > 0 else "📉 下跌"
        alert_type = "价格突破" if abs(change_pct) >= threshold else "价格异动"
        
        return self.send_alert(
            symbol=symbol,
            market=market,
            alert_type=f"{direction} {alert_type}",
            price=price,
            threshold=threshold * 100,
            message=f"变动幅度: {change_pct:.2f}%"
        )
    
    def send_daily_summary(self, holdings: List[Dict], prices: Dict) -> bool:
        """发送每日行情汇总"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        lines = [f"📊 每日行情汇总\n⏰ {now}\n"]
        
        total_value = 0
        total_cost = 0
        
        for h in holdings:
            sym = h["symbol"]
            qty = h["quantity"]
            cost = h["avg_price"]
            
            price_data = prices.get(f"{sym}_{h['market']}")
            if price_data:
                current_price = price_data.get("price", cost)
                value = qty * current_price
                cost_total = qty * cost
                pnl = value - cost_total
                pnl_pct = (pnl / cost_total * 100) if cost_total else 0
                
                total_value += value
                total_cost += cost_total
                
                emoji = "🟢" if pnl >= 0 else "🔴"
                lines.append(f"{emoji} {sym}: {current_price:.2f} x {qty} = {value:.2f} (PnL: {pnl:.2f} / {pnl_pct:.1f}%)")
        
        if total_cost > 0:
            total_pnl = total_value - total_cost
            total_pnl_pct = total_pnl / total_cost * 100
            lines.append(f"\n💼 总计: 市值 {total_value:.2f} | 成本 {total_cost:.2f} | PnL {total_pnl:.2f} ({total_pnl_pct:.1f}%)")
        
        return self.send_text("\n".join(lines))

    def send_signal_alert(self, symbol: str, signal: str, price: float,
                          rsi: float, ai_verdict: str = "",
                          strategy: str = "RSI") -> bool:
        """发送交易信号通知（开仓/平仓信号）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        emoji = "🟢" if signal == "BUY" else ("🔴" if signal == "SELL" else "⚪️")
        
        content = f"""📡 交易信号
⏰ {now}
{emoji} 标的: {symbol}
📊 信号: {signal}
💰 价格: ${price:.4f}
📐 RSI: {rsi:.1f}
🧠 策略: {strategy}"""
        if ai_verdict:
            content += f"\n🤖 AI验证: {ai_verdict}"
        
        return self.send_text(content)

    def send_position_alert(self, symbol: str, side: str, price: float,
                            quantity: float, pnl_pct: float = 0.0,
                            stop_loss: float = 0.0, take_profit: float = 0.0,
                            reason: str = "") -> bool:
        """发送持仓变动通知（开仓/平仓）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        is_open = side.upper() in ("BUY", "OPEN", "LONG")
        emoji = "🟢" if is_open else "🔴"
        event = "开仓" if is_open else "平仓"
        
        content = f"""{'📌' if is_open else '🏁'} 持仓变动 — {event}
⏰ {now}
{emoji} 标的: {symbol}
💰 价格: ${price:.4f}
🔢 数量: {quantity:.6f}"""
        
        if not is_open and pnl_pct != 0:
            pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
            content += f"\n{pnl_emoji} 盈亏: {pnl_pct:+.2f}%"
        
        if reason:
            content += f"\n📝 原因: {reason}"
        
        if is_open:
            if stop_loss:
                content += f"\n🛡️ 止损: ${stop_loss:.4f}"
            if take_profit:
                content += f"\n🎯 止盈: ${take_profit:.4f}"
        
        return self.send_text(content)

    def send_risk_alert(self, level: str, message: str,
                        daily_loss_pct: float = 0.0,
                        total_exposure_pct: float = 0.0) -> bool:
        """发送风控告警（止损/回撤警戒）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        level_emoji = {"normal": "🟢", "caution": "🟡", "danger": "🟠", "lock": "🔴"}.get(level, "⚪️")
        
        content = f"""🚨 风控告警
⏰ {now}
{level_emoji} 级别: {level.upper()}
📝 信息: {message}"""
        if daily_loss_pct:
            content += f"\n📉 日内亏损: {daily_loss_pct:+.2f}%"
        if total_exposure_pct:
            content += f"\n📊 总暴露度: {total_exposure_pct:.1f}%"
        
        return self.send_text(content)

# 全局实例
feishu_alert = FeishuAlert()

if __name__ == "__main__":
    # 测试
    alert = FeishuAlert()
    print("发送测试告警:", alert.send_alert("BTC", "CRYPTO", "价格突破", 50000, 5))
