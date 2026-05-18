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
    symbol:          str   = "BTC/USDT"   # 交易对
    timeframe:       str   = "1h"         # K线周期
    capital_pct:     float = 1.0          # 每次下单资金占总资金比例（0~1）
    stop_loss:       float = 0.05         # 止损比例（5%）
    take_profit:     float = 0.10         # 止盈比例（10%）
    commission_pct:  float = 0.001        # 手续费率（默认 0.1% = 现货 taker fee）
    slippage_pct:    float = 0.0005       # 滑点率（默认 0.05%）
    trade_direction: str   = "long"       # 交易方向: "long" | "short" | "both"


# ============================================================
# 公共指标计算（模块级，供策略类和外部模块共享）
# ============================================================

def compute_rsi(prices: List[float], period: int = 14) -> List[float]:
    """
    计算相对强弱指数 RSI (Wilder's smoothing)

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
        self._validate_config()

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
        """计算相对强弱指数 RSI（委托给模块级函数）"""
        return compute_rsi(prices, period)

    def _validate_config(self):
        """校验参数合法性，防止颠倒的参数导致策略反向交易"""
        c = self.config
        if c.stop_loss <= 0:
            raise ValueError(f"stop_loss 必须 > 0，当前值: {c.stop_loss}")
        if c.take_profit <= 0:
            raise ValueError(f"take_profit 必须 > 0，当前值: {c.take_profit}")
        if c.stop_loss >= c.take_profit:
            raise ValueError(
                f"stop_loss({c.stop_loss}) 必须 < take_profit({c.take_profit})，"
                f"否则盈亏比倒挂"
            )
        if not 0 < c.capital_pct <= 1.0:
            raise ValueError(f"capital_pct 必须在 (0, 1] 范围内，当前值: {c.capital_pct}")
        if c.commission_pct < 0:
            raise ValueError(f"commission_pct 不能为负，当前值: {c.commission_pct}")
        if c.slippage_pct < 0:
            raise ValueError(f"slippage_pct 不能为负，当前值: {c.slippage_pct}")

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
系统支持做多（买入）和做空（卖出开仓）双向交易，请同等对待两个方向的信号。

分析维度：
1. 宏观市场情绪（BTC走势、恐惧贪婪指数、美元指数）
2. 合约资金费率（判断多空博弈：正费率=多头拥挤利于做空，负费率=空头拥挤利于做多）
3. 链上数据（若有）
4. 市场结构（趋势强度、波动率）：下跌趋势中做空信号可信度更高，上涨趋势中做多信号可信度更高

输出格式（JSON，仅返回一个JSON对象，不要其他文字）：
{
  "verdict": "APPROVE" | "REJECT" | "HOLD",
  "confidence": 0.0~1.0,
  "reason": "简要说明原因（20字以内）",
  "risk_level": "LOW" | "MEDIUM" | "HIGH"
}

规则：
- APPROVE：AI认为技术信号可靠，支持执行（含做空）
- REJECT：AI认为当前宏观环境不适合，建议否决
- HOLD：信号模糊，暂不执行，继续观察
- confidence > 0.5 时 VERDICT = APPROVE/REJECT 才有效
- confidence <= 0.5 时 VERDICT 强制为 HOLD
- 严格风控：价格连续下跌且24h跌幅 > 5% 时对买入信号应谨慎，倾向 REJECT 或 HOLD
- 模棱两可时给 HOLD，让系统继续观察而非贸然入场
- 只有宏观情绪和技术信号方向一致时才给 APPROVE"""

    def _build_user_prompt(self, ctx: MarketContext) -> str:
        action = "买入(做多)" if ctx.technical_signal == "BUY" else ("卖出(做空)" if ctx.technical_signal == "SELL" else "持仓")
        pos_info = f"持仓中，入场价 ${ctx.entry_price:.2f}，浮盈 {ctx.unrealized_pnl_pct:.2f}%" if ctx.position_status == "in_position" else "空仓"

        return f"""技术指标信号：{action}
币种：{ctx.symbol}
当前价格：${ctx.current_price:.2f}
24小时涨跌幅：{ctx.price_change_24h_pct:+.2f}%
24小时成交量：${ctx.volume_24h:.2f}
RSI(8)：{ctx.rsi:.2f}
持仓状态：{pos_info}

请分析宏观市场情绪，判断是否支持该技术信号（买入=做多，卖出=做空）。"""

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

        # ── 极端行情前置拦截：避免在剧烈下跌中盲目买入 ──
        ctx = market_context
        if technical_signal == Signal.BUY:
            # 24h 跌幅 > 8%：强制否决买入
            if ctx.price_change_24h_pct < -8.0:
                return Signal.HOLD, f"AI否决(24h跌幅{ctx.price_change_24h_pct:.1f}%极端，禁止买入)"
            # RSI < 15 极深超卖 + 仍在下跌：建议观望
            if ctx.rsi < 15.0 and ctx.price_change_24h_pct < -3.0:
                return Signal.HOLD, f"AI否决(RSI={ctx.rsi:.1f}极深超卖+仍在下跌，等待企稳)"
        if technical_signal == Signal.SELL:
            # 24h 涨幅 > 10%：强制否决卖出（做空）
            if ctx.price_change_24h_pct > 10.0:
                return Signal.HOLD, f"AI否决(24h涨幅{ctx.price_change_24h_pct:.1f}%极端，禁止做空)"

        # 构建缓存键（极端行情拦截已完成，更新 ctx.technical_signal 后用于 AI 查询）
        sig_name = {Signal.BUY: "BUY", Signal.SELL: "SELL"}.get(technical_signal, "HOLD")
        market_context.technical_signal = sig_name
        cache_key = self._get_cache_key(market_context)

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

        # 逻辑：confidence > 0.5 时才执行 VERDICT
        if confidence > 0.5:
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
            # 死叉：MACD 从上穿越 Signal
            crossed_down = macd[i] < signal[i] and macd[i-1] >= signal[i-1]
            # 辅助：MACD 柱由正转负（动能确认，与 BUY 对称）
            hist_confirm = hist[i] < 0 and hist[i] < hist[i-1]
            if crossed_down and (hist_confirm or hist[i-1] > 0):
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
        bw_change = self._indicators.get("bandwidth_change", [])

        if not upper:
            self.populate_indicators(candles)
            upper = self._indicators["upper"]
            closes = self._indicators["close"]
            bw_change = self._indicators["bandwidth_change"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            if upper[i] == 0 or closes[i] == 0:
                continue
            # 价格上穿上轨（布林带上轨卖出）
            touched_upper = closes[i] >= upper[i] and closes[i-1] < upper[i-1]
            # 布林带开口确认（趋势启动，与 BUY 对称）
            expanding = bw_change[i] > 0 if bw_change[i] != 0 else False
            if touched_upper and (expanding or i > len(candles) * 0.5):
                signals[i] = Signal.SELL
        return signals


# ============================================================
# 策略注册表 — 新策略在此一行注册，自动接入回测和实盘
# ============================================================

# ============================================================
# KDJ 随机指标策略（FormulaStrategy 包装）
# ============================================================

class KDJStrategy(Strategy):
    """
    KDJ 随机指标策略

    规则：
      - K 线从下穿越 D 线且 J < 20（超卖区金叉）→ 买入
      - K 线从上穿越 D 线且 J > 80（超买区死叉）→ 卖出

    参数：
      - k_period: K 值周期（默认 9）
      - d_period: D 值平滑周期（默认 3）
      - j_period: J 值周期（默认 3）
    """
    def __init__(self, config=None, k_period=9, d_period=3, j_period=3):
        super().__init__(config)
        self.k_period = k_period
        self.d_period = d_period
        self.j_period = j_period

    def _compute_kdj(self, candles):
        n = len(candles)
        k_vals = [50.0] * n
        d_vals = [50.0] * n
        j_vals = [50.0] * n
        _k, _d = 50.0, 50.0

        for i in range(self.k_period, n):
            highest = max(c["high"] for c in candles[i - self.k_period + 1:i + 1])
            lowest = min(c["low"] for c in candles[i - self.k_period + 1:i + 1])
            rsv = (candles[i]["close"] - lowest) / (highest - lowest) * 100 if highest != lowest else 50.0
            _k = (_k * (self.d_period - 1) + rsv) / self.d_period
            _d = (_d * (self.d_period - 1) + _k) / self.d_period
            _j = 3 * _k - 2 * _d
            k_vals[i] = _k
            d_vals[i] = _d
            j_vals[i] = _j

        return k_vals, d_vals, j_vals

    def populate_indicators(self, candles):
        k, d, j = self._compute_kdj(candles)
        self._indicators = {"k": k, "d": d, "j": j, "close": [c["close"] for c in candles]}
        return self._indicators

    def populate_entry_trend(self, candles):
        k = self._indicators.get("k", [])
        d = self._indicators.get("d", [])
        j = self._indicators.get("j", [])
        if not k:
            self.populate_indicators(candles)
            k, d, j = self._indicators["k"], self._indicators["d"], self._indicators["j"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            if k[i] > d[i] and k[i - 1] <= d[i - 1] and j[i] < 20:
                signals[i] = Signal.BUY
        return signals

    def populate_exit_trend(self, candles):
        k = self._indicators.get("k", [])
        d = self._indicators.get("d", [])
        j = self._indicators.get("j", [])
        if not k:
            self.populate_indicators(candles)
            k, d, j = self._indicators["k"], self._indicators["d"], self._indicators["j"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            if k[i] < d[i] and k[i - 1] >= d[i - 1] and j[i] > 80:
                signals[i] = Signal.SELL
        return signals


# ============================================================
# ATR 动态止损策略（均线趋势 + ATR 止损）
# ============================================================

class ATRStopStrategy(Strategy):
    """
    ATR 动态止损策略

    规则：
      - 价格站上 EMA 且 ATR 扩大（波动突破）→ 买入
      - 价格跌破 EMA 或触发 ATR 止损 → 卖出
      - 止损使用 2×ATR 动态计算

    参数：
      - ema_period: EMA 周期（默认 20）
      - atr_period: ATR 周期（默认 14）
      - atr_multiplier: ATR 止损倍数（默认 2.0）
    """
    def __init__(self, config=None, ema_period=20, atr_period=14, atr_multiplier=2.0):
        super().__init__(config)
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier

    def populate_indicators(self, candles):
        closes = [c["close"] for c in candles]
        ema = self.EMA(closes, self.ema_period)
        atr = compute_atr(candles, self.atr_period)
        self._indicators = {"ema": ema, "atr": atr, "close": closes}
        return self._indicators

    def populate_entry_trend(self, candles):
        ema = self._indicators.get("ema", [])
        atr = self._indicators.get("atr", [])
        closes = self._indicators.get("close", [])
        if not ema:
            self.populate_indicators(candles)
            ema, atr, closes = self._indicators["ema"], self._indicators["atr"], self._indicators["close"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(2, len(candles)):
            if ema[i] == 0 or ema[i - 1] == 0:
                continue
            price_cross_up = closes[i] > ema[i] and closes[i - 1] <= ema[i - 1]
            atr_expanding = atr[i] > atr[i - 1]
            if price_cross_up and atr_expanding:
                signals[i] = Signal.BUY
        return signals

    def populate_exit_trend(self, candles):
        ema = self._indicators.get("ema", [])
        closes = self._indicators.get("close", [])
        if not ema:
            self.populate_indicators(candles)
            ema, closes = self._indicators["ema"], self._indicators["close"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            if ema[i] == 0:
                continue
            if closes[i] < ema[i] and closes[i - 1] >= ema[i - 1]:
                signals[i] = Signal.SELL
        return signals


# ============================================================
# 策略注册表 — 新策略在此一行注册，自动接入回测和实盘
# （完整注册表在文件末尾，见下方 STRATEGY_REGISTRY）
# ============================================================


# ============================================================
# 策略N+1：CoinGlass 情绪驱动 + 清算集群策略
# 数据来源：CoinGlass（优先）/ Gate.io ccxt（降级）
# 架构：中书省信号生成层专用策略
# ============================================================

class CoinGlassSentimentStrategy(Strategy):
    """
    CoinGlass 情绪驱动 + 清算集群顺势策略

    核心逻辑：
    入场信号 = 资金费率情绪(35%) + 多空比(25%) + 清算集群(25%) + 价格结构(15%)

    数据源优先级：
      1. CoinGlass Open API（资金费率、多空比、ETF、期权数据）
      2. Gate.io ccxt（价格数据、技术指标）

    规则：
      · 综合打分 ≥ 65  → 买入信号
      · 综合打分 ≤ 35  → 卖出信号
      · 资金费率极端（|rate| > 0.1%） → 强制反向信号
      · ATR突破确认趋势方向

    参数：
      · funding_weight     资金费率权重（默认 0.35）
      · ls_weight         多空比权重（默认 0.25）
      · liq_weight        清算集群权重（默认 0.25）
      · structure_weight  价格结构权重（默认 0.15）
      · score_buy_thresh  买入阈值（默认 65）
      · score_sell_thresh 卖出阈值（默认 35）
      · extreme_rate      极端资金费率阈值（默认 0.001，即0.1%）
      · atr_period        ATR周期（默认 14）
      · ema_fast/fast    EMA快慢线周期
      · rsi_period        RSI周期（默认 14）
    """

    def __init__(self, config=None,
                 funding_weight: float = 0.35,
                 ls_weight: float = 0.25,
                 liq_weight: float = 0.25,
                 structure_weight: float = 0.15,
                 score_buy_thresh: float = 65.0,
                 score_sell_thresh: float = 35.0,
                 extreme_rate: float = 0.001,
                 atr_period: int = 14,
                 ema_fast: int = 10,
                 ema_slow: int = 28,
                 rsi_period: int = 14):
        super().__init__(config)
        self.funding_weight = funding_weight
        self.ls_weight = ls_weight
        self.liq_weight = liq_weight
        self.structure_weight = structure_weight
        self.score_buy_thresh = score_buy_thresh
        self.score_sell_thresh = score_sell_thresh
        self.extreme_rate = extreme_rate
        self.atr_period = atr_period
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        # 外部情绪数据（由实盘引擎注入）
        self._funding_rate: float = 0.0          # CoinGlass资金费率
        self._ls_ratio: float = 1.0              # 多空比
        self._etf_flow: float = 0.0              # ETF净流入（USD）
        self._oi_change_pct: float = 0.0         # 未平仓合约变化%
        self._liq_cluster_level: float = 0.0      # 清算集群位置（0=无，+1=上方密集，-1=下方密集）
        self._liq_cluster_strength: float = 0.0  # 清算集群强度（0~1）
        self._data_source: str = "none"          # 数据来源标记

    def set_sentiment_data(self,
                           funding_rate: float = 0.0,
                           ls_ratio: float = 1.0,
                           etf_flow: float = 0.0,
                           oi_change_pct: float = 0.0,
                           liq_cluster_level: float = 0.0,
                           liq_cluster_strength: float = 0.0,
                           source: str = "coinglass"):
        """由实盘引擎或数据层注入外部情绪数据"""
        self._funding_rate = funding_rate
        self._ls_ratio = ls_ratio
        self._etf_flow = etf_flow
        self._oi_change_pct = oi_change_pct
        self._liq_cluster_level = liq_cluster_level
        self._liq_cluster_strength = liq_cluster_strength
        self._data_source = source

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [float(c["close"]) for c in candles]
        highs  = [float(c["high"]) for c in candles]
        lows   = [float(c["low"])  for c in candles]
        vols   = [float(c.get("volume", 0)) for c in candles]

        ema_fast_val = self.EMA(closes, self.ema_fast)
        ema_slow_val = self.EMA(closes, self.ema_slow)
        rsi_val = self.RSI(closes, self.rsi_period)
        atr_val = compute_atr(candles, self.atr_period)

        # ATR历史均值（用于判断当前ATR是否扩张）
        atr_mean = sum(atr_val[-self.atr_period:]) / self.atr_period if len(atr_val) >= self.atr_period else atr_val[-1] if atr_val else 0.0
        atr_ratio = atr_val[-1] / atr_mean if atr_mean > 0 else 1.0

        # 价格动量（5日涨跌幅）
        momentum = (closes[-1] / closes[-6] - 1) if len(closes) > 5 else 0.0

        self._indicators = {
            "close":        closes,
            "ema_fast":     ema_fast_val,
            "ema_slow":     ema_slow_val,
            "rsi":          rsi_val,
            "atr":          atr_val,
            "atr_ratio":    atr_ratio,
            "momentum":     momentum,
            "volume":       vols,
        }
        return self._indicators

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        if not self._indicators:
            self.populate_indicators(candles)

        closes   = self._indicators["close"]
        ema_fast = self._indicators["ema_fast"]
        ema_slow = self._indicators["ema_slow"]
        rsi      = self._indicators["rsi"]
        atr_ratio = self._indicators["atr_ratio"]
        momentum  = self._indicators["momentum"]

        signals = [Signal.HOLD] * len(candles)
        scores  = self._compute_composite_score(len(candles) - 1)

        for i in range(3, len(candles)):
            score = self._compute_composite_score(i)

            # ① 极端资金费率 → 强制反向（优先判断）
            if abs(self._funding_rate) > self.extreme_rate:
                if self._funding_rate > self.extreme_rate:
                    # 资金费率极高（年化>90%），强制做空
                    if score < self.score_sell_thresh:
                        signals[i] = Signal.SELL
                else:
                    # 资金费率极低（年化<-90%），强制做多
                    if score > self.score_buy_thresh:
                        signals[i] = Signal.BUY
                continue

            # ② 常规打分区间
            if score >= self.score_buy_thresh:
                # 多头信号：需EMA多头排列确认
                if ema_fast[i] > ema_slow[i]:
                    signals[i] = Signal.BUY
            elif score <= self.score_sell_thresh:
                # 空头信号：需EMA空头排列确认
                if ema_fast[i] < ema_slow[i]:
                    signals[i] = Signal.SELL

        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        if not self._indicators:
            self.populate_indicators(candles)

        closes    = self._indicators["close"]
        ema_fast  = self._indicators["ema_fast"]
        ema_slow  = self._indicators["ema_slow"]
        rsi       = self._indicators["rsi"]

        signals = [Signal.HOLD] * len(candles)
        for i in range(1, len(candles)):
            # 止损：RSI超买+EMA死叉
            if rsi[i] > 70 and ema_fast[i] < ema_slow[i] and ema_fast[i-1] >= ema_slow[i-1]:
                signals[i] = Signal.SELL
            # 止盈：RSI超卖+EMA金叉
            elif rsi[i] < 30 and ema_fast[i] > ema_slow[i] and ema_fast[i-1] <= ema_slow[i-1]:
                signals[i] = Signal.SELL
            # 追踪止损：价格跌破EMA慢线
            elif ema_slow[i] > 0 and closes[i] < ema_slow[i] and closes[i-1] >= ema_slow[i-1]:
                signals[i] = Signal.SELL
        return signals

    def _compute_composite_score(self, index: int) -> float:
        """
        计算综合打分（0~100）

        资金费率维度（35%）：
          · rate > 0.05%  → 极度多头 → 0分（反向做空情绪）
          · rate < -0.03% → 极度空头 → 100分（反向做多情绪）
          · rate = 0%      → 50分

        多空比维度（25%）：
          · ratio < 0.9   → 机构偏空，散户偏多 → 75分（做多机会）
          · ratio > 1.1   → 机构偏多，散户偏空 → 25分（做空机会）
          · ratio = 1.0   → 50分

        清算集群维度（25%）：
          · cluster_level > 0（上方密集）→ 卖压重 → 偏低分（对多头不利）
          · cluster_level < 0（下方密集）→ 买压重 → 偏高分（对空头不利）
          · cluster_strength越大，分数越极端

        价格结构维度（15%）：
          · EMA多头排列 → +15分
          · ATR扩张（突破）→ +10分（封顶15）
          · RSI超卖 → +10分（封顶15）
        """
        score = 50.0  # 基准分

        # ── ① 资金费率打分（35%权重）─────────────────────────
        funding_score = 50.0
        fr = self._funding_rate
        if fr > 0.0005:       # > 0.05%，年化45%+，极度看多
            funding_score = max(0, 50 - (fr - 0.0005) * 50000)
        elif fr < -0.0003:    # < -0.03%，年化-27%，极度看空
            funding_score = min(100, 50 + abs(fr + 0.0003) * 50000)
        # funding_score 越高 → 对做多有利的情绪环境
        funding_contrib = funding_score * self.funding_weight  # 0~35

        # ── ② 多空比打分（25%权重）──────────────────────────
        ls_score = 50.0
        ls = self._ls_ratio
        if ls < 0.9:         # 机构偏空 → 散户偏多 → 做多机会
            ls_score = min(100, 75 + (0.9 - ls) * 100)
        elif ls > 1.1:       # 机构偏多 → 散户偏空 → 做空机会
            ls_score = max(0, 25 - (ls - 1.1) * 100)
        ls_contrib = ls_score * self.ls_weight  # 0~25

        # ── ③ 清算集群打分（25%权重）─────────────────────────
        # cluster_level: +1=上方空头密集（压力），-1=下方多头密集（支撑）
        # 下方密集（支撑强）→ 有利于做多 → 高分
        # 上方密集（压力重）→ 有利于做空 → 低分
        liq_base = 50.0
        liq_contrib_raw = -self._liq_cluster_level * self._liq_cluster_strength * 50  # -50~+50
        liq_contrib = (liq_base + liq_contrib_raw) * self.liq_weight  # 约12.5~37.5

        # ── ④ 价格结构打分（15%权重）────────────────────────
        ema_fast = self._indicators.get("ema_fast", [])
        ema_slow = self._indicators.get("ema_slow", [])
        rsi_arr  = self._indicators.get("rsi", [])
        atr_ratio = self._indicators.get("atr_ratio", [1.0])

        structure_score = 0.0
        if index < len(ema_fast) and index < len(ema_slow):
            if ema_fast[index] > ema_slow[index]:  # 多头排列
                structure_score += 7.5
            else:
                structure_score -= 7.5  # 空头排列

        if index < len(rsi_arr):
            if rsi_arr[index] < 35:               # 超卖
                structure_score += 5.0
            elif rsi_arr[index] > 65:             # 超买
                structure_score -= 5.0

        if atr_ratio > 1.3:  # ATR扩张，趋势加速
            structure_score += 2.5

        structure_contrib = (50 + structure_score) * self.structure_weight  # 约35~65区间

        total = funding_contrib + ls_contrib + liq_contrib + structure_contrib
        return max(0.0, min(100.0, total))

    def get_sentiment_summary(self) -> dict:
        """返回当前情绪数据快照（用于日志和Dashboard展示）"""
        return {
            "funding_rate":       self._funding_rate,
            "ls_ratio":           self._ls_ratio,
            "etf_flow_usd":       self._etf_flow,
            "oi_change_pct":      self._oi_change_pct,
            "liq_cluster_level":  self._liq_cluster_level,
            "liq_cluster_strength": self._liq_cluster_strength,
            "data_source":        self._data_source,
        }


def build_strategy(name: str, config: StrategyConfig, **kwargs) -> Strategy:
    """根据策略名称和配置构建策略实例"""
    cls = STRATEGY_REGISTRY.get(name.upper())
    if cls:
        return cls(config=config, **kwargs)
    raise ValueError(f"未知策略: {name}，可用: {list(STRATEGY_REGISTRY.keys())}")


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


# ============================================================
# 策略1：多因子趋势系统（Multi-Factor Trend Strategy）
# 主力策略 — 多因子打分，趋势过滤 + 量化评分入场
# ============================================================

class MultiFactorTrendStrategy(Strategy):
    """
    多因子趋势系统（主力策略）

    趋势过滤（必须全部满足才开仓）：
      - BTC 200小时EMA向上 + 价格 > EMA
      - BTC主导率下降或稳定
      - 资金费率正值但不过高（<0.05%）

    多因子打分（总分 > 65分才做多）：
      - 动量：过去7天收益率排名前30%（+20分）
      - 成交量：过去24h量/过去7天均量 > 1.5（+15分）
      - 链上：活跃地址/交易量上升（+15分）
      - 技术：RSI(14) < 75 且 MACD金叉（+15分）
      - 宏观：Fear & Greed Index < 70（+15分）

    仓位与风控：
      - 单币最大仓位 8%
      - 止损：-12% 或 ATR(14) × 2.5
      - 止盈：分批（+25% 减半，+50% 清仓）
      - Trailing Stop：盈利后跟进10-15%

    参数：
      - min_score:      最低多因子打分阈值（默认 65）
      - ema_period:     EMA周期（默认 200）
      - atr_period:     ATR周期（默认 14）
      - atr_multiplier: ATR止损倍数（默认 2.5）
      - max_position_pct: 最大持仓占比（默认 0.08）
    """

    def __init__(self, config=None,
                 min_score: int = 65,
                 ema_period: int = 200,
                 atr_period: int = 14,
                 atr_multiplier: float = 2.5,
                 max_position_pct: float = 0.08,
                 trailing_pct: float = 0.10):
        super().__init__(config)
        self.min_score = min_score
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_position_pct = max_position_pct
        self.trailing_pct = trailing_pct

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [c["close"] for c in candles]
        highs = [c.get("high", c["close"]) for c in candles]
        lows  = [c.get("low",  c["close"]) for c in candles]
        volumes = [c.get("volume", 0) for c in candles]

        # 趋势指标：EMA
        ema200 = self.EMA(closes, self.ema_period)

        # 动量指标：7天收益率
        returns_7d = [0.0] * 7 + [
            (closes[i] - closes[i-7]) / closes[i-7] * 100 if i >= 7 and closes[i-7] != 0 else 0.0
            for i in range(7, len(closes))
        ]

        # 成交量指标：24h量 / 7天均量
        avg_vol_7d = [0.0] * 7 + [
            sum(volumes[i-7:i]) / 7 for i in range(7, len(volumes))
        ]
        vol_ratio = [
            volumes[i] / avg_vol_7d[i] if avg_vol_7d[i] > 0 else 0.0
            for i in range(len(closes))
        ]

        # RSI
        rsi = self.RSI(closes, 14)

        # MACD（标准参数 12/26/9）
        ema_fast = self.EMA(closes, 12)
        ema_slow = self.EMA(closes, 26)
        macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
        signal_line = self._ema_of_list(macd_line, 9)
        macd_hist  = [macd_line[i] - signal_line[i] for i in range(len(closes))]

        # ATR（止损用）
        atr = compute_atr(candles, self.atr_period)

        self._indicators = {
            "ema200":    ema200,
            "returns_7d": returns_7d,
            "vol_ratio": vol_ratio,
            "rsi":       rsi,
            "macd":      macd_line,
            "signal":    signal_line,
            "macd_hist": macd_hist,
            "atr":       atr,
            "close":     closes,
            "high":      highs,
            "low":       lows,
            "volume":    volumes,
        }
        return self._indicators

    def _ema_of_list(self, values: List[float], period: int) -> List[float]:
        """对任意列表计算EMA"""
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
        ind = self._indicators
        ema200    = ind.get("ema200", [])
        returns   = ind.get("returns_7d", [])
        vol_ratio = ind.get("vol_ratio", [])
        rsi       = ind.get("rsi", [])
        macd      = ind.get("macd", [])
        signal    = ind.get("signal", [])
        macd_hist = ind.get("macd_hist", [])
        closes    = ind.get("close", [])

        if not ema200:
            self.populate_indicators(candles)
            ema200    = self._indicators["ema200"]
            returns   = self._indicators["returns_7d"]
            vol_ratio = self._indicators["vol_ratio"]
            rsi       = self._indicators["rsi"]
            macd      = self._indicators["macd"]
            signal    = self._indicators["signal"]
            macd_hist = self._indicators["macd_hist"]
            closes    = self._indicators["close"]

        signals = [Signal.HOLD] * len(candles)

        # 需要足够数据（前200根K线不产生信号）
        warmup = self.ema_period + 14 + 7

        for i in range(warmup, len(candles)):
            c = candles[i]
            # ── 趋势过滤（必须全部满足）──
            # 1. 价格 > EMA200
            price_above_ema = closes[i] > ema200[i] if ema200[i] != 0 else False
            # 2. EMA200 向上（当前值 > 前1个值）
            ema_up = ema200[i] > ema200[i-1] if ema200[i] != 0 and ema200[i-1] != 0 else False
            if not (price_above_ema and ema_up):
                continue

            # ── 多因子打分 ──
            score = 0

            # 动量：7天收益率 > 0（+20分）
            if returns[i] > 0:
                score += 20

            # 成交量：vol_ratio > 1.5（+15分）
            if vol_ratio[i] > 1.5:
                score += 15

            # 技术 RSI < 75（+8分），MACD金叉（+7分）
            if rsi[i] < 75:
                score += 8
            # MACD金叉：当前MACD > Signal且前一根MACD <= Signal
            if (macd[i] > signal[i] and macd[i-1] <= signal[i-1] and
                    macd[i] != 0 and signal[i] != 0):
                score += 7

            # 宏观 Fear & Greed Index（由外部注入 self.fear_greed）
            fg = getattr(self, "fear_greed", 50)
            if fg < 70:
                score += 15

            # 链上因子（由外部注入 self.onchain_score，0~100）
            oc = getattr(self, "onchain_score", 50)
            if oc > 60:
                score += 15

            if score >= self.min_score:
                signals[i] = Signal.BUY

        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        """分批止盈 + ATR止损 + trailing stop"""
        ind = self._indicators
        closes   = ind.get("close", [])
        atr      = ind.get("atr", [])
        ema200   = ind.get("ema200", [])
        rsi      = ind.get("rsi", [])
        macd     = ind.get("macd", [])
        signal   = ind.get("signal", [])

        if not closes:
            return [Signal.HOLD] * len(candles)

        signals = [Signal.HOLD] * len(candles)

        for i in range(1, len(candles)):
            # ATR止损：价格跌破 EMA200 且 ATR扩大
            if ema200[i] != 0 and closes[i] < ema200[i] and closes[i-1] >= ema200[i-1]:
                signals[i] = Signal.SELL
                continue
            # MACD死叉
            if (macd[i] < signal[i] and macd[i-1] >= signal[i-1] and
                    macd[i] != 0 and signal[i] != 0):
                signals[i] = Signal.SELL
                continue
            # RSI 超买
            if rsi[i] > 80 and rsi[i-1] <= 80:
                signals[i] = Signal.SELL

        return signals

    def get_atr_stop_loss(self, entry_price: float, candles: List[Dict], is_long: bool = True) -> float:
        """ATR动态止损价"""
        atr = compute_atr(candles, self.atr_period)
        if not atr:
            return entry_price * (1 - 0.12)
        current_atr = atr[-1] if atr else 0
        if is_long:
            return entry_price - current_atr * self.atr_multiplier
        else:
            return entry_price + current_atr * self.atr_multiplier


# ============================================================
# 策略2：资金费率套利（Funding Rate Arbitrage Strategy）
# 稳定收益基石 — 正向套利吃资金费率
# ============================================================

class FundingRateArbitrageStrategy(Strategy):
    """
    资金费率套利策略（稳定收益基石）

    逻辑：
      - 做多现货 + 做空等量永续（当资金费率持续为正）
      - 做空现货 + 做多永续（资金费率为负时）

    开仓条件：
      - 资金费率持续 > 0.03%（每4-8小时检查）
      - 基差稳定或有收敛趋势

    目标：
      - 每月吃 0.8-2.5% 资金费率
      - 基差收敛额外收益

    参数：
      - min_funding_rate: 最小资金费率阈值（默认 0.0003 = 0.03%）
      - max_funding_rate: 最大资金费率阈值（默认 0.01 = 1%，避免极高费率陷阱）
      - rebalance_hours:  检查间隔（小时，默认 6）
    """

    def __init__(self, config=None,
                 min_funding_rate: float = 0.0003,
                 max_funding_rate: float = 0.01,
                 rebalance_hours: int = 6):
        super().__init__(config)
        self.min_funding_rate = min_funding_rate
        self.max_funding_rate = max_funding_rate
        self.rebalance_hours = rebalance_hours

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        closes = [c["close"] for c in candles]
        volumes = [c.get("volume", 0) for c in candles]

        self._indicators = {
            "close":  closes,
            "volume": volumes,
        }
        return self._indicators

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        """
        资金费率策略不依赖技术指标入场信号，
        而依赖外部注入的资金费率数据。
        当 funding_rate > min_funding_rate 且 < max_funding_rate 时产生买入信号。
        """
        signals = [Signal.HOLD] * len(candles)
        fr = getattr(self, "funding_rate", None)

        if fr is None:
            return signals

        # 资金费率在合理区间
        if self.min_funding_rate <= fr <= self.max_funding_rate:
            signals[-1] = Signal.BUY   # 做多现货+做空永续

        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        """
        平仓条件：
          - 资金费率转负或过高
          - 基差收敛到0附近
        """
        signals = [Signal.HOLD] * len(candles)
        fr = getattr(self, "funding_rate", None)

        if fr is None:
            return signals

        # 费率不再适合套利
        if fr < 0 or fr > self.max_funding_rate:
            signals[-1] = Signal.SELL

        return signals


# ============================================================
# 策略3：统计套利（Statistical Arbitrage — 配对交易）
# 经典配对均值回归策略
# ============================================================

class StatisticalArbitrageStrategy(Strategy):
    """
    统计套利策略（配对交易）

    逻辑：
      - 计算过去30天两个标的的价差Z-score
      - Z-score > 2：做空高估 + 做多低估
      - Z-score < -2：做多低估 + 做空高估
      - Z-score 回归0附近时平仓

    配对示例：
      - BTC vs ETH
      - SOL vs AVAX / NEAR
      - 同叙事币（两个AI币）

    参数：
      - pair_symbol:     配对标的（默认 "ETH")
                         主交易对由 config.symbol 决定，配对对由 pair_symbol 决定
                         实际配对：config.symbol vs pair_symbol
      - lookback:        Z-score 回看窗口（默认 30）
      - z_entry:          入场Z-score阈值（默认 2.0）
      - z_exit:           平仓Z-score阈值（默认 0.5）
      - z_exit_loss:      止损Z-score阈值（默认 3.5）
    """

    def __init__(self, config=None,
                 pair_symbol: str = "ETH",
                 lookback: int = 30,
                 z_entry: float = 2.0,
                 z_exit: float = 0.5,
                 z_exit_loss: float = 3.5):
        super().__init__(config)
        self.pair_symbol = pair_symbol
        self.lookback = lookback
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.z_exit_loss = z_exit_loss

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        """
        计算配对价差和Z-score。
        配对对的K线数据从 self.pair_candles 注入（外部调用者负责提供）。
        """
        closes = [c["close"] for c in candles]

        # 主交易对自身收益率
        returns_main = [0.0] + [
            (closes[i] - closes[i-1]) / closes[i-1] if closes[i-1] != 0 else 0.0
            for i in range(1, len(closes))
        ]

        # 配对收益率（由外部注入 self.pair_returns）
        pair_returns = getattr(self, "pair_returns", [])

        # 计算价差（spread = main_return - pair_return）
        spread = []
        for i in range(len(closes)):
            pr = pair_returns[i] if i < len(pair_returns) else 0.0
            mr = returns_main[i] if i < len(returns_main) else 0.0
            spread.append(mr - pr)

        # 计算Z-score
        zscore = self._compute_zscore(spread, self.lookback)

        self._indicators = {
            "close":   closes,
            "spread":   spread,
            "zscore":  zscore,
            "returns":  returns_main,
        }
        return self._indicators

    def _compute_zscore(self, values: List[float], lookback: int) -> List[float]:
        """计算滚动Z-score"""
        result = [0.0] * len(values)
        for i in range(lookback, len(values)):
            window = values[i-lookback:i]
            mean = sum(window) / lookback
            variance = sum((v - mean) ** 2 for v in window) / lookback
            std = variance ** 0.5
            if std > 0:
                result[i] = (values[i] - mean) / std
        return result

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        """
        Z-score > z_entry → 做空高估（做空主交易对，做多配对对）
        Z-score < -z_entry → 做多低估（做多主交易对，做空配对对）
        """
        ind = self._indicators
        zscore = ind.get("zscore", [])
        if not zscore:
            self.populate_indicators(candles)
            zscore = self._indicators["zscore"]

        signals = [Signal.HOLD] * len(candles)
        warmup = self.lookback + 1

        for i in range(warmup, len(candles)):
            z = zscore[i]
            if z > self.z_entry:
                # 做空高估标的（主交易对）
                signals[i] = Signal.SELL
            elif z < -self.z_entry:
                # 做多低估标的（主交易对）
                signals[i] = Signal.BUY

        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        """
        Z-score 回归 |z_exit| 以内 → 平仓
        Z-score 超过 |z_exit_loss| → 止损
        """
        ind = self._indicators
        zscore = ind.get("zscore", [])
        if not zscore:
            return [Signal.HOLD] * len(candles)

        signals = [Signal.HOLD] * len(candles)
        warmup = self.lookback + 1

        for i in range(warmup, len(candles)):
            z = zscore[i]
            # 止损（极端情况）
            if abs(z) > self.z_exit_loss:
                signals[i] = Signal.SELL
            # 回归均值平仓
            elif abs(z) < self.z_exit:
                signals[i] = Signal.SELL

        return signals


# ============================================================
# 策略注册表更新
# ============================================================

STRATEGY_REGISTRY: Dict[str, type] = {
    "RSI":        RSIStrategy,
    "SMA":        SMAcrossStrategy,
    "MACD":       MACDStrategy,
    "BOLLINGER":  BollingerBandsStrategy,
    "KDJ":        KDJStrategy,
    "ATRSTOP":    ATRStopStrategy,
    "MULTIFACTOR": MultiFactorTrendStrategy,
    "FUNDING_ARB": FundingRateArbitrageStrategy,
    "STAT_ARB":   StatisticalArbitrageStrategy,
    "COINGLASS":  CoinGlassSentimentStrategy,   # 情绪+清算集群策略（2026-05-18）
}

