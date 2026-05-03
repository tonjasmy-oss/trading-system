"""
策略基类模块 - 参考 freqtrade IStrategy 设计
定义回测所需的标准策略接口
包含：
  - Strategy: 策略基类（抽象接口）
  - SMAcrossStrategy: 双 SMA 简单移动平均交叉策略
  - RSIStrategy: RSI 区间策略（超卖买入 / 超买卖出）
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


# ============================================================
# 信号枚举
# ============================================================

class Signal:
    """交易信号类型"""
    HOLD = 0      # 持仓不动
    BUY  = 1      # 买入信号
    SELL = -1     # 卖出信号


# ============================================================
# 策略配置数据类
# ============================================================

@dataclass
class StrategyConfig:
    """策略通用配置"""
    symbol:      str   = "BTC/USDT"   # 交易对
    timeframe:   str   = "1h"         # K线周期
    capital_pct: float = 1.0         # 每次下单资金占总资金比例（0~1）
    stop_loss:   float = 0.05        # 止损比例（5%）
    take_profit: float = 0.10        # 止盈比例（10%）


# ============================================================
# 策略基类
# ============================================================

class Strategy(ABC):
    """
    策略基类，定义回测引擎所需的标准接口

    设计参考 freqtrade IStrategy：
      - populate_indicators(): 填充技术指标
      - populate_entry_trend(): 生成入场信号
      - populate_exit_trend():  生成出场信号

    子类只需实现上述三个方法即可接入回测引擎
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()
        self._indicators: Dict[str, List[float]] = {}   # 缓存计算出的指标

    # -------------------- 抽象接口 --------------------

    @abstractmethod
    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        """
        根据 K线数据计算技术指标

        Args:
            candles: OHLCV 列表，每项含 open/high/low/close/volume/timestamp

        Returns:
            dict，键为指标名，值为与 candles 等长的 float 列表
            例如：{"sma20": [val1, val2, ...], "rsi": [val1, val2, ...]}
        """
        ...

    @abstractmethod
    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        """
        生成入场（买入）信号

        Args:
            candles: OHLCV 列表

        Returns:
            list of int，与 candles 等长，1=买入，0=持仓不动
        """
        ...

    @abstractmethod
    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        """
        生成出场（卖出）信号

        Args:
            candles: OHLCV 列表

        Returns:
            list of int，与 candles 等长，-1=卖出，0=持仓不动
        """
        ...

    # -------------------- 通用工具方法 --------------------

    def SMA(self, prices: List[float], period: int) -> List[float]:
        """
        计算简单移动平均线 SMA

        Args:
            prices: 价格列表（收盘价）
            period: 均线周期（如 20 表示 SMA20）

        Returns:
            与输入等长的列表，前 period-1 个为 NaN（0.0），之后为均线值
        """
        result = []
        for i in range(len(prices)):
            if i < period - 1:
                result.append(0.0)   # 数据不足时填充 0（视为无效）
            else:
                result.append(sum(prices[i - period + 1:i + 1]) / period)
        return result

    def EMA(self, prices: List[float], period: int) -> List[float]:
        """
        计算指数移动平均线 EMA

        Args:
            prices: 价格列表
            period: 均线周期

        Returns:
            与输入等长的列表，前 period-1 个为 0.0
        """
        if len(prices) < period:
            return [0.0] * len(prices)
        multiplier = 2 / (period + 1)
        # 前 period 个值用 SMA 初始化
        result = [0.0] * (period - 1)
        result.append(sum(prices[:period]) / period)
        for i in range(period, len(prices)):
            ema = (prices[i] - result[-1]) * multiplier + result[-1]
            result.append(ema)
        return result

    def RSI(self, prices: List[float], period: int = 14) -> List[float]:
        """
        计算相对强弱指数 RSI

        Args:
            prices:  价格列表（收盘价）
            period:  RSI 周期，默认 14

        Returns:
            与输入等长的列表，值域 0~100
        """
        if len(prices) < period + 1:
            return [50.0] * len(prices)

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]

        result = [50.0] * (period + 1)

        # 初始平均涨跌幅
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))

        for i in range(period + 1, len(deltas) + 1):
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
            if avg_loss == 0:
                result.append(100.0)
            else:
                rs = avg_gain / avg_loss
                result.append(100 - 100 / (1 + rs))

        # 与 prices 等长
        while len(result) < len(prices):
            result.insert(0, 50.0)
        return result

    def get_config(self) -> StrategyConfig:
        """返回当前策略配置"""
        return self.config


# ============================================================
# AI 信号过滤层（整合 VergeX AI 风格的多模型架构）
# 支持 DeepSeek / OpenAI，可对技术信号进行宏观情绪验证
# ============================================================

import os
import json
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AIModel(Enum):
    """支持的 AI 模型"""
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    MINIMAX = "minimax"


@dataclass
class MarketContext:
    """传递给 AI 的市场上下文数据"""
    symbol: str
    current_price: float
    price_change_24h_pct: float
    volume_24h: float
    rsi: float
    technical_signal: str  # BUY / SELL / HOLD
    position_status: str   # in_position / no_position
    entry_price: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None


class AISignalFilter:
    """
    AI 信号过滤器 — 参考 VergeX AI 的 DeepSeek/OpenAI 模型选择架构

    功能：
      - 在技术指标信号产生后，调用 AI 模型分析宏观情绪
      - AI 可能加强、否决或忽略技术信号
      - 支持 DeepSeek（低成本）和 OpenAI（高精度）两种模型

    使用方式：
        ai_filter = AISignalFilter(model=AIModel.DEEPSEEK)
        market_ctx = MarketContext(symbol="ETH/USDT", current_price=3500, ...)
        filtered_signal = ai_filter.validate_signal(
            technical_signal=Signal.BUY,
            market_context=market_ctx
        )
    """

    # DeepSeek API
    DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
    DEEPSEEK_MODEL = "deepseek-chat"

    # OpenAI API
    OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
    OPENAI_MODEL = "gpt-4o-mini"

    # MiniMax API
    MINIMAX_API_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    MINIMAX_MODEL = "MiniMax-Text-01"

    def __init__(
        self,
        model: AIModel = AIModel.DEEPSEEK,
        api_key: Optional[str] = None,
        cache_ttl_seconds: int = 300,  # 5分钟内相同信号不重复请求
    ):
        self.model = model
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("MINIMAX_API_KEY")
        self.cache_ttl = cache_ttl_seconds
        self._cache: Dict[str, tuple[float, str]] = {}  # key -> (timestamp, result)

    # -------------------- Prompt 构建 --------------------

    def _build_system_prompt(self) -> str:
        return """你是一位专业的加密货币交易分析师，擅长宏观市场分析和风险管理。

你的职责是验证技术指标信号，结合宏观市场情绪给出最终交易建议。

分析维度：
1. 宏观市场情绪（BTC走势、恐惧贪婪指数、美元指数）
2. 合约资金费率（判断多空博弈）
3. 链上数据（若有）
4. 市场结构（趋势强度、波动率）

输出格式（JSON，仅返回一个JSON对象，不要其他文字）：
{
  "verdict": "APPROVE" | "REJECT" | "HOLD",
  "confidence": 0.0~1.0,
  "reason": "简要说明原因（20字以内）",
  "risk_level": "LOW" | "MEDIUM" | "HIGH"
}

规则：
- APPROVE：AI认为技术信号可靠，支持执行
- REJECT：AI认为当前宏观环境不适合，建议否决
- HOLD：信号模糊，暂不执行，继续观察
- confidence > 0.7 时 VERDICT = APPROVE/REJECT 才有效
- confidence <= 0.7 时 VERDICT 强制为 HOLD"""

    def _build_user_prompt(self, ctx: MarketContext) -> str:
        action = "买入" if ctx.technical_signal == "BUY" else ("卖出" if ctx.technical_signal == "SELL" else "持仓")
        pos_info = f"持仓中，入场价 ${ctx.entry_price:.2f}，浮盈 {ctx.unrealized_pnl_pct:.2f}%" if ctx.position_status == "in_position" else "空仓"

        return f"""技术指标信号：{action}
币种：{ctx.symbol}
当前价格：${ctx.current_price:.2f}
24小时涨跌幅：{ctx.price_change_24h_pct:+.2f}%
24小时成交量：${ctx.volume_24h:.2f}
RSI(8)：{ctx.rsi:.2f}
持仓状态：{pos_info}

请分析宏观市场情绪，判断是否支持该技术信号。"""

    # -------------------- 核心方法 --------------------

    def _call_ai(self, prompt: str) -> Optional[Dict]:
        """调用 AI 模型"""
        if not self.api_key:
            logger.warning("AI_SIGNAL_FILTER: 未配置 API_KEY，跳过 AI 验证（透传技术信号）")
            return None

        if self.model == AIModel.DEEPSEEK:
            api_url = self.DEEPSEEK_API_URL
            model_name = self.DEEPSEEK_MODEL
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        elif self.model == AIModel.OPENAI:
            api_url = self.OPENAI_API_URL
            model_name = self.OPENAI_MODEL
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        elif self.model == AIModel.MINIMAX:
            api_url = self.MINIMAX_API_URL
            model_name = self.MINIMAX_MODEL
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        else:
            logger.warning(f"AI_FILTER: 不支持的模型类型: {self.model}")
            return None

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,  # 低温度保证输出稳定
            "max_tokens": 200,
        }

        try:
            import requests
            resp = requests.post(api_url, headers=headers, json=payload, timeout=20)
            if resp.status_code != 200:
                logger.error(f"AI API 返回错误 {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            # 提取 JSON
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            return json.loads(content.strip())
        except Exception as e:
            logger.error(f"AI API 调用失败: {e}")
            return None

    def _get_cache_key(self, ctx: MarketContext) -> str:
        return f"{ctx.symbol}:{ctx.technical_signal}:{ctx.current_price:.2f}"

    def _is_cache_valid(self, key: str) -> bool:
        import time
        if key not in self._cache:
            return False
        ts, _ = self._cache[key]
        return time.time() - ts < self.cache_ttl

    def validate_signal(
        self,
        technical_signal: int,  # Signal.BUY / .SELL / .HOLD
        market_context: MarketContext,
    ) -> tuple[int, str]:
        """
        验证技术信号，返回 (filtered_signal, ai_verdict)

        filtered_signal: 经过 AI 验证后的信号（可能与原信号不同）
        ai_verdict: AI 的判断说明
        """
        import time

        # 技术信号为 HOLD 时直接透传
        if technical_signal == Signal.HOLD:
            return Signal.HOLD, "技术信号HOLD，无需AI验证"

        # 构建缓存键
        sig_name = {Signal.BUY: "BUY", Signal.SELL: "SELL"}.get(technical_signal, "HOLD")
        ctx = market_context
        ctx.technical_signal = sig_name
        cache_key = self._get_cache_key(ctx)

        # 检查缓存
        if self._is_cache_valid(cache_key):
            _, cached_result = self._cache[cache_key]
            logger.info(f"AI_FILTER: 使用缓存结果 {cached_result['verdict']}")
            verdict = cached_result
        else:
            user_prompt = self._build_user_prompt(ctx)
            verdict = self._call_ai(user_prompt)
            if verdict:
                self._cache[cache_key] = (time.time(), verdict)

        # 无 API 响应时透传技术信号
        if not verdict:
            return technical_signal, "AI不可用，透传技术信号"

        v = verdict.get("verdict", "HOLD")
        confidence = verdict.get("confidence", 0.5)
        reason = verdict.get("reason", "")
        risk = verdict.get("risk_level", "MEDIUM")

        # 逻辑：confidence > 0.65 时才执行 VERDICT
        if confidence > 0.65:
            if v == "REJECT":
                logger.info(f"AI_FILTER: 否决信号 {sig_name}，confidence={confidence:.2f}，reason={reason}")
                return Signal.HOLD, f"AI否决({reason})"
            elif v == "APPROVE":
                logger.info(f"AI_FILTER: 批准信号 {sig_name}，confidence={confidence:.2f}，reason={reason}")
                return technical_signal, f"AI批准({reason})"

        # confidence <= 0.65 或 HOLD → 透传但标记风险
        risk_tag = f"⚠️{risk}" if risk == "HIGH" else ""
        return technical_signal, f"AI模糊(HOLD)→{sig_name} {risk_tag} {reason}"


# ============================================================
# 双 SMA 交叉策略
# ============================================================

class SMAcrossStrategy(Strategy):
    """
    双 SMA 交叉策略（Simple Moving Average Crossover）

    规则：
      - 当短期 SMA 从下穿越长期 SMA（金叉）→ 买入
      - 当短期 SMA 从上穿越长期 SMA（死叉）→ 卖出
      - 配合止损 / 止盈

    参数：
      - fast_period:  快线周期（默认 10）
      - slow_period:  慢线周期（默认 30）
    """

    def __init__(self, config: Optional[StrategyConfig] = None,
                 fast_period: int = 10, slow_period: int = 30):
        super().__init__(config)
        self.fast_period = fast_period
        self.slow_period = slow_period

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [c["close"] for c in candles]
        sma_fast = self.SMA(closes, self.fast_period)
        sma_slow = self.SMA(closes, self.slow_period)
        self._indicators = {
            "sma_fast": sma_fast,
            "sma_slow": sma_slow,
            "close":    closes,
        }
        return self._indicators

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        sma_fast = self._indicators.get("sma_fast", [])
        sma_slow = self._indicators.get("sma_slow", [])
        if not sma_fast or not sma_slow:
            self.populate_indicators(candles)
            sma_fast = self._indicators["sma_fast"]
            sma_slow = self._indicators["sma_slow"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            # 过滤无效值（前 slow_period 个为 0.0）
            if sma_fast[i] == 0 or sma_slow[i] == 0 or sma_fast[i-1] == 0 or sma_slow[i-1] == 0:
                continue
            # 金叉：快线从下穿越慢线
            if sma_fast[i] > sma_slow[i] and sma_fast[i - 1] <= sma_slow[i - 1]:
                signals[i] = Signal.BUY
        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        sma_fast = self._indicators.get("sma_fast", [])
        sma_slow = self._indicators.get("sma_slow", [])
        if not sma_fast or not sma_slow:
            self.populate_indicators(candles)
            sma_fast = self._indicators["sma_fast"]
            sma_slow = self._indicators["sma_slow"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            if sma_fast[i] == 0 or sma_slow[i] == 0 or sma_fast[i-1] == 0 or sma_slow[i-1] == 0:
                continue
            # 死叉：快线从上穿越慢线
            if sma_fast[i] < sma_slow[i] and sma_fast[i - 1] >= sma_slow[i - 1]:
                signals[i] = Signal.SELL
        return signals


# ============================================================
# RSI 区间策略
# ============================================================

class RSIStrategy(Strategy):
    """
    RSI 区间策略

    规则：
      - RSI < oversold_threshold（默认 30）→ 买入（超卖）
      - RSI > overbought_threshold（默认 70）→ 卖出（超买）
      - 配合止损 / 止盈

    参数：
      - rsi_period:      RSI 计算周期，默认 14
      - oversold:        超卖阈值，默认 30
      - overbought:      超买阈值，默认 70
    """

    def __init__(self, config: Optional[StrategyConfig] = None,
                 rsi_period: int = 14,
                 oversold: float = 30.0,
                 overbought: float = 70.0):
        super().__init__(config)
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [c["close"] for c in candles]
        rsi = self.RSI(closes, self.rsi_period)
        self._indicators = {
            "rsi":   rsi,
            "close": closes,
        }
        return self._indicators

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        rsi = self._indicators.get("rsi", [])
        if not rsi:
            self.populate_indicators(candles)
            rsi = self._indicators["rsi"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            # RSI 从超卖区回升（防止重复信号：只在超卖区域内首次转升时买入）
            if rsi[i] >= self.oversold and rsi[i] > rsi[i - 1] and rsi[i - 1] <= self.oversold:
                signals[i] = Signal.BUY
        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        rsi = self._indicators.get("rsi", [])
        if not rsi:
            self.populate_indicators(candles)
            rsi = self._indicators["rsi"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            # RSI 进入超买区后回落时卖出
            if rsi[i] <= self.overbought and rsi[i] < rsi[i - 1] and rsi[i - 1] >= self.overbought:
                signals[i] = Signal.SELL
        return signals


# ============================================================
# MACD 策略
# ============================================================

class MACDStrategy(Strategy):
    """
    MACD 策略（Moving Average Convergence Divergence）

    规则：
      - MACD 线从下穿越 Signal 线（金叉）→ 买入
      - MACD 线从上穿越 Signal 线（死叉）→ 卖出
      - 辅助：MACD 柱由负转正（动能增强）

    参数：
      - fast_period:   快线 EMA 周期（默认 12）
      - slow_period:   慢线 EMA 周期（默认 26）
      - signal_period: Signal 线 EMA 周期（默认 9）
    """

    def __init__(self, config: Optional[StrategyConfig] = None,
                 fast_period: int = 12,
                 slow_period: int = 26,
                 signal_period: int = 9):
        super().__init__(config)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [c["close"] for c in candles]

        # 计算快线和慢线 EMA
        ema_fast = self.EMA(closes, self.fast_period)
        ema_slow = self.EMA(closes, self.slow_period)

        # MACD 线 = 快线 - 慢线
        macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]

        # Signal 线 = MACD 的 EMA
        signal_line = self._calc_ema_of_list(macd_line, self.signal_period)

        # MACD 柱 = MACD 线 - Signal 线
        macd_hist = [macd_line[i] - signal_line[i] for i in range(len(closes))]

        self._indicators = {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_hist,
            "close": closes,
        }
        return self._indicators

    def _calc_ema_of_list(self, values: List[float], period: int) -> List[float]:
        if len(values) < period:
            return [0.0] * len(values)
        multiplier = 2 / (period + 1)
        result = [0.0] * (period - 1)
        result.append(sum(values[:period]) / period)
        for i in range(period, len(values)):
            ema = (values[i] - result[-1]) * multiplier + result[-1]
            result.append(ema)
        return result

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        macd = self._indicators.get("macd", [])
        signal = self._indicators.get("signal", [])
        hist = self._indicators.get("histogram", [])

        if not macd:
            self.populate_indicators(candles)
            macd = self._indicators["macd"]
            signal = self._indicators["signal"]
            hist = self._indicators["histogram"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            if macd[i] == 0 or signal[i] == 0 or macd[i-1] == 0 or signal[i-1] == 0:
                continue
            # 金叉：MACD 从下穿越 Signal
            crossed_up = macd[i] > signal[i] and macd[i-1] <= signal[i-1]
            # 辅助：MACD 柱由负转正（动能确认）
            hist_confirm = hist[i] > 0 and hist[i] > hist[i-1]
            if crossed_up and (hist_confirm or hist[i-1] < 0):
                signals[i] = Signal.BUY
        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        macd = self._indicators.get("macd", [])
        signal = self._indicators.get("signal", [])

        if not macd:
            self.populate_indicators(candles)
            macd = self._indicators["macd"]
            signal = self._indicators["signal"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            if macd[i] == 0 or signal[i] == 0 or macd[i-1] == 0 or signal[i-1] == 0:
                continue
            # 死叉：MACD 从上穿越 Signal
            if macd[i] < signal[i] and macd[i-1] >= signal[i-1]:
                signals[i] = Signal.SELL
        return signals


# ============================================================
# Bollinger Bands 策略
# ============================================================

class BollingerBandsStrategy(Strategy):
    """
    Bollinger Bands 策略（布林带策略）

    规则：
      - 价格下穿下轨（超卖）→ 买入
      - 价格上穿上轨（超买）→ 卖出
      - 布林带收口（波动率极低）后开口（趋势启动）

    参数：
      - period:    均线周期（默认 20）
      - std_dev:   标准差倍数（默认 2.0）
      - oversold_threshold: 下轨乘数（默认 1.0 = 价格触及下轨）
    """

    def __init__(self, config: Optional[StrategyConfig] = None,
                 period: int = 20,
                 std_dev: float = 2.0,
                 oversold_threshold: float = 0.0,
                 overbought_threshold: float = 0.0):
        super().__init__(config)
        self.period = period
        self.std_dev = std_dev
        self.oversold_threshold = oversold_threshold
        self.overbought_threshold = overbought_threshold

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [c["close"] for c in candles]

        # 计算中轨（SMA）
        sma = self.SMA(closes, self.period)

        # 计算标准差
        std = self._calc_rolling_std(closes, self.period)

        # 上轨 = SMA + std_dev * std
        # 下轨 = SMA - std_dev * std
        upper = [sma[i] + self.std_dev * std[i] if sma[i] != 0 else 0.0 for i in range(len(closes))]
        lower = [sma[i] - self.std_dev * std[i] if sma[i] != 0 else 0.0 for i in range(len(closes))]

        # 布林带宽度（收口检测）
        bandwidth = [upper[i] - lower[i] if upper[i] != 0 else 0.0 for i in range(len(closes))]

        # 布林带宽度变化率（开口/收口检测）
        bandwidth_change = [0.0] + [bandwidth[i] - bandwidth[i-1] for i in range(1, len(bandwidth))]

        self._indicators = {
            "sma": sma,
            "upper": upper,
            "lower": lower,
            "bandwidth": bandwidth,
            "bandwidth_change": bandwidth_change,
            "close": closes,
        }
        return self._indicators

    def _calc_rolling_std(self, prices: List[float], period: int) -> List[float]:
        result = [0.0] * len(prices)
        for i in range(period - 1, len(prices)):
            chunk = prices[i - period + 1:i + 1]
            mean = sum(chunk) / period
            variance = sum((p - mean) ** 2 for p in chunk) / period
            result[i] = variance ** 0.5
        return result

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        lower = self._indicators.get("lower", [])
        closes = self._indicators.get("close", [])
        bw_change = self._indicators.get("bandwidth_change", [])

        if not lower:
            self.populate_indicators(candles)
            lower = self._indicators["lower"]
            closes = self._indicators["close"]
            bw_change = self._indicators["bandwidth_change"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            if lower[i] == 0 or closes[i] == 0:
                continue
            # 价格下穿下轨（布林带下轨买入）
            touched_lower = closes[i] <= lower[i] and closes[i-1] > lower[i-1]
            # 布林带开口确认（趋势启动）
            expanding = bw_change[i] > 0 if bw_change[i] != 0 else False
            if touched_lower and (expanding or i > len(candles) * 0.5):
                signals[i] = Signal.BUY
        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        upper = self._indicators.get("upper", [])
        closes = self._indicators.get("close", [])

        if not upper:
            self.populate_indicators(candles)
            upper = self._indicators["upper"]
            closes = self._indicators["close"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            if upper[i] == 0 or closes[i] == 0:
                continue
            # 价格上穿上轨（布林带上轨卖出）
            if closes[i] >= upper[i] and closes[i-1] < upper[i-1]:
                signals[i] = Signal.SELL
        return signals


# ============================================================
# ATR 真实波动幅度（用于市场结构分析）
# ============================================================

def compute_atr(candles: List[Dict], period: int = 14) -> List[float]:
    """
    计算 Average True Range（ATR）
    用于衡量市场波动率和设置动态止损

    Args:
        candles: OHLCV 列表
        period: ATR 周期（默认 14）

    Returns:
        ATR 列表（与 candles 等长）
    """
    if len(candles) < 2:
        return [0.0] * len(candles)

    true_ranges = []
    for i in range(len(candles)):
        high = candles[i].get("high", candles[i].get("close", 0))
        low = candles[i].get("low", candles[i].get("close", 0))

        if i == 0:
            tr = high - low
        else:
            prev_close = candles[i-1].get("close", 0)
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return [sum(true_ranges) / len(true_ranges)] * len(true_ranges) if true_ranges else [0.0] * len(candles)

    # 初始 ATR = 前 period 个 TR 的均值
    atr = [0.0] * (period - 1)
    atr.append(sum(true_ranges[:period]) / period)

    for i in range(period, len(true_ranges)):
        atr.append((atr[-1] * (period - 1) + true_ranges[i]) / period)

    return atr


def compute_volatility(candles: List[Dict], period: int = 20) -> List[float]:
    """
    计算历史波动率（用于市场结构分析）
    返回每日收益率的标准差（年化）
    """
    closes = [c["close"] for c in candles]
    if len(closes) < period + 1:
        return [0.0] * len(closes)

    volatility = []
    for i in range(period, len(closes)):
        returns = []
        for j in range(i - period + 1, i):
            if closes[j] != 0:
                returns.append((closes[j+1] - closes[j]) / closes[j])
        if len(returns) > 1:
            mean_r = sum(returns) / len(returns)
            variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            vol = (variance ** 0.5) * (252 ** 0.5)  # 年化
            volatility.append(vol)
        else:
            volatility.append(0.0)

    return [0.0] * period + volatility


def detect_trend_strength(candles: List[Dict], period: int = 20) -> List[float]:
    """
    检测趋势强度（ADX 简化版）
    返回值 > 25 表示趋势较强，> 40 表示趋势很强
    """
    if len(candles) < period + 1:
        return [0.0] * len(candles)

    closes = [c["close"] for c in candles]
    highs = [c.get("high", c["close"]) for c in candles]
    lows = [c.get("low", c["close"]) for c in candles]

    # 计算 +DM 和 -DM
    plus_dm = []
    minus_dm = []
    tr_list = []

    for i in range(1, len(candles)):
        high_diff = highs[i] - highs[i-1]
        low_diff = lows[i-1] - lows[i]

        if high_diff > low_diff and high_diff > 0:
            plus_dm.append(high_diff)
        else:
            plus_dm.append(0.0)

        if low_diff > high_diff and low_diff > 0:
            minus_dm.append(low_diff)
        else:
            minus_dm.append(0.0)

        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)

    # 计算平滑后的 DM 和 TR
    smooth_plus = sum(plus_dm[:period]) / period
    smooth_minus = sum(minus_dm[:period]) / period
    smooth_tr = sum(tr_list[:period]) / period

    adx_values = [0.0] * (period * 2)

    for i in range(period, len(plus_dm)):
        smooth_plus = (smooth_plus * (period - 1) + plus_dm[i]) / period
        smooth_minus = (smooth_minus * (period - 1) + minus_dm[i]) / period
        smooth_tr = (smooth_tr * (period - 1) + tr_list[i]) / period

        if smooth_tr == 0:
            adx_values.append(0.0)
            continue

        plus_di = (smooth_plus / smooth_tr) * 100
        minus_di = (smooth_minus / smooth_tr) * 100

        di_sum = plus_di + minus_di
        if di_sum == 0:
            adx_values.append(0.0)
            continue

        dx = abs(plus_di - minus_di) / di_sum * 100

        if len(adx_values) < period:
            adx_values.append(dx)
        else:
            adx = (adx_values[-1] * (period - 1) + dx) / period
            adx_values.append(adx)

    return adx_values

