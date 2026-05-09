"""
实盘模拟引擎 v2 — VergeX AI 多 Agent 架构
整合了：
  - AI 信号过滤层（DeepSeek/OpenAI 宏观情绪验证）
  - 多 Agent 并行管理（多标的独立策略引擎）
  - Hyperliquid 链上 DEX 支持
  - Trade-only API 安全验证
  - 飞书推送信号和持仓状态

使用方式：
  python live_trading.py --check              # 执行一次信号检查
  python live_trading.py --status             # 显示所有 Agent 状态
  python live_trading.py --multi              # 多 Agent 并行模式
  python live_trading.py --validate-key       # 验证 API Key 权限
"""

import os
import sys
import json
import time
import math
import sqlite3
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Tuple
from pathlib import Path

# 加载项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    AI_MODEL, AI_SIGNAL_FILTER_ENABLED,
    MULTI_AGENT_ENABLED, AGENT_CHECK_INTERVAL, AGENT_SYMBOLS,
    VALIDATE_TRADE_ONLY, USE_HYPERLIQUID,
    HYPERLIQUID_WALLET_ADDRESS,
    CRYPTO_EXCHANGE, CRYPTO_API_KEY, CRYPTO_API_SECRET,
    LIVE_TRADING_ENABLED, LIVE_EXCHANGE, LIVE_API_KEY, LIVE_API_SECRET,
    LIVE_TESTNET, LIVE_INITIAL_CAPITAL,
    RISK_MAX_DAILY_LOSS_PCT, RISK_MAX_DAILY_LOSS_LOCK,
    RISK_MAX_TOTAL_EXPOSURE, RISK_MAX_POSITION_PER_SYMBOL,
    RISK_MAX_DAILY_TRADES, RISK_MAX_HOLDING_HOURS,
    STRATEGY_RSI_PERIOD, STRATEGY_RSI_OVERSOLD, STRATEGY_RSI_OVERBOUGHT,
    STRATEGY_STOP_LOSS, STRATEGY_TAKE_PROFIT,
    OPTIMAL_PARAMS,
)
from crypto_api import (
    get_crypto_price, get_ohlcv,
    validate_trade_only_key, set_hyperliquid_wallet,
    get_hyperliquid_price, get_hyperliquid_candles,
)
from strategies import (
    Signal, AISignalFilter, AIModel, MarketContext,
    StrategyConfig, build_strategy, STRATEGY_REGISTRY,
    compute_rsi,
)
from multi_strategy_vote import MultiStrategyVote

# 通达信公式支持
from tdx_compiler import FormulaStrategy, BUILTIN_FORMULAS

# 三省六部架构（2026-05-02 新增）
try:
    from menxia_sheng import MenxiaSheng, RiskLevel as MXRiskLevel
    _MENXIA_AVAILABLE = True
except ImportError:
    MenxiaSheng = None
    _MENXIA_AVAILABLE = False

try:
    from shangshu_sheng import ShangshuSheng
    _SHANGSHU_AVAILABLE = True
except ImportError:
    ShangshuSheng = None

try:
    from trade_history import TradeHistory, get_history
    _TRADE_HISTORY_AVAILABLE = True
except ImportError:
    TradeHistory = None
    get_history = None
    _TRADE_HISTORY_AVAILABLE = False

# 多信号候选路由（Agent-S Best-of-N 借鉴）
try:
    from components.signal_router import SignalRouter, CandidateSignal
    _SIGNAL_ROUTER_AVAILABLE = True
except ImportError:
    SignalRouter = None
    CandidateSignal = None
    _SIGNAL_ROUTER_AVAILABLE = False

# 市场状态标注（MarketRegime）
try:
    from components.market_regime import MarketRegime
    _MARKET_REGIME_AVAILABLE = True
except ImportError:
    MarketRegime = None
    _MARKET_REGIME_AVAILABLE = False

logger = logging.getLogger(__name__)

# 飞书主动推送
try:
    from feishu_alert import FeishuAlert
    _feishu = FeishuAlert()
except Exception:
    _feishu = None
    logger.warning("飞书推送模块加载失败，将不发送主动通知")

# ============================================================
# 常量
# ============================================================

INITIAL_CAPITAL = 10000.0  # 每 Agent 模拟初始资金 USDT
DB_PATH = os.path.join(os.path.dirname(__file__), "live_trading.db")
FEISHU_CHAT_ID = os.getenv("FEISHU_DM_CHAT_ID", "")


# ============================================================
# 数据库
# ============================================================

def init_trading_db():
    """初始化实盘模拟数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, timeframe TEXT,
            entry_price REAL, entry_time INTEGER,
            stop_loss REAL, take_profit REAL,
            quantity REAL, status TEXT DEFAULT 'open',
            exchange TEXT DEFAULT 'binance',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, timeframe TEXT,
            entry_price REAL, entry_time INTEGER,
            exit_price REAL, exit_time INTEGER,
            quantity REAL, pnl_pct REAL, pnl_abs REAL,
            exit_reason TEXT,
            ai_verdict TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS equity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT, timestamp INTEGER, price REAL,
            equity REAL, position_value REAL,
            in_position INTEGER,
            rsi REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS signal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT, signal_type TEXT, price REAL,
            rsi REAL, ai_verdict TEXT, message TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    logger.info("实盘模拟数据库初始化完成: %s", DB_PATH)


# ============================================================
# 单个 Agent（Per-Symbol 独立引擎）
# ============================================================

class TradingAgent:
    """
    单标的交易 Agent（参考 VergeX AI Agent 架构）

    每个 Agent 独立运行：
      - 绑定一个标的（symbol）+ 策略（strategy）+ 交易所（exchange）
      - 独立计算 RSI 信号
      - 可选经过 AI 信号过滤器
      - 独立持仓管理
      - 独立数据库记录
    """

    def __init__(
        self,
        agent_id: str,
        symbol: str,
        strategy: str = "RSI",
        exchange: str = "binance",
        timeframe: str = "4h",
        rsi_period: int = 8,
        oversold: float = 22.0,
        overbought: float = 75.0,
        stop_loss_pct: float = 0.025,
        take_profit_pct: float = 0.04,
        initial_capital: float = INITIAL_CAPITAL,
        # 三省六部（2026-05-02）：门下省审核 + 尚书省执行
        menxia: Optional["MenxiaSheng"] = None,
        shangshu: Optional["ShangshuSheng"] = None,
        formula: Optional[str] = None,   # 通达信公式字符串（strategy=FORMULA 时使用）
    ):
        self.agent_id = agent_id
        self.symbol = symbol          # "ETH/USDT"
        self.strategy_name = strategy  # "RSI" | "SMA" | "BOLLINGER" | "FORMULA"
        self.exchange = exchange      # "binance" | "hyperliquid" | "gateio"
        self.timeframe = timeframe
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.initial_capital = initial_capital
        self.formula = formula       # 通达信公式（strategy=FORMULA 时）

        # 三省六部注入
        self.menxia = menxia            # 门下省（风控审核）
        self.shangshu = shangshu        # 尚书省（执行调度）

        # 交易记忆（TradeHistory — Agent-S Outcome Memory 借鉴）
        self._trade_history: Optional["TradeHistory"] = None
        if _TRADE_HISTORY_AVAILABLE:
            try:
                self._trade_history = get_history(
                    db_dir=os.path.dirname(os.path.abspath(__file__))
                )
                logger.info(f"[{agent_id}] TradeHistory 已连接")
            except Exception as e:
                logger.warning(f"[{agent_id}] TradeHistory 初始化失败: {e}")

        # 多信号候选路由器（Agent-S Best-of-N 借鉴）
        self._signal_router: Optional["SignalRouter"] = None
        if _SIGNAL_ROUTER_AVAILABLE:
            try:
                self._signal_router = SignalRouter(
                    menxia=menxia,
                    trade_history=self._trade_history,
                )
                logger.info(f"[{agent_id}] SignalRouter 已连接")
            except Exception as e:
                logger.warning(f"[{agent_id}] SignalRouter 初始化失败: {e}")

        # 市场状态标注（MarketRegime）
        self._market_regime: Optional["MarketRegime"] = None
        if _MARKET_REGIME_AVAILABLE:
            try:
                self._market_regime = MarketRegime(
                    db_dir=os.path.dirname(os.path.abspath(__file__))
                )
                logger.info(f"[{agent_id}] MarketRegime 已连接")
            except Exception as e:
                logger.warning(f"[{agent_id}] MarketRegime 初始化失败: {e}")

        # 当前交易的 trade_uid（关联开仓/平仓）
        self._current_trade_id: Optional[int] = None
        self._current_regime: Dict = {}  # 当前市场状态（供 record_open 使用）

        # 飞书主动推送开关（可配置）
        self._feishu_enabled = os.getenv("FEISHU_PUSH_ENABLED", "true").lower() == "true"

        self.capital = initial_capital
        self.position: Optional[Dict] = None

        # AI 过滤器
        self.ai_filter: Optional[AISignalFilter] = None
        if AI_SIGNAL_FILTER_ENABLED and AI_MODEL:
            try:
                model_map = {"deepseek": AIModel.DEEPSEEK, "openai": AIModel.OPENAI, "minimax": AIModel.MINIMAX}
                model = model_map.get(AI_MODEL.lower(), AIModel.DEEPSEEK)
                self.ai_filter = AISignalFilter(model=model)
                logger.info(f"[{agent_id}] AI 过滤器已启用: {AI_MODEL}")
            except Exception as e:
                logger.warning(f"[{agent_id}] AI 过滤器初始化失败: {e}")

        # 策略实例（支持通达信公式）
        self.strategy_obj = self._build_strategy(strategy)

        self._load_open_position()
        logger.info(f"[{agent_id}] Agent 初始化: {symbol} @ {exchange} "
                   f"策略={strategy} | "
                   f"门下省:{'✓' if menxia else '✗'} | "
                   f"尚书省:{'✓' if shangshu else '✗'}")

    def _build_strategy(self, strategy_type: str):
        """根据策略类型构建策略实例（通过注册表）"""
        config = StrategyConfig(symbol=self.symbol, timeframe=self.timeframe)

        # 多策略投票
        if strategy_type == "VOTE":
            rsi_s = build_strategy("RSI", config, rsi_period=self.rsi_period,
                                   oversold=self.oversold, overbought=self.overbought)
            macd_s = build_strategy("MACD", config)
            boll_s = build_strategy("BOLLINGER", config, period=20, std_dev=2.0)
            return MultiStrategyVote(
                strategies=[(rsi_s, 0.4), (macd_s, 0.3), (boll_s, 0.3)],
                threshold=0.3, name="RSI+MACD+BOLL",
            )

        # 通达信公式
        if strategy_type == "FORMULA":
            formula_str = self.formula or BUILTIN_FORMULAS.get('KDJ', BUILTIN_FORMULAS['MACD'])
            return FormulaStrategy(formula=formula_str, symbol=self.symbol,
                                   timeframe=self.timeframe,
                                   stop_loss=self.stop_loss_pct,
                                   take_profit=self.take_profit_pct)

        # 内置策略（通过注册表）
        strategy_kwargs = {}
        if strategy_type == "RSI":
            strategy_kwargs = {"rsi_period": self.rsi_period,
                               "oversold": self.oversold,
                               "overbought": self.overbought}
        elif strategy_type == "BOLLINGER":
            strategy_kwargs = {"period": 20, "std_dev": 2.0}

        return build_strategy(strategy_type, config, **strategy_kwargs)

    # -------------------- 数据获取 --------------------

    def _build_multi_strategy_candidates(
        self, candles: List[Dict], current_price: float, quantity: float
    ) -> List["CandidateSignal"]:
        """
        为 SignalRouter 构建多策略候选列表。
        当前 Agent 只配了单一策略时，从 K线数据生成其他策略候选信号，
        让路由器能够做跨策略评分比较（Best-of-N）。
        """
        candidates: List["CandidateSignal"] = []
        closes = [c["close"] for c in candles]

        strategy_configs = [
            ("RSI", RSIStrategy(
                StrategyConfig(symbol=self.symbol, timeframe=self.timeframe),
                rsi_period=self.rsi_period, oversold=self.oversold, overbought=self.overbought,
            )),
            ("MACD", MACDStrategy(
                StrategyConfig(symbol=self.symbol, timeframe=self.timeframe),
            )),
            ("BOLLINGER", BollingerBandsStrategy(
                StrategyConfig(symbol=self.symbol, timeframe=self.timeframe),
                period=20, std_dev=2.0,
            )),
        ]

        for name, strat in strategy_configs:
            try:
                if isinstance(strat, (RSIStrategy, MACDStrategy, BollingerBandsStrategy)):
                    indicators = strat.populate_indicators(candles) if hasattr(strat, "populate_indicators") else {}
                else:
                    indicators = {}

                # 估算置信度（基于指标值与策略阈值的关系）
                confidence = self._estimate_confidence(strat, candles)

                candidates.append(CandidateSignal(
                    symbol=self.symbol,
                    strategy=name,
                    confidence=confidence,
                    side="BUY",
                    price=current_price,
                    quantity=quantity,
                    timeframe=self.timeframe,
                    agent_id=self.agent_id,
                    indicators=indicators,
                ))
            except Exception:
                pass

        return candidates

    def _estimate_confidence(self, strategy, candles: List[Dict]) -> float:
        """估算策略信号的置信度（0~1）"""
        try:
            closes = [c["close"] for c in candles]
            rsi_vals = compute_rsi(closes, self.rsi_period)
            current_rsi = rsi_vals[-1]

            if isinstance(strategy, RSIStrategy):
                if current_rsi < self.oversold:
                    return 0.7 + (self.oversold - current_rsi) / 50
                elif current_rsi > self.overbought:
                    return 0.7 + (current_rsi - self.overbought) / 50
            elif isinstance(strategy, MACDStrategy):
                return 0.65
            elif isinstance(strategy, BollingerBandsStrategy):
                return 0.60
            return 0.5
        except Exception:
            return 0.5

    def _fetch_candles(self, limit: int = 50) -> Optional[List[Dict]]:
        """获取 K线数据（根据交易所自动选择）"""
        if self.exchange == "hyperliquid":
            return get_hyperliquid_candles(
                symbol=self.symbol.split("/")[0],
                timeframe=self.timeframe,
                limit=limit,
            )
        else:
            # Binance / Gate.io
            return get_ohlcv(
                symbol=self.symbol.split("/")[0],
                timeframe=self.timeframe,
                limit=limit,
            )

    def _fetch_price(self) -> Optional[float]:
        """获取当前价格"""
        if self.exchange == "hyperliquid":
            data = get_hyperliquid_price(symbol=self.symbol.split("/")[0])
            return data.get("price") if data else None
        else:
            data = get_crypto_price(self.symbol.split("/")[0])
            return data.get("price") if data else None

    # -------------------- 信号检测 --------------------

    def _detect_signals(self, candles: List[Dict]) -> Tuple[int, float, float]:
        """
        检测买入/卖出信号
        Returns: (signal, indicator_value, prev_indicator_value)
        indicator_value 的含义取决于策略：
          - RSI 策略：RSI 值
          - 公式策略：最后一个输出的值（由公式决定）
        """
        if len(candles) < max(self.rsi_period + 2, 30):
            return Signal.HOLD, 50.0, 50.0

        # ── 公式策略 ──
        if isinstance(self.strategy_obj, FormulaStrategy):
            closes = [c["close"] for c in candles]
            entry_signals = self.strategy_obj.populate_entry_trend(candles)
            exit_signals  = self.strategy_obj.populate_exit_trend(candles)
            last_entry = entry_signals[-1] if entry_signals else Signal.HOLD
            last_exit  = exit_signals[-1] if exit_signals else Signal.HOLD

            if last_entry == Signal.BUY:
                # 找第一个买入信号的位置，返回 RSI 近似值
                rsi_vals = self.strategy_obj.populate_indicators(candles)
                rsi_ref = rsi_vals.get("RSI", rsi_vals.get("K", [50.0] * len(candles)))
                return Signal.BUY, rsi_ref[-1], rsi_ref[-2]
            if last_exit == Signal.SELL:
                rsi_vals = self.strategy_obj.populate_indicators(candles)
                rsi_ref = rsi_vals.get("RSI", rsi_vals.get("D", [50.0] * len(candles)))
                return Signal.SELL, rsi_ref[-1], rsi_ref[-2]
            return Signal.HOLD, 50.0, 50.0

        # ── 多策略投票 ──
        if isinstance(self.strategy_obj, MultiStrategyVote):
            # 获取信号和置信度
            entry_signals, confidences = self.strategy_obj.populate_signals_with_confidence(candles)
            last_entry = entry_signals[-1] if entry_signals else 0
            last_conf = confidences[-1] if confidences else 0.0
            if isinstance(last_entry, Signal):
                last_entry = last_entry.value
            closes = [c["close"] for c in candles]
            rsi = compute_rsi(closes, self.rsi_period)
            current_rsi = rsi[-1]
            prev_rsi = rsi[-2]
            if last_entry == 1:
                return Signal.BUY, current_rsi, prev_rsi
            elif last_entry == -1:
                return Signal.SELL, current_rsi, prev_rsi
            return Signal.HOLD, current_rsi, prev_rsi

        # ── 内置策略（RSI / SMA / MACD / BOLLINGER） ──
        closes = [c["close"] for c in candles]
        if len(closes) < self.rsi_period + 2:
            return Signal.HOLD, 50.0, 50.0

        rsi = compute_rsi(closes, self.rsi_period)
        current_rsi = rsi[-1]
        prev_rsi = rsi[-2]

        if (current_rsi >= self.oversold and current_rsi > prev_rsi and prev_rsi <= self.oversold):
            return Signal.BUY, current_rsi, prev_rsi
        if (current_rsi <= self.overbought and current_rsi < prev_rsi and prev_rsi >= self.overbought):
            return Signal.SELL, current_rsi, prev_rsi

        return Signal.HOLD, current_rsi, prev_rsi

    # -------------------- AI 信号验证 --------------------

    def _apply_ai_filter(
        self,
        technical_signal: int,
        current_price: float,
        rsi: float,
        price_change_24h_pct: float,
        volume_24h: float,
    ) -> Tuple[int, str]:
        """
        调用 AI 过滤器验证技术信号
        Returns: (filtered_signal, ai_verdict)
        """
        if not self.ai_filter:
            return technical_signal, "AI未启用"

        pos_status = "in_position" if self.position else "no_position"
        entry_price = self.position["entry_price"] if self.position else None
        unrealized = None
        if self.position and entry_price:
            unrealized = (current_price - entry_price) / entry_price * 100

        market_ctx = MarketContext(
            symbol=self.symbol,
            current_price=current_price,
            price_change_24h_pct=price_change_24h_pct,
            volume_24h=volume_24h,
            rsi=rsi,
            technical_signal={Signal.BUY: "BUY", Signal.SELL: "SELL", Signal.HOLD: "HOLD"}.get(technical_signal, "HOLD"),
            position_status=pos_status,
            entry_price=entry_price,
            unrealized_pnl_pct=unrealized,
        )

        return self.ai_filter.validate_signal(technical_signal, market_ctx)

    # -------------------- 交易操作 --------------------

    async def _open_position(self, price: float, timestamp: int, rsi: float, ai_verdict: str) -> bool:
        """开仓（已通过风控检查）— 支持实盘+模拟双路径"""
        if self.position is not None:
            return False

        quantity = (self.capital * 1.0) / price
        if quantity <= 0:
            return False

        stop_loss = price * (1 - self.stop_loss_pct)
        take_profit = price * (1 + self.take_profit_pct)

        # ── 实盘路径：尚书省执行 ──
        if self.shangshu is not None and LIVE_TRADING_ENABLED:
            result = await self.shangshu.execute_open(
                symbol=self.symbol,
                side="buy",
                quantity=quantity,
                order_type="market",
                agent_id=self.agent_id,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            if not result.success:
                logger.error(f"[{self.agent_id}] 尚书省实盘开仓失败: {result.message}")
                return False
            exec_price = result.exec_price
            logger.info(
                f"[{self.agent_id}] === 实盘 BUY === 价格: ${exec_price:.4f} "
                f"数量: {quantity:.6f} 订单ID: {result.order_id}"
            )
        else:
            # ── 模拟路径（原有逻辑）─
            exec_price = price

        self.position = {
            "symbol": self.symbol,
            "entry_price": exec_price,
            "entry_time": timestamp,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "quantity": quantity,
            "ai_verdict": ai_verdict,
            "entry_rsi": rsi,
            "is_live": self.shangshu is not None and LIVE_TRADING_ENABLED,
            "order_id": result.order_id if self.shangshu else None,
        }
        self.capital = 0.0

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO positions (symbol, timeframe, entry_price, entry_time,
                                   stop_loss, take_profit, quantity, status, exchange)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """, (self.symbol, self.timeframe, exec_price, timestamp, stop_loss, take_profit, quantity, self.exchange))
        conn.commit()
        conn.close()

        # 通知门下省记录
        if self.menxia:
            self.menxia.record_open(self.symbol, exec_price, quantity, stop_loss, take_profit)

        # ── TradeHistory 记录开仓 ──
        if self._trade_history:
            try:
                self._current_trade_id = self._trade_history.record_open(
                    symbol=self.symbol,
                    signal_price=price,          # 中书省发出信号时的价格
                    exec_price=exec_price,        # 实际成交价（含滑点）
                    entry_time=timestamp,
                    quantity=quantity,
                    strategy=self.strategy_name,
                    timeframe=self.timeframe,
                    agent_id=self.agent_id,
                    exchange=self.exchange,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    signal_confidence=0.5,
                    ai_verdict=ai_verdict or "",
                    is_live=self.shangshu is not None and LIVE_TRADING_ENABLED,
                    order_id=result.order_id if self.shangshu else None,
                )
            except Exception as e:
                logger.warning(f"[{self.agent_id}] TradeHistory 开仓记录失败: {e}")

        # 飞书推送：开仓通知
        if _feishu and self._feishu_enabled:
            _feishu.send_position_alert(
                symbol=self.symbol,
                side="BUY",
                price=exec_price,
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"AI验证:{ai_verdict}" if ai_verdict else "",
            )

        logger.info(
            "[%s] === BUY === 价格: $%.2f  RSI: %.2f  数量: %.6f  止损: $%.2f  止盈: $%.2f  AI:%s",
            self.agent_id, exec_price, rsi, quantity, stop_loss, take_profit, ai_verdict
        )
        return True

    async def _close_position(self, price: float, timestamp: int, reason: str, rsi: float) -> bool:
        """平仓（同步通知门下省）— 支持实盘+模拟双路径"""
        if self.position is None:
            return False

        entry_price = self.position["entry_price"]
        quantity = self.position["quantity"]
        ai_verdict = self.position.get("ai_verdict", "")

        pnl_pct = (price - entry_price) / entry_price * 100
        pnl_abs = quantity * (price - entry_price)

        # ── 实盘路径：尚书省执行 ──
        if self.shangshu is not None and LIVE_TRADING_ENABLED:
            result = await self.shangshu.execute_close(
                symbol=self.symbol,
                side="sell",
                quantity=quantity,
                order_type="market",
                agent_id=self.agent_id,
                reason=reason,
            )
            if not result.success:
                logger.error(f"[{self.agent_id}] 尚书省实盘平仓失败: {result.message}")
                return False
            exec_price = result.exec_price
            pnl_pct = (exec_price - entry_price) / entry_price * 100
            logger.info(
                f"[{self.agent_id}] === 实盘 SELL === 价格: ${exec_price:.4f} "
                f"盈亏: {pnl_pct:+.2f}% 订单ID: {result.order_id}"
            )
        else:
            exec_price = price

        self.capital = quantity * exec_price

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO trades (symbol, timeframe, entry_price, entry_time,
                               exit_price, exit_time, quantity, pnl_pct, pnl_abs, exit_reason, ai_verdict)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.symbol, self.timeframe, entry_price, self.position["entry_time"],
              exec_price, timestamp, quantity, pnl_pct, pnl_abs, reason, ai_verdict))
        c.execute("UPDATE positions SET status = ? WHERE status = 'open'", (reason,))
        conn.commit()
        conn.close()

        # 通知门下省更新每日亏损
        if self.menxia:
            self.menxia.record_close(self.symbol, pnl_pct)

        # ── TradeHistory 记录平仓 ──
        if self._trade_history and self._current_trade_id is not None:
            try:
                entry_ts = self.position["entry_time"] if self.position else timestamp
                holding_hours = (timestamp - entry_ts) / 3600.0
                self._trade_history.record_close(
                    trade_id=self._current_trade_id,
                    exit_price=exec_price,
                    exit_time=timestamp,
                    exit_reason=reason,
                    pnl_pct=pnl_pct,
                    pnl_abs=pnl_abs,
                    holding_hours=holding_hours,
                )
                self._current_trade_id = None
            except Exception as e:
                logger.warning(f"[{self.agent_id}] TradeHistory 平仓记录失败: {e}")

        # 飞书推送：平仓通知
        if _feishu and self._feishu_enabled:
            _feishu.send_position_alert(
                symbol=self.symbol,
                side="SELL",
                price=exec_price,
                quantity=quantity,
                pnl_pct=pnl_pct,
                reason=reason,
            )

        logger.info(
            "[%s] === %s 平仓 === 价格: $%.2f  盈亏: %+.2f%%  原因: %s  RSI: %.2f",
            self.agent_id, "SELL" if reason != "stop_loss" else "止损", exec_price, pnl_pct, reason, rsi
        )

        self.position = None
        return True

    async def _check_position_risk(self, price: float, timestamp: int, rsi: float) -> bool:
        """检查持仓是否触发止损/止盈"""
        if self.position is None:
            return False

        pnl_pct = (price - self.position["entry_price"]) / self.position["entry_price"]

        if pnl_pct <= -self.stop_loss_pct:
            await self._close_position(price, timestamp, "stop_loss", rsi)
            return True
        if pnl_pct >= self.take_profit_pct:
            await self._close_position(price, timestamp, "take_profit", rsi)
            return True

        return False

    # -------------------- 主检查循环 --------------------

    async def check_once(self) -> Dict:
        """
        对该 Agent 执行一次完整检查
        Returns: 该 Agent 的状态摘要
        """
        result = {
            "agent_id": self.agent_id,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "strategy": self.strategy_name,
            "timestamp": None,
            "price": None,
            "rsi": None,
            "signal": "HOLD",
            "ai_verdict": "",
            "risk_status": None,
            "position": None,
            "capital": self.capital,
            "equity": self.capital,
            "total_return_pct": 0.0,
            "message": "",
        }

        # 更新全局风控 equity（每个 Agent 检查时顺带更新全局）
        candles = self._fetch_candles(limit=50)
        if not candles:
            result["message"] = "获取K线失败"
            return result

        closes = [c["close"] for c in candles]
        current_price = closes[-1]
        current_ts = candles[-1]["timestamp"]
        result["price"] = current_price
        result["timestamp"] = candles[-1]["timestamp"]

        # 更新门下省 equity（自动调整风险等级）
        equity = self._get_equity(current_price)
        if self.menxia:
            self.menxia.update_equity(equity)

        rsi = compute_rsi(closes, self.rsi_period)[-1]
        result["rsi"] = rsi

        # ── MarketRegime 市场状态标注 ──
        regime_info: Dict = {}
        if self._market_regime:
            try:
                closes_list = [c["close"] for c in candles]
                volumes_list = [c.get("volume", 0) for c in candles]
                regime_state = self._market_regime.get_current_regime(
                    symbol=self.symbol,
                    timeframe=self.timeframe,
                    closes=closes_list,
                    volumes=volumes_list,
                    save=True,
                )
                self._current_regime = regime_state
                regime_info = {
                    "trend": regime_state.get("trend", "unknown"),
                    "volatility": regime_state.get("volatility", "unknown"),
                    "volume": regime_state.get("volume", "unknown"),
                    "confidence": regime_state.get("confidence", 0.0),
                }
                result["market_regime"] = regime_info
            except Exception as e:
                logger.warning(f"[{self.agent_id}] MarketRegime 获取失败: {e}")

        # 获取 24h 数据用于 AI 过滤器
        price_data = self._fetch_price()
        price_change_24h_pct = 0.0
        volume_24h = 0.0
        current_price = price_data  # _fetch_price 返回 float（当前价格）
        if current_price:
            # 尝试从实时数据中获取 24h 变化和成交量（通过单独请求行情数据）
            try:
                from crypto_api import get_crypto_price
                ticker = get_crypto_price(self.symbol.split("/")[0])
                if isinstance(ticker, dict):
                    price_change_24h_pct = ticker.get("change_24h_pct", 0.0)
                    volume_24h = ticker.get("volume_24h", 0.0)
            except Exception:
                pass

        # 门下省：持仓超时检查
        if self.menxia and self.position:
            timeout_list = self.menxia.review_batch_close(self.symbol)
            for sym in timeout_list:
                if sym == self.symbol:
                    logger.warning(f"[{self.agent_id}] 持仓超时，触发强制平仓")
                    await self._close_position(current_price, current_ts, "timeout", rsi)

        # 风控：止损/止盈检查
        await self._check_position_risk(current_price, current_ts, rsi)

        # 门下省状态注入 result
        if self.menxia:
            mx_status = self.menxia.get_status()
            result["risk_status"] = {
                "level": mx_status["risk_level"],
                "daily_loss_pct": mx_status["daily_loss_pct"],
                "total_exposure_pct": mx_status["total_exposure_pct"],
                "can_open": mx_status["can_open"],
            }

        # 信号检测（已完成持仓检查）
        signal_val, _, _ = self._detect_signals(candles)
        signal_names = {Signal.BUY: "BUY", Signal.SELL: "SELL", Signal.HOLD: "HOLD"}
        result["signal"] = signal_names.get(signal_val, "HOLD")

        # AI 过滤（仅对 BUY/SELL 有效）
        ai_verdict = ""
        if signal_val != Signal.HOLD:
            filtered_sig, ai_verdict = self._apply_ai_filter(
                signal_val, current_price, rsi, price_change_24h_pct, volume_24h
            )
            result["ai_verdict"] = ai_verdict

            if filtered_sig == Signal.BUY and self.position is None:
                # === 门下省风控审核（第一优先）===
                can_open, reason = (True, "")
                if self.menxia:
                    review = self.menxia.review_open(
                        symbol=self.symbol,
                        entry_price=current_price,
                        quantity=(self.capital * 1.0) / current_price,
                        agent_id=self.agent_id,
                        signal_confidence=last_conf if 'last_conf' in dir() else 0.5,
                        indicators={'rsi': current_rsi} if 'current_rsi' in dir() else {},
                    )
                    can_open = review.approved
                    reason = review.reason

                # ── SignalRouter 多候选评分（Agent-S Best-of-N）──
                routing_info: Dict = {}
                if self._signal_router and can_open:
                    try:
                        quantity = (self.capital * 1.0) / current_price
                        # 当前策略候选
                        primary = CandidateSignal(
                            symbol=self.symbol,
                            strategy=self.strategy_name,
                            confidence=last_conf if 'last_conf' in dir() else 0.5,
                            side="BUY",
                            price=current_price,
                            quantity=quantity,
                            timeframe=self.timeframe,
                            agent_id=self.agent_id,
                            indicators={"rsi": current_rsi} if 'current_rsi' in dir() else {},
                        )
                        # 从配置扩展多策略候选（如果有额外策略可用）
                        candidates = self._build_multi_strategy_candidates(
                            candles, current_price, quantity
                        )
                        if primary not in candidates:
                            candidates.insert(0, primary)

                        mx_status = self.menxia.get_status()
                        exposure = mx_status.get("total_exposure_pct", 0) / 100.0
                        route_result = self._signal_router.route(
                            candidates=candidates,
                            exposure_pct=exposure,
                            market_trend="unknown",     # P2: market_regime
                            market_volatility="unknown",
                        )
                        routing_info = {
                            "routing_reason": route_result.routing_reason,
                            "chosen_strategy": route_result.chosen.strategy if route_result.chosen else None,
                            "chosen_score": route_result.chosen.score if route_result.chosen else None,
                            "alternatives": [
                                {"strategy": c.strategy, "score": c.score,
                                 "breakdown": c.score_breakdown}
                                for c in route_result.alternatives
                            ],
                        }
                        if route_result.chosen:
                            can_open = True
                            logger.info(
                                f"[{self.agent_id}] SignalRouter 选中 "
                                f"{route_result.chosen.strategy}（评分 {route_result.chosen.score:.1f}）"
                            )
                        else:
                            can_open = False
                            reason = f"全部 {len(route_result.rejected)} 个候选被否决"
                    except Exception as e:
                        logger.warning(f"[{self.agent_id}] SignalRouter 异常: {e}")

                if not can_open:
                    logger.warning(f"[{self.agent_id}] 门校省否决开仓: {reason}")
                    result["signal"] = f"门校省否决({reason})"
                    if routing_info:
                        result["routing"] = routing_info
                else:
                    await self._open_position(current_price, current_ts, rsi, ai_verdict)
                    result["signal"] = "BUY"
                    if routing_info:
                        result["routing"] = routing_info
            elif filtered_sig == Signal.HOLD and signal_val == Signal.BUY:
                result["signal"] = "HOLD（AI否决）"
        else:
            result["ai_verdict"] = "技术信号HOLD"

        # 权益
        equity = self._get_equity(current_price)
        result["equity"] = equity
        result["total_return_pct"] = (equity - self.initial_capital) / self.initial_capital * 100
        result["capital"] = self.capital

        self._log_equity(current_ts, current_price, equity, rsi)
        self._log_signal(signal_names.get(signal_val, "HOLD"), current_price, rsi, result["ai_verdict"])

        # 持仓状态
        if self.position:
            entry = self.position["entry_price"]
            pnl = (current_price - entry) / entry * 100
            result["position"] = {
                "entry_price": entry,
                "current_price": current_price,
                "pnl_pct": pnl,
                "stop_loss": self.position["stop_loss"],
                "take_profit": self.position["take_profit"],
                "quantity": self.position["quantity"],
            }

        return result

    def _get_equity(self, current_price: float) -> float:
        if self.position:
            qty = self.position["quantity"]
            return self.capital + qty * current_price
        return self.capital

    # -------------------- 持久化 --------------------

    def _load_open_position(self):
        """从数据库恢复未平持仓"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT symbol, entry_price, entry_time, stop_loss, take_profit, quantity, exchange
            FROM positions WHERE status = 'open' AND symbol = ? ORDER BY id DESC LIMIT 1
        """, (self.symbol,))
        row = c.fetchone()
        conn.close()
        if row:
            self.position = {
                "symbol": row[0],
                "entry_price": row[1],
                "entry_time": row[2],
                "stop_loss": row[3],
                "take_profit": row[4],
                "quantity": row[5],
                "exchange": row[6],
            }
            self.capital = 0.0
            logger.info("[%s] 恢复未平持仓: %s 价格 $%.2f  数量 %.6f",
                        self.agent_id, row[0], row[1], row[5])

    def _log_equity(self, timestamp: int, price: float, equity: float, rsi: float):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO equity_log (agent_id, timestamp, price, equity, position_value, in_position, rsi)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (self.agent_id, timestamp, price, equity,
              self.position["quantity"] * price if self.position else 0.0,
              1 if self.position else 0, rsi))
        conn.commit()
        conn.close()

    def _log_signal(self, signal_type: str, price: float, rsi: float, ai_verdict: str):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO signal_log (agent_id, signal_type, price, rsi, ai_verdict, message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (self.agent_id, signal_type, price, rsi, ai_verdict, ""))
        conn.commit()
        conn.close()


# ============================================================
# 多 Agent 编排器（VergeX AI 多 Agent 架构核心）
# ============================================================

class MultiAgentOrchestrator:
    """
    多 Agent 编排器 — 参考 VergeX AI 的多 Agent 并行架构

    功能：
      - 解析 AGENT_SYMBOLS 配置，创建多个独立 Agent
      - 并行执行所有 Agent 的 check_once()
      - 汇总所有 Agent 状态
      - 定期轮询（后台线程）
      - 三省六部：门下省（风控审核）+ 尚书省（执行调度）

    三省六部流程：
      中书省信号 → 门下省审核 → ✅ → 尚书省执行 → 刑部记录
    """

    def __init__(self, with_risk_manager: bool = True,
                 live_trading: bool = LIVE_TRADING_ENABLED):
        self.agents: List[TradingAgent] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.live_trading = live_trading and _SHANGSHU_AVAILABLE

        # ── 门下省：风控审核服务（所有 Agent 共享）──
        self.menxia: Optional[MenxiaSheng] = None
        if _MENXIA_AVAILABLE:
            # 飞书告警回调（风控等级变化时推送到群）
            def _risk_alert(level: str, msg: str):
                if _feishu:
                    daily_loss = getattr(self.menxia, '_daily_loss', 0.0) * 100
                    total_exp = 0.0
                    try:
                        status = self.menxia.get_status()
                        total_exp = status.get('total_exposure_pct', 0.0)
                    except Exception:
                        pass
                    _feishu.send_risk_alert(
                        level=level,
                        message=msg,
                        daily_loss_pct=daily_loss,
                        total_exposure_pct=total_exp,
                    )

            self.menxia = MenxiaSheng(
                initial_capital=LIVE_INITIAL_CAPITAL,
                db_path=DB_PATH,
                risk_alert_callback=_risk_alert,
            )
            self.menxia.MAX_DAILY_LOSS_PCT = RISK_MAX_DAILY_LOSS_PCT
            self.menxia.MAX_DAILY_LOSS_LOCK = RISK_MAX_DAILY_LOSS_LOCK
            self.menxia.MAX_TOTAL_EXPOSURE = RISK_MAX_TOTAL_EXPOSURE
            self.menxia.MAX_POSITION_PER_SYMBOL = RISK_MAX_POSITION_PER_SYMBOL
            self.menxia.MAX_DAILY_TRADES = RISK_MAX_DAILY_TRADES
            self.menxia.MAX_HOLDING_HOURS = RISK_MAX_HOLDING_HOURS
            logger.info(f"[门下省] 初始化: 单日亏损>{RISK_MAX_DAILY_LOSS_PCT*100:.0f}%禁止开仓, "
                       f"总暴露>{RISK_MAX_TOTAL_EXPOSURE*100:.0f}%禁止开仓")

        # ── 尚书省：执行调度服务 ──
        self.shangshu: Optional[ShangshuSheng] = None
        if _SHANGSHU_AVAILABLE and live_trading:
            try:
                self.shangshu = ShangshuSheng(
                    exchange=LIVE_EXCHANGE,
                    api_key=LIVE_API_KEY,
                    api_secret=LIVE_API_SECRET,
                    testnet=LIVE_TESTNET,
                    db_path=DB_PATH,
                )
                mode = "测试网" if LIVE_TESTNET else "实盘"
                logger.info(f"[尚书省] 初始化: {LIVE_EXCHANGE} ({mode})")
            except Exception as e:
                logger.error(f"[尚书省] 初始化失败: {e}")
                self.shangshu = None

        self._parse_and_create_agents()
        logger.info(f"多 Agent 编排器已初始化: {len(self.agents)} 个 Agent | "
                   f"实盘: {'是' if self.live_trading else '否（模拟）'}")

    def _parse_and_create_agents(self):
        """解析 AGENT_SYMBOLS 配置，创建 Agent 实例"""
        agent_configs = AGENT_SYMBOLS.split(",")
        for i, cfg in enumerate(agent_configs):
            cfg = cfg.strip()
            if not cfg:
                continue

            parts = cfg.split(":")
            symbol = parts[0].strip()

            # 解析策略类型，支持 FORMULA:名称 语法
            raw_strategy = parts[1].strip().upper() if len(parts) > 1 else "RSI"
            formula_name = None
            if raw_strategy.startswith("FORMULA:"):
                formula_name = raw_strategy.split(":", 1)[1].strip().lower()
                strategy = "FORMULA"
            else:
                strategy = raw_strategy

            exchange = parts[2].strip().lower() if len(parts) > 2 else "binance"
            # 自定义公式（FORMULA:名称 语法，第四个字段指定公式名）
            custom_formula_str = None
            if len(parts) > 3:
                custom_formula_str = parts[3].strip()

            # 解析公式字符串（FORMULA:builtin_name 或 FORMULA:custom_name）
            resolved_formula = None
            if strategy == "FORMULA":
                if formula_name:
                    # FORMULA:MACD, FORMULA:KDJ 等内置公式
                    resolved_formula = BUILTIN_FORMULAS.get(formula_name.upper(), BUILTIN_FORMULAS.get('MACD'))
                elif custom_formula_str:
                    # FORMULA::自定义公式代码（第四字段直接是公式）
                    resolved_formula = custom_formula_str
                else:
                    resolved_formula = BUILTIN_FORMULAS.get('MACD')

            # 逐标的最优参数（Grid Search 结果，2026-05-03）
            params = OPTIMAL_PARAMS.get(symbol, {})
            rsi_p = params.get("rsi_period", STRATEGY_RSI_PERIOD)
            os_val = params.get("oversold", STRATEGY_RSI_OVERSOLD)
            ob_val = params.get("overbought", STRATEGY_RSI_OVERBOUGHT)
            sl_val = params.get("stop_loss", STRATEGY_STOP_LOSS)
            tp_val = params.get("take_profit", STRATEGY_TAKE_PROFIT)

            agent = TradingAgent(
                agent_id=f"agent_{i+1}",
                symbol=symbol,
                strategy=strategy,
                exchange=exchange,
                timeframe="4h",
                rsi_period=rsi_p,
                oversold=os_val,
                overbought=ob_val,
                stop_loss_pct=sl_val,
                take_profit_pct=tp_val,
                formula=resolved_formula,
                # 三省六部注入
                menxia=self.menxia,
                shangshu=self.shangshu,
            )
            self.agents.append(agent)

    async def check_all_once(self) -> List[Dict]:
        """对所有 Agent 执行一次检查（异步）"""
        import asyncio
        results = []
        for agent in self.agents:
            try:
                result = await agent.check_once()
                results.append(result)
            except Exception as e:
                logger.error(f"[{agent.agent_id}] 检查失败: {e}")
                results.append({
                    "agent_id": agent.agent_id,
                    "symbol": agent.symbol,
                    "error": str(e),
                })
        return results

    def get_all_status(self) -> List[Dict]:
        """获取所有 Agent 状态"""
        results = []
        for agent in self.agents:
            current_price = agent._fetch_price()
            equity = agent._get_equity(current_price or 0)
            status = {
                "agent_id": agent.agent_id,
                "symbol": agent.symbol,
                "exchange": agent.exchange,
                "strategy": agent.strategy_name,
                "capital": agent.capital,
                "equity": equity,
                "total_return_pct": (equity - agent.initial_capital) / agent.initial_capital * 100,
                "position": agent.position,
                "current_price": current_price,
            }
            # 注入门下省全局风控状态（如果 agent 有 menxia）
            if agent.menxia:
                mx = agent.menxia.get_status()
                status["risk_level"] = mx["risk_level"]
                status["daily_loss_pct"] = mx["daily_loss_pct"]
                status["total_exposure_pct"] = mx["total_exposure_pct"]
                status["risk_can_open"] = mx["can_open"]
            results.append(status)
        return results

    def start_background(self):
        """启动后台轮询线程"""
        if self._running:
            logger.warning("多 Agent 已在后台运行")
            return

        self._running = True
        self._thread = threading.Thread(target=self._background_loop, daemon=True)
        self._thread.start()
        logger.info("多 Agent 后台轮询已启动（间隔 %ds）", AGENT_CHECK_INTERVAL)

    def stop_background(self):
        """停止后台轮询"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("多 Agent 后台轮询已停止")

    def _background_loop(self):
        """后台轮询主循环"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self._running:
            try:
                loop.run_until_complete(self.check_all_once())
            except Exception as e:
                logger.error(f"后台轮询异常: {e}")
            time.sleep(AGENT_CHECK_INTERVAL)
        loop.close()

    def print_status(self):
        """打印所有 Agent 状态"""
        status_list = self.get_all_status()
        print()
        print("=" * 70)
        print(f"  多 Agent 状态  ({len(status_list)} 个 Agent)")
        print("=" * 70)
        total_return = 0.0
        for s in status_list:
            pos_info = "持有中" if s["position"] else "空仓"
            price_str = f"${s['current_price']:.2f}" if s["current_price"] else "N/A"
            print(f"\n  [{s['agent_id']}] {s['symbol']} @ {s['exchange']}  ({s['strategy']})")
            print(f"    当前价格    : {price_str}")
            print(f"    模拟资金    : ${s['capital']:.2f}")
            print(f"    总资产      : ${s['equity']:.2f}  ({s['total_return_pct']:+.2f}%)")
            print(f"    持仓状态    : {pos_info}")
            if s["position"]:
                p = s["position"]
                pnl = (s["current_price"] - p["entry_price"]) / p["entry_price"] * 100 if s["current_price"] else 0
                print(f"    入场价      : ${p['entry_price']:.2f}")
                print(f"    持仓盈亏    : {pnl:+.2f}%")
            total_return += s["total_return_pct"]

        avg_return = total_return / len(status_list) if status_list else 0
        print()
        print(f"  平均收益率  : {avg_return:+.2f}%")
        print("=" * 70)


# ============================================================
# API Key 安全验证命令
# ============================================================

def validate_api_keys():
    """验证当前配置的 API Key 权限是否为 Trade-only"""
    if not CRYPTO_API_KEY or not CRYPTO_API_SECRET:
        print("⚠️  未配置 CRYPTO_API_KEY 或 CRYPTO_API_SECRET")
        print("   请在 .env 文件中配置交易所 API Key")
        return

    print(f"正在验证 API Key 权限（交易所: {CRYPTO_EXCHANGE}）...")
    result = validate_trade_only_key(CRYPTO_EXCHANGE, CRYPTO_API_KEY, CRYPTO_API_SECRET)

    print()
    if result["valid"]:
        print(f"✅ {result['message']}")
    else:
        print(f"⚠️  {result['message']}")

    print(f"   权限列表: {result['permissions']}")
    print(f"   可提现: {'是 ⚠️' if result['can_withdraw'] else '否 ✅'}")
    print()
    if not result["valid"]:
        print("建议：请在交易所创建 Trade-only API Key，仅授权交易，禁用提现")
        print("Binance 操作路径: API管理 → 创建API → 选择'仅交易'权限")


# ============================================================
# 主入口
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="实盘模拟引擎 v2 — VergeX AI 多 Agent 架构",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python live_trading.py --check              # 执行一次信号检查（多Agent）
  python live_trading.py --status              # 显示所有Agent状态
  python live_trading.py --single ETH          # 单标的模式（兼容旧版）
  python live_trading.py --validate-key        # 验证API Key权限
  python live_trading.py --daemon              # 后台常驻多Agent轮询

环境变量:
  AGENT_SYMBOLS       多Agent标的配置（默认: ETH/USDT:RSI:binance）
  AI_MODEL            AI模型 deepseek|openai（默认: 空=禁用）
  AI_SIGNAL_FILTER_ENABLED  true=启用AI过滤（默认: false）
  USE_HYPERLIQUID     true=优先使用Hyperliquid（默认: false）
  MULTI_AGENT_ENABLED true=启用多Agent（默认: false）
  VALIDATE_TRADE_ONLY true=启动时验证API权限（默认: true）
        """
    )
    parser.add_argument("--check", action="store_true", help="执行一次信号检查并退出")
    parser.add_argument("--status", action="store_true", help="显示所有Agent状态并退出")
    parser.add_argument("--single", metavar="SYMBOL", help="单标的模式（兼容旧版，如 ETH）")
    parser.add_argument("--validate-key", action="store_true", help="验证API Key权限")
    parser.add_argument("--daemon", action="store_true", help="后台常驻多Agent轮询")
    args = parser.parse_args()

    # 初始化数据库
    init_trading_db()

    # API Key 安全验证
    if args.validate_key:
        validate_api_keys()
        return

    # Trade-only 启动检查
    if VALIDATE_TRADE_ONLY and CRYPTO_API_KEY and CRYPTO_API_SECRET:
        print("正在验证 API Key 权限...")
        result = validate_trade_only_key(CRYPTO_EXCHANGE, CRYPTO_API_KEY, CRYPTO_API_SECRET)
        if result["valid"]:
            print(f"✅ {result['message']}")
        else:
            print(f"⚠️  {result['message']}（可在 .env 中设置 VALIDATE_TRADE_ONLY=false 跳过）")

    # Hyperliquid 钱包设置
    if USE_HYPERLIQUID and HYPERLIQUID_WALLET_ADDRESS:
        set_hyperliquid_wallet(HYPERLIQUID_WALLET_ADDRESS)

    # 单标的模式（兼容旧版）
    if args.single:
        symbol = args.single.upper()
        if not symbol.endswith("/USDT"):
            symbol = f"{symbol}/USDT"
        agent = TradingAgent(
            agent_id="single",
            symbol=symbol,
            strategy="RSI",
            exchange=CRYPTO_EXCHANGE if not USE_HYPERLIQUID else "hyperliquid",
        )
        result = agent.check_once()
        print(f"\n[{agent.agent_id}] {symbol} 信号检查完成")
        print(f"  信号: {result['signal']}  RSI: {result.get('rsi', 0):.2f}  价格: ${result.get('price', 0):.2f}")
        print(f"  AI裁决: {result.get('ai_verdict', 'N/A')}")
        if result.get('position'):
            p = result['position']
            print(f"  持仓: 入场 ${p['entry_price']:.2f}  当前 ${p['current_price']:.2f}  {p['pnl_pct']:+.2f}%")
        return

    # 多 Agent 模式
    orchestrator = MultiAgentOrchestrator()

    if args.check:
        import asyncio
        results = asyncio.get_event_loop().run_until_complete(
            orchestrator.check_all_once()
        )
        print(f"\n多 Agent 信号检查完成（{len(results)} 个 Agent）")
        for r in results:
            err = r.get("error", "")
            if err:
                print(f"  [{r['agent_id']}] {r['symbol']}: 错误 - {err}")
                continue
            print(f"  [{r['agent_id']}] {r['symbol']}: {r['signal']}  "
                  f"RSI:{r.get('rsi', 0):.1f}  价格:${r.get('price', 0):.2f}  "
                  f"AI:{r.get('ai_verdict', 'N/A')}")
        orchestrator.print_status()

    elif args.status:
        orchestrator.print_status()

    elif args.daemon:
        print(f"多 Agent 后台常驻模式已启动（{len(orchestrator.agents)} 个 Agent）")
        print(f"检查间隔: {AGENT_CHECK_INTERVAL}s")
        print("按 Ctrl+C 停止")
        orchestrator.start_background()
        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            print("\n正在停止...")
            orchestrator.stop_background()

    else:
        print(f"实盘模拟引擎 v2 已启动")
        print(f"多 Agent 模式: {'启用' if MULTI_AGENT_ENABLED else '未启用（使用 --single）'}")
        print(f"AI 过滤: {'启用 (' + AI_MODEL + ')' if AI_SIGNAL_FILTER_ENABLED else '未启用'}")
        print(f"Hyperliquid: {'启用' if USE_HYPERLIQUID else '未启用'}")
        print()
        orchestrator.print_status()
        print()
        print("使用说明:")
        print("  --check        执行一次信号检查")
        print("  --status       显示所有Agent状态")
        print("  --single ETH   单标的模式（兼容旧版）")
        print("  --validate-key 验证API Key权限")
        print("  --daemon       后台常驻多Agent轮询")


if __name__ == "__main__":
    main()
