"""
SignalEngine — 三省六部: 中书省（信号生成）

职责：
  - 获取 K线数据（抽象接口，可替换数据源）
  - 计算技术指标（RSI/SMA/MACD/BOLLINGER/FORMULA/VOTE）
  - AI 信号过滤
  - 暴露 signal_only() 同步方法给 TradingAgent 调用
"""

from __future__ import annotations

import os
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0


# ─── 通达信内置公式 ──────────────────────────────────────────

BUILTIN_FORMULAS: Dict[str, str] = {
    "MACD": 'DIF:=EMA(CLOSE,12)-EMA(CLOSE,26);\nDEA:=EMA(DIF,9);\nMACD:=2*(DIF-DEA);\n买入:=CROSS(DIF,DEA);\n卖出:=CROSS(DEA,DIF);\n买入,SELL;\n卖出,SKY;',
    "KDJ": 'RSV:=(CLOSE-LLV(LOW,N))/(HHV(HIGH,N)-LLV(LOW,N))*100;\nK:=SMA(RSV,3,1);\nD:=SMA(K,3,1);\nJ:=3*K-2*D;\n买入:=CROSS(J,D) AND J<20;\n卖出:=CROSS(D,J) AND J>80;\n买入,SELL;\n卖出,SKY;',
    "RSI": 'LC:=REF(CLOSE,1);\nRSI1:=SMA(MAX(CLOSE-LC,0),N,1)/SMA(ABS(CLOSE-LC),N,1)*100;\n买入:=RSI1<20;\n卖出:=RSI1>80;\n买入,SELL;\n卖出,SKY;',
}

# ─── RSI 计算 ────────────────────────────────────────────────

def compute_rsi(closes: List[float], period: int = 14) -> List[float]:
    if len(closes) < period + 1:
        return []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [-min(d, 0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result: List[float] = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / (avg_loss + 1e-10)
        result.append(100 - 100 / (1 + rs))
    return result


# ─── 策略协议 ──────────────────────────────────────────────

@dataclass
class StrategyConfig:
    symbol: str
    timeframe: str


class BaseStrategy:
    """策略基类"""
    def __init__(self, config: StrategyConfig):
        self.config = config

    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        """返回 (signal_val, value1, value2)"""
        raise NotImplementedError


class RSIStrategy(BaseStrategy):
    def __init__(self, config: StrategyConfig, rsi_period: int = 14,
                 oversold: float = 30.0, overbought: float = 70.0):
        super().__init__(config)
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        closes = [c["close"] for c in candles]
        if len(closes) < self.rsi_period + 1:
            return 0, 0.0, 0.0
        rsi_vals = compute_rsi(closes, self.rsi_period)
        if not rsi_vals:
            return 0, 0.0, 0.0
        rsi = rsi_vals[-1]
        signal = Signal.HOLD.value
        if rsi < self.oversold:
            signal = Signal.BUY.value
        elif rsi > self.overbought:
            signal = Signal.SELL.value
        return signal, rsi, 0.0


class SMAcrossStrategy(BaseStrategy):
    def __init__(self, config: StrategyConfig, fast_period: int = 10, slow_period: int = 30):
        super().__init__(config)
        self.fast_period = fast_period
        self.slow_period = slow_period

    def _sma(self, closes: List[float], n: int) -> float:
        return sum(closes[-n:]) / n if len(closes) >= n else 0.0

    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        closes = [c["close"] for c in candles]
        if len(closes) < self.slow_period:
            return 0, 0.0, 0.0
        fast = self._sma(closes, self.fast_period)
        slow = self._sma(closes, self.slow_period)
        # 简单的最近两根SMA比较
        prev_fast = sum(closes[-(self.fast_period + 1):-1]) / self.fast_period
        prev_slow = sum(closes[-(self.slow_period + 1):-1]) / self.slow_period
        signal = Signal.HOLD.value
        if prev_fast <= prev_slow and fast > slow:
            signal = Signal.BUY.value
        elif prev_fast >= prev_slow and fast < slow:
            signal = Signal.SELL.value
        return signal, fast, slow


class MACDStrategy(BaseStrategy):
    def __init__(self, config: StrategyConfig, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__(config)
        self.fast, self.slow, self.signal = fast, slow, signal

    def _ema(self, closes: List[float], n: int) -> float:
        if len(closes) < n:
            return 0.0
        k = 2 / (n + 1)
        ema = sum(closes[:n]) / n
        for price in closes[n:]:
            ema = price * k + ema * (1 - k)
        return ema

    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        closes = [c["close"] for c in candles]
        if len(closes) < self.slow + 1:
            return 0, 0.0, 0.0
        dif = self._ema(closes, self.fast) - self._ema(closes, self.slow)
        # signal EMA 用简化版本
        dea = dif * 0.9  # placeholder
        signal = Signal.HOLD.value
        if dif > dea:
            signal = Signal.BUY.value
        elif dif < dea:
            signal = Signal.SELL.value
        return signal, dif, dea


class BollingerBandsStrategy(BaseStrategy):
    def __init__(self, config: StrategyConfig, period: int = 20, std_dev: float = 2.0):
        super().__init__(config)
        self.period = period
        self.std_dev = std_dev

    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        closes = [c["close"] for c in candles]
        if len(closes) < self.period:
            return 0, 0.0, 0.0
        window = closes[-self.period:]
        ma = sum(window) / self.period
        variance = sum((x - ma) ** 2 for x in window) / self.period
        std = variance ** 0.5
        upper = ma + self.std_dev * std
        lower = ma - self.std_dev * std
        price = closes[-1]
        signal = Signal.HOLD.value
        if price < lower:
            signal = Signal.BUY.value
        elif price > upper:
            signal = Signal.SELL.value
        return signal, upper, lower


class MultiStrategyVote(BaseStrategy):
    """多策略加权投票"""
    def __init__(self, strategies: List[Tuple[BaseStrategy, float]], threshold: float = 0.3, name: str = ""):
        super().__init__(strategies[0][0].config if strategies else StrategyConfig("", ""))
        self.strategies = strategies
        self.threshold = threshold
        self.name = name

    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        votes = {Signal.BUY.value: 0.0, Signal.SELL.value: 0.0}
        for strat, weight in self.strategies:
            sig, _, _ = strat.compute(candles)
            if sig == Signal.BUY.value:
                votes[Signal.BUY.value] += weight
            elif sig == Signal.SELL.value:
                votes[Signal.SELL.value] += weight
        total = sum(w for _, w in self.strategies)
        buy_ratio = votes[Signal.BUY.value] / total if total > 0 else 0.0
        sell_ratio = votes[Signal.SELL.value] / total if total > 0 else 0.0
        if buy_ratio >= self.threshold:
            return Signal.BUY.value, buy_ratio, sell_ratio
        elif sell_ratio >= self.threshold:
            return Signal.SELL.value, buy_ratio, sell_ratio
        return Signal.HOLD.value, buy_ratio, sell_ratio


# ─── 通达信公式解析策略（基础实现）─────────────────────────────

class FormulaStrategy(BaseStrategy):
    def __init__(self, config: StrategyConfig, formula: str, symbol: str, timeframe: str,
                 stop_loss: float = 0.02, take_profit: float = 0.04):
        super().__init__(config)
        self.formula = formula
        self.symbol = symbol
        self.timeframe = timeframe
        self.stop_loss = stop_loss
        self.take_profit = take_profit

    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        # 基础占位实现：仅用简单价格动量
        closes = [c["close"] for c in candles]
        if len(closes) < 5:
            return 0, 0.0, 0.0
        mom = (closes[-1] - closes[-5]) / closes[-5] * 100
        signal = Signal.HOLD.value
        if mom < -3:
            signal = Signal.BUY.value
        elif mom > 5:
            signal = Signal.SELL.value
        return signal, mom, 0.0


# ─── AI 信号过滤器 ───────────────────────────────────────────

class AIModel(Enum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    MINIMAX = "minimax"


class AISignalFilter:
    def __init__(self, model: AIModel = AIModel.DEEPSEEK):
        self.model = model
        self._client = None
        self._init_client()

    def _init_client(self):
        try:
            if self.model == AIModel.DEEPSEEK:
                import openai
                self._client = openai.OpenAI(
                    api_key=os.getenv("DEEPSEEK_API_KEY"),
                    base_url="https://api.deepseek.com"
                )
            elif self.model == AIModel.OPENAI:
                import openai
                self._client = openai.OpenAI()
            logger.info(f"AI 过滤器初始化成功: {self.model.value}")
        except Exception as e:
            logger.warning(f"AI 过滤器初始化失败: {e}")
            self._client = None

    def filter(self, signal: int, price: float, rsi: float,
               price_change_24h_pct: float, volume_24h: float) -> Tuple[int, str]:
        """返回 (filtered_signal, ai_verdict_str)"""
        if not self._client:
            return signal, "AI_CLIENT_UNAVAILABLE"

        direction = "买入" if signal == Signal.BUY.value else "卖出"
        prompt = (
            f"当前标的信号: {direction}，价格=${price}，"
            f"RSI={rsi:.1f}，24h涨跌={price_change_24h_pct:.2f}%，24h成交量=${volume_24h:.0f}。"
            f"判断是否应该执行此交易（回答 是 或 否 并简短说明）。"
        )
        try:
            response = self._client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=60,
                temperature=0.2,
            )
            verdict = response.choices[0].message.content.strip()
            if "否" in verdict or "不" in verdict:
                return Signal.HOLD.value, verdict
            return signal, verdict
        except Exception as e:
            logger.warning(f"AI 过滤器调用失败: {e}")
            return signal, f"AI_ERROR: {e}"


# ─── SignalEngine ────────────────────────────────────────────

class SignalEngine:
    """
    负责：K线获取 → 策略计算 → AI 过滤
    不持有仓位状态，是纯函数式组件。
    """

    def __init__(
        self,
        agent_id: str,
        symbol: str,
        strategy_name: str,
        exchange: str,
        timeframe: str = "4h",
        rsi_period: int = 8,
        oversold: float = 22.0,
        overbought: float = 75.0,
        formula: Optional[str] = None,
    ):
        self.agent_id = agent_id
        self.symbol = symbol
        self.exchange = exchange
        self.timeframe = timeframe
        self.strategy_name = strategy_name
        self.formula = formula

        # AI 过滤器
        AI_SIGNAL_FILTER_ENABLED = os.getenv("AI_SIGNAL_FILTER_ENABLED", "true").lower() == "true"
        AI_MODEL = os.getenv("AI_MODEL", "deepseek")
        self.ai_filter: Optional[AISignalFilter] = None
        if AI_SIGNAL_FILTER_ENABLED and AI_MODEL:
            try:
                model_map = {"deepseek": AIModel.DEEPSEEK, "openai": AIModel.OPENAI, "minimax": AIModel.MINIMAX}
                self.ai_filter = AISignalFilter(model=model_map.get(AI_MODEL.lower(), AIModel.DEEPSEEK))
            except Exception as e:
                logger.warning(f"[{agent_id}] AI 过滤器初始化失败: {e}")

        # 策略实例
        self.strategy_obj = self._build_strategy()

    def _build_strategy(self):
        config = StrategyConfig(symbol=self.symbol, timeframe=self.timeframe)
        s = self.strategy_name
        if s == "RSI":
            return RSIStrategy(config=config, rsi_period=self.rsi_period,
                               oversold=self.oversold, overbought=self.overbought)
        elif s == "SMA":
            return SMAcrossStrategy(config=config)
        elif s == "MACD":
            return MACDStrategy(config=config)
        elif s == "BOLLINGER":
            return BollingerBandsStrategy(config=config)
        elif s == "VOTE":
            rsi_strat = RSIStrategy(config=config, rsi_period=self.rsi_period,
                                    oversold=self.oversold, overbought=self.overbought)
            macd_strat = MACDStrategy(config=config)
            boll_strat = BollingerBandsStrategy(config=config)
            return MultiStrategyVote(
                strategies=[(rsi_strat, 0.4), (macd_strat, 0.3), (boll_strat, 0.3)],
                threshold=0.3, name="RSI+MACD+BOLL")
        elif s == "FORMULA":
            formula_str = self.formula or BUILTIN_FORMULAS.get('KDJ', BUILTIN_FORMULAS['MACD'])
            return FormulaStrategy(config=config, formula=formula_str,
                                   symbol=self.symbol, timeframe=self.timeframe)
        else:
            return RSIStrategy(config=config, rsi_period=self.rsi_period,
                              oversold=self.oversold, overbought=self.overbought)

    def _fetch_candles(self, limit: int = 50) -> Optional[List[Dict]]:
        """获取K线，兼容不同数据源"""
        try:
            from crypto_api import get_ohlcv
            return get_ohlcv(self.symbol, timeframe=self.timeframe, limit=limit)
        except Exception as e:
            logger.warning(f"[{self.agent_id}] 获取K线失败: {e}")
            return None

    def _fetch_price(self) -> Optional[float]:
        """获取当前价格"""
        try:
            from crypto_api import get_crypto_price
            data = get_crypto_price(self.symbol.split("/")[0])
            if isinstance(data, dict):
                return data.get("price")
            return data
        except Exception:
            return None

    def get_rsi(self, candles: List[Dict]) -> float:
        closes = [c["close"] for c in candles]
        vals = compute_rsi(closes, self.rsi_period)
        return vals[-1] if vals else 50.0

    def compute_signal(self, candles: List[Dict]) -> Tuple[int, float, float]:
        """同步计算：返回 (signal_val, value1, value2)"""
        return self.strategy_obj.compute(candles)

    def apply_ai_filter(
        self, signal: int, price: float, rsi: float,
        price_change_24h_pct: float, volume_24h: float
    ) -> Tuple[int, str]:
        """AI 过滤"""
        if not self.ai_filter or signal == Signal.HOLD.value:
            return signal, ""
        return self.ai_filter.filter(signal, price, rsi, price_change_24h_pct, volume_24h)

    def signal_only(self) -> Dict:
        """
        完整信号提取（同步，供协调者调用）：
        1. 获取K线
        2. 计算RSI
        3. 计算策略信号
        4. 可选AI过滤
        返回 {signal, rsi, price, ai_verdict, candles}
        """
        candles = self._fetch_candles(limit=50)
        if not candles:
            return {"signal": Signal.HOLD.value, "rsi": None, "price": None,
                    "ai_verdict": "", "candles": None}

        closes = [c["close"] for c in candles]
        price = closes[-1]
        rsi = self.get_rsi(candles)

        sig_val, _, _ = self.compute_signal(candles)
        ai_verdict = ""

        if sig_val != Signal.HOLD.value and self.ai_filter:
            price_change_24h_pct = 0.0
            volume_24h = 0.0
            try:
                from crypto_api import get_crypto_price
                ticker = get_crypto_price(self.symbol.split("/")[0])
                if isinstance(ticker, dict):
                    price_change_24h_pct = ticker.get("change_24h_pct", 0.0)
                    volume_24h = ticker.get("volume_24h", 0.0)
            except Exception:
                pass
            sig_val, ai_verdict = self.apply_ai_filter(
                sig_val, price, rsi, price_change_24h_pct, volume_24h)

        return {
            "signal": sig_val,
            "rsi": rsi,
            "price": price,
            "ai_verdict": ai_verdict,
            "candles": candles,
        }
