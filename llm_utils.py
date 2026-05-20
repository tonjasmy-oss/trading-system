"""
统一 LLM 调用模块
借鉴 QuantDinger llm.py 的多 provider 设计，适配 Trading-System 环境变量
支持: DeepSeek / OpenAI / 自定义 OpenAI 兼容接口
"""

import json
import os
import logging
from typing import Optional, Dict, Any
from enum import Enum

import requests

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    CUSTOM = "custom"


PROVIDER_CONFIGS = {
    LLMProvider.DEEPSEEK: {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "env_api_key": "DEEPSEEK_API_KEY",
        "env_model": "AI_MODEL_OVERRIDE",
    },
    LLMProvider.OPENAI: {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "env_api_key": "OPENAI_API_KEY",
        "env_model": "AI_MODEL_OVERRIDE",
    },
    LLMProvider.CUSTOM: {
        "base_url": "",
        "default_model": "",
        "env_api_key": "AI_API_KEY",
        "env_model": "AI_MODEL",
    },
}


class LLMService:
    """统一 LLM 调用服务"""

    def __init__(self, provider: str = None):
        provider_str = provider or os.getenv("AI_MODEL", "deepseek")
        try:
            self.provider = LLMProvider(provider_str)
        except ValueError:
            logger.warning(f"未知 LLM provider: {provider_str}，回退到 deepseek")
            self.provider = LLMProvider.DEEPSEEK

        cfg = PROVIDER_CONFIGS[self.provider]
        self.base_url = os.getenv("AI_BASE_URL", "") or cfg["base_url"]
        self.model = (
            os.getenv(cfg.get("env_model", ""), "")
            or os.getenv("AI_MODEL", "")
            or cfg["default_model"]
        )
        self.api_key = os.getenv(cfg["env_api_key"], "") or os.getenv("AI_API_KEY", "")

        if not self.api_key:
            logger.warning(f"[LLM] {self.provider.value} API key 未配置")

        self.timeout = int(os.getenv("AI_REQUEST_TIMEOUT", "30"))

    def chat(
        self,
        messages: list,
        temperature: float = 0.3,
        max_tokens: int = 500,
        model: str = None,
    ) -> Optional[str]:
        """发送聊天请求，返回响应文本"""
        if not self.api_key:
            logger.error(f"[LLM] 未配置 {self.provider.value} API key")
            return None

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except requests.Timeout:
            logger.error(f"[LLM] 请求超时 ({self.timeout}s)")
            return None
        except requests.RequestException as e:
            logger.error(f"[LLM] 请求失败: {e}")
            return None
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error(f"[LLM] 响应解析失败: {e}")
            return None

    def ask_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 500,
    ) -> Optional[Dict[str, Any]]:
        """发送请求并解析 JSON 响应"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        if not text:
            return None

        # 尝试提取 JSON（可能被 markdown 代码块包裹）
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:] if len(lines) > 1 else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"[LLM] 非 JSON 响应: {text[:200]}")
            return {"raw": text}


# 全局单例
_llm_service: Optional[LLMService] = None


def get_llm() -> LLMService:
    """获取 LLM 服务单例"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service


# ============================================================
# AI 信号过滤（从 live_trading.py 提取为独立模块）
# ============================================================

class AISignalFilter:
    """AI 信号过滤器 —— 宏观情绪验证"""

    SYSTEM_PROMPT = """你是一个加密货币交易信号审核助手。根据技术指标信号和当前市场环境，判断该信号是否可信。

回复格式（严格 JSON）：
{
  "verdict": "APPROVE" | "HOLD" | "REJECT",
  "confidence": 0.0-1.0,
  "reason": "简短中文原因（15字以内）"
}

判断标准：
- APPROVE: 技术信号方向与宏观情绪一致，可执行
- HOLD: 市场方向不明，建议观望
- REJECT: 技术信号与宏观条件相悖，风险较高"""

    def __init__(self, model: str = "deepseek"):
        self.llm = LLMService(provider=model)

    def validate_signal(
        self,
        technical_signal: Dict[str, Any],
        market_ctx: Dict[str, Any] = None,
    ) -> tuple:
        """
        验证交易信号

        Args:
            technical_signal: {"signal": "BUY/SELL", "symbol": "SUI/USDT", "rsi": 35.2, ...}
            market_ctx: {"price": 1.05, "change_24h": -2.3, "volume_24h": ...}

        Returns:
            (filtered_signal, verdict_text)
        """
        signal_type = technical_signal.get("signal", "HOLD")
        symbol = technical_signal.get("symbol", "UNKNOWN")
        rsi = technical_signal.get("rsi", None)

        ctx_desc = f"当前信号: {signal_type} | 标的: {symbol}"
        if rsi is not None:
            ctx_desc += f" | RSI: {rsi:.1f}"
        if market_ctx:
            price = market_ctx.get("price")
            change = market_ctx.get("change_24h")
            if price:
                ctx_desc += f" | 价格: {price}"
            if change is not None:
                ctx_desc += f" | 24h涨跌: {change:+.2f}%"

        user_prompt = f"请评估以下交易信号：\n{ctx_desc}"

        result = self.llm.ask_json(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=200,
        )

        if result is None:
            # API 调用失败，放行信号（不阻塞交易）
            return technical_signal, "AI不可用(放行)"

        verdict = result.get("verdict", "HOLD")
        reason = result.get("reason", "AI评估异常")

        if verdict == "APPROVE":
            return technical_signal, f"AI批准({reason})"
        elif verdict == "REJECT":
            return None, f"AI否决({reason})"
        else:  # HOLD
            return None, f"AI模糊(HOLD)→观望  {reason}"


# ============================================================
# 便捷函数
# ============================================================

def ai_quick_ask(prompt: str, system: str = "你是量化交易助手，回答简洁专业。") -> Optional[str]:
    """快捷 AI 问答"""
    llm = get_llm()
    return llm.chat([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ])


def ai_analyze_market(symbol: str, price: float, change_24h: float, rsi: float = None) -> Optional[str]:
    """快捷市场分析"""
    prompt = f"分析 {symbol}：当前价格 {price}，24h 涨跌 {change_24h:+.2f}%"
    if rsi is not None:
        prompt += f"，RSI(14)={rsi:.1f}"
    prompt += "。给出50字以内的简要判断。"
    return ai_quick_ask(prompt)


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.INFO)
    svc = LLMService()
    print(f"Provider: {svc.provider.value}")
    print(f"Model: {svc.model}")
    print(f"API Key: {'已配置' if svc.api_key else '未配置'}")

    if svc.api_key:
        resp = svc.chat([{"role": "user", "content": "用一句话介绍量化交易"}])
        print(f"测试响应: {resp}")
