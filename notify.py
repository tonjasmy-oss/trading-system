"""
多通知渠道模块 v2
借鉴 QuantDinger signal_notifier.py 的多渠道设计
支持: 飞书 / Telegram / Discord / Email / 自定义 Webhook
"""

import os
import json
import logging
from typing import Optional, List, Dict
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


class NotifyManager:
    """统一通知管理器 —— 借鉴 QuantDinger 的 per-strategy notification_config 设计"""

    def __init__(self):
        self.channels = []

        # 飞书
        self.feishu_webhook = os.getenv("FEISHU_WEBHOOK_URL", "")
        if self.feishu_webhook:
            self.channels.append("feishu")

        # Telegram
        self.tg_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if self.tg_bot_token and self.tg_chat_id:
            self.channels.append("telegram")

        # Discord Webhook（借鉴 QuantDinger）
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
        if self.discord_webhook:
            self.channels.append("discord")

        # Email SMTP
        self.email_smtp_host = os.getenv("EMAIL_SMTP_HOST", "")
        self.email_smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
        self.email_from = os.getenv("EMAIL_FROM", "")
        self.email_to = os.getenv("EMAIL_TO", "")
        self.email_password = os.getenv("EMAIL_PASSWORD", "")
        if self.email_smtp_host and self.email_from:
            self.channels.append("email")

        # 自定义 Webhook（借鉴 QuantDinger）
        self.custom_webhook = os.getenv("CUSTOM_WEBHOOK_URL", "")
        self.custom_webhook_secret = os.getenv("CUSTOM_WEBHOOK_SECRET", "")
        if self.custom_webhook:
            self.channels.append("webhook")

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "TradingSystem/2.0"})

    # ── 核心发送接口 ──────────────────────────────────────

    def send(self, title: str, body: str, channels: List[str] = None):
        """发送通知到指定渠道（默认全部已配置渠道）"""
        targets = channels or self.channels
        for ch in targets:
            method = getattr(self, f"_send_{ch}", None)
            if method:
                try:
                    method(title, body)
                except Exception as e:
                    logger.error(f"[通知] {ch} 发送失败: {e}")

    # ── 渠道实现 ──────────────────────────────────────────

    def _send_feishu(self, title: str, body: str):
        """飞书 Webhook 消息"""
        if not self.feishu_webhook:
            return
        payload = {
            "msg_type": "text",
            "content": {"text": f"{title}\n{body}"},
        }
        self._session.post(self.feishu_webhook, json=payload, timeout=10)

    def _send_telegram(self, title: str, body: str):
        """Telegram Bot 消息"""
        url = f"https://api.telegram.org/bot{self.tg_bot_token}/sendMessage"
        text = f"*{title}*\n{body}" if len(title + body) < 4000 else f"{title}\n\n{body[:3800]}..."
        self._session.post(url, json={
            "chat_id": self.tg_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)

    def _send_discord(self, title: str, body: str):
        """Discord Webhook 消息 —— 借鉴 QuantDinger signal_notifier"""
        if not self.discord_webhook:
            return
        # Discord 消息限制 2000 字符
        content = f"**{title}**\n{body}"
        if len(content) > 1900:
            content = content[:1900] + "..."
        payload = {"content": content}
        self._session.post(self.discord_webhook, json=payload, timeout=10)

    def _send_email(self, title: str, body: str):
        """Email SMTP 消息"""
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = title
        msg["From"] = self.email_from
        msg["To"] = self.email_to or self.email_from

        with smtplib.SMTP(self.email_smtp_host, self.email_smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if self.email_password:
                smtp.login(self.email_from, self.email_password)
            smtp.send_message(msg)

    def _send_webhook(self, title: str, body: str):
        """自定义 Webhook（JSON POST，可选 HMAC 签名）"""
        if not self.custom_webhook:
            return
        payload = {"title": title, "body": body, "timestamp": datetime.now().isoformat()}
        headers = {"Content-Type": "application/json"}
        if self.custom_webhook_secret:
            import hmac, hashlib
            sig = hmac.new(
                self.custom_webhook_secret.encode(),
                json.dumps(payload, sort_keys=True).encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Signature"] = sig
        self._session.post(self.custom_webhook, json=payload, headers=headers, timeout=10)

    # ── 格式化通知（借鉴 QuantDinger signal_notifier 的信号格式化）──

    def signal_alert(
        self, symbol: str, signal: str, price: float,
        rsi: float = None, ai_verdict: str = "", strategy: str = "RSI",
    ):
        """交易信号通知"""
        now = datetime.now().strftime("%H:%M:%S")
        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪️"}.get(signal, "⚪️")
        title = f"📡 交易信号 | {symbol}"
        body = f"{emoji} 信号: {signal} | 价格: ${price:.4f} | 策略: {strategy}"
        if rsi is not None:
            body += f" | RSI: {rsi:.1f}"
        if ai_verdict:
            body += f"\n🤖 AI: {ai_verdict}"
        body += f"\n⏰ {now}"
        self.send(title, body)

    def position_alert(
        self, symbol: str, side: str, price: float, quantity: float = 0,
        pnl_pct: float = 0, reason: str = "", stop_loss: float = 0, take_profit: float = 0,
    ):
        """持仓变动通知"""
        is_open = side.upper() in ("BUY", "LONG", "OPEN")
        event = "开仓" if is_open else "平仓"
        pnl_str = f"{'🟢' if pnl_pct >= 0 else '🔴'} 盈亏: {pnl_pct:+.2f}%" if not is_open and pnl_pct != 0 else ""
        title = f"{'📌' if is_open else '🏁'} {event} | {symbol}"
        body = f"价格: ${price:.4f} | 数量: {quantity:.6f}"
        if pnl_str:
            body += f"\n{pnl_str}"
        if reason:
            body += f"\n📝 {reason}"
        if is_open and stop_loss:
            body += f"\n🛡️ 止损: ${stop_loss:.4f}"
        if is_open and take_profit:
            body += f"\n🎯 止盈: ${take_profit:.4f}"
        self.send(title, body)

    def risk_alert(self, level: str, message: str, daily_loss_pct: float = 0, exposure_pct: float = 0):
        """风控告警"""
        emoji = {"normal": "🟢", "caution": "🟡", "danger": "🟠", "lock": "🔴"}.get(level, "⚪️")
        title = f"🚨 风控告警 [{level.upper()}]"
        body = message
        if daily_loss_pct:
            body += f"\n📉 日内亏损: {daily_loss_pct:+.2f}%"
        if exposure_pct:
            body += f"\n📊 暴露度: {exposure_pct:.1f}%"
        self.send(title, body)

    def daily_summary(self, holdings: List[Dict], total_equity: float, total_pnl: float, total_pnl_pct: float):
        """每日汇总"""
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        title = "📊 每日持仓汇总"
        body = f"总权益: {total_equity:.2f} USDT\n{pnl_emoji} PnL: {total_pnl:+.2f} ({total_pnl_pct:+.2f}%)\n"
        for h in holdings[:10]:
            body += f"  {h.get('symbol','?')}: {h.get('quantity',0):.4f} @ {h.get('avg_price',0):.4f}\n"
        self.send(title, body)


# ── 全局单例 ──────────────────────────────────────────────

_notify_mgr: Optional[NotifyManager] = None


def get_notifier() -> NotifyManager:
    global _notify_mgr
    if _notify_mgr is None:
        _notify_mgr = NotifyManager()
    return _notify_mgr


def send_alert(title: str, body: str, channels: List[str] = None):
    """快捷发送告警（默认所有已配置渠道）"""
    get_notifier().send(title, body, channels)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mgr = NotifyManager()
    print(f"已配置渠道: {mgr.channels}")
    if mgr.channels:
        mgr.send("🧪 测试通知", "通知模块工作正常", channels=mgr.channels[:1])
        print("测试通知已发送")
    else:
        print("未配置任何通知渠道，请设置环境变量")
