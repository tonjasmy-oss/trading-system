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
import asyncio
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
    LIVE_TESTNET, LIVE_TESTNET_API_KEY, LIVE_TESTNET_API_SECRET,
    LIVE_INITIAL_CAPITAL, LIVE_ORDER_CAPITAL_PCT,
    WEEX_API_PASSPHRASE,
    RISK_MAX_DAILY_LOSS_PCT, RISK_MAX_DAILY_LOSS_LOCK,
    RISK_MAX_TOTAL_EXPOSURE, RISK_MAX_POSITION_PER_SYMBOL,
    RISK_MAX_DAILY_TRADES, RISK_MAX_HOLDING_HOURS,
    STRATEGY_RSI_PERIOD, STRATEGY_RSI_OVERSOLD, STRATEGY_RSI_OVERBOUGHT,
    STRATEGY_STOP_LOSS, STRATEGY_TAKE_PROFIT,
    OPTIMAL_PARAMS,
    # 多因子 / 资金费率套利 / 统计套利 参数
    MULTIFACTOR_MIN_SCORE, MULTIFACTOR_EMA_PERIOD, MULTIFACTOR_ATR_PERIOD,
    MULTIFACTOR_ATR_MULTIPLIER, MULTIFACTOR_MAX_POSITION, MULTIFACTOR_TRAILING_PCT,
    MULTIFACTOR_STOP_LOSS_PCT, MULTIFACTOR_TP1_PCT, MULTIFACTOR_TP2_PCT,
    MULTIFACTOR_FUNDING_THRESH,
    FUNDING_ARB_MIN_RATE, FUNDING_ARB_MAX_RATE, FUNDING_ARB_REBALANCE_H,
    STAT_ARB_PAIR_SYMBOL, STAT_ARB_LOOKBACK, STAT_ARB_Z_ENTRY,
    STAT_ARB_Z_EXIT, STAT_ARB_Z_LOSS,
    BLACK_SWAN_DROP_PCT, MAX_DRAWDOWN_LOCK_PCT,
    ATRSTOP_EMA_PERIOD, ATRSTOP_ATR_PERIOD, ATRSTOP_ATR_MULTIPLIER,
)
from crypto_api import (
    get_crypto_price, get_ohlcv,
    validate_trade_only_key, set_hyperliquid_wallet,
    get_hyperliquid_price, get_hyperliquid_candles,
    get_fear_and_greed_index, get_btc_dominance, get_funding_rate,
    get_onchain_metrics, get_multi_factor_data,
)
from strategies import (
    Signal, AISignalFilter, AIModel, MarketContext,
    StrategyConfig, build_strategy, STRATEGY_REGISTRY,
    compute_rsi, RSIStrategy, MACDStrategy, BollingerBandsStrategy,
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

# 多周期信号确认（MTF Confirmer）
try:
    from components.mtf_confirmer import MultiTimeframeConfirmer
    _MTF_AVAILABLE = True
except ImportError:
    MultiTimeframeConfirmer = None
    _MTF_AVAILABLE = False

# 在线参数优化
try:
    from components.online_optimizer import OnlineParameterOptimizer
    _OPTIMIZER_AVAILABLE = True
except ImportError:
    OnlineParameterOptimizer = None
    _OPTIMIZER_AVAILABLE = False

# 交易后反思复盘
try:
    from reflection import ReflectionService
    _REFLECTION_AVAILABLE = True
except ImportError:
    ReflectionService = None
    _REFLECTION_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
logger = logging.getLogger(__name__)

# 飞书主动推送（模块级 lazy init，TradingAgent 可通过参数注入覆盖）
_feishu_sentinel = object()
_feishu = _feishu_sentinel  # 未初始化标记
try:
    from feishu_alert import FeishuAlert
    _feishu = FeishuAlert()
except Exception:
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

def _enable_wal(conn: sqlite3.Connection) -> None:
    """启用 WAL mode，提升多进程并发读写性能"""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")


def init_trading_db():
    """初始化实盘模拟数据库"""
    conn = sqlite3.connect(DB_PATH)
    _enable_wal(conn)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, timeframe TEXT,
            entry_price REAL, entry_time INTEGER,
            stop_loss REAL, take_profit REAL,
            quantity REAL, status TEXT DEFAULT 'open',
            side TEXT DEFAULT 'long',
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
    # 迁移：为已有数据库增加 side 列
    try: c.execute("ALTER TABLE positions ADD COLUMN side TEXT DEFAULT 'long'")
    except sqlite3.OperationalError: pass
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
        # Donchian Channel 参数
        channel_period: int = 20,
        trend_ema_period: int = 50,
        # 三省六部（2026-05-02）：门下省审核 + 尚书省执行
        menxia: Optional["MenxiaSheng"] = None,
        shangshu: Optional["ShangshuSheng"] = None,
        formula: Optional[str] = None,   # 通达信公式字符串（strategy=FORMULA 时使用）
    ):
        self.agent_id = agent_id
        self.symbol = symbol          # "ETH/USDT"
        self.strategy_name = strategy  # "RSI" | "SMA" | "BOLLINGER" | "DONCHIAN"
        self.exchange = exchange      # "binance" | "hyperliquid" | "gateio"
        self.timeframe = timeframe
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.initial_capital = initial_capital
        self.channel_period = channel_period
        self.trend_ema_period = trend_ema_period
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
        # 实例级飞书推送器（允许外部注入，覆盖模块级默认值）
        self._feishu = _feishu if _feishu is not _feishu_sentinel else None

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

        # 策略轮动器（strategy=AUTO 时使用）
        self._rotator = None
        if strategy == "AUTO":
            try:
                from components.strategy_rotator import StrategyRotator
                self._rotator = StrategyRotator(symbol=symbol)
                logger.info(f"[{agent_id}] 策略轮动器已启用")
            except ImportError:
                logger.warning(f"[{agent_id}] 策略轮动器不可用，回退到MACD")

        # 多周期信号确认器
        self._mtf_confirmer = None
        if _MTF_AVAILABLE:
            self._mtf_confirmer = MultiTimeframeConfirmer(symbol=symbol)

        # 在线参数优化器
        self._optimizer: Optional[OnlineParameterOptimizer] = None
        if _OPTIMIZER_AVAILABLE:
            anchor = OPTIMAL_PARAMS.get(symbol, {})
            self._optimizer = OnlineParameterOptimizer(
                symbol=symbol,
                db_path=DB_PATH,
                anchor_params=anchor,
            )
            self._optimizer.set_current_params({
                "rsi_period": rsi_period,
                "oversold": oversold,
                "overbought": overbought,
                "stop_loss": stop_loss_pct,
                "take_profit": take_profit_pct,
            })
            logger.info(f"[{agent_id}] 在线参数优化器已启用")

        # 策略实例（支持通达信公式）
        self.strategy_obj = self._build_strategy(strategy)

        # ── 启动时同步：通过余额差额推断交易所持仓（Weex v3 无独立持仓 API）──
        if self.shangshu is not None and hasattr(self.shangshu, 'fetch_balance'):
            try:
                loop = asyncio.get_event_loop()
                balance = loop.run_until_complete(self.shangshu.fetch_balance())
                usdt = balance.get('USDT', {}) if balance else {}
                total_usdt = usdt.get('total', 0)
                free_usdt = usdt.get('free', 0)
                margin_locked = total_usdt - free_usdt  # 总余额 - 可用 = 占用保证金
                if balance and margin_locked <= 0.01:
                    conn = sqlite3.connect(DB_PATH)
                    _enable_wal(conn)
                    c = conn.cursor()
                    c.execute("UPDATE positions SET status='exchange_closed' WHERE symbol=? AND status='open' AND exchange=?", (self.symbol, self.exchange))
                    conn.commit()
                    rows = c.rowcount
                    conn.close()
                    if rows > 0:
                        logger.warning(f"[{agent_id}] 同步清除{rows}条幽灵持仓（交易所无持仓）")
                    self.position = None
                    logger.info(f"[{agent_id}] 启动同步完成：交易所无持仓（保证金占用≈0），DB已校正")
                else:
                    logger.info(f"[{agent_id}] 启动同步完成：交易所可能有持仓（保证金占用≈${margin_locked:.2f}），DB保留")
            except Exception as e:
                logger.warning(f"[{agent_id}] 启动同步失败（不影响启动）：{e}")

        # ── 余额同步：覆盖 initial_capital 为交易所真实 USDT 可用余额 ──
        if self.shangshu is not None and hasattr(self.shangshu, 'fetch_balance'):
            try:
                loop = asyncio.get_event_loop()
                bal = loop.run_until_complete(self.shangshu.fetch_balance())
                usdt = bal.get("USDT", {})
                free_usdt = usdt.get("free", 0)
                total_usdt = usdt.get("total", 0)
                frozen_usdt = usdt.get("frozen", 0)
                if total_usdt > 0:
                    self.initial_capital = float(free_usdt)
                    self.capital = float(free_usdt)
                    logger.info(f"[{agent_id}] 余额同步: total=${total_usdt:.4f} available=${free_usdt:.4f} frozen=${frozen_usdt:.4f} → 可用资金 ${free_usdt:.4f} USDT")
                else:
                    logger.info(f"[{agent_id}] 余额同步: 交易所余额=0，保持模拟资金 ${self.initial_capital:.2f}")
            except Exception as e:
                logger.warning(f"[{agent_id}] 余额同步失败，保持模拟资金 ${self.initial_capital:.2f}：{e}")

        self._load_open_position()
        logger.info(f"[{agent_id}] Agent 初始化: {symbol} @ {exchange} "
                   f"策略={strategy} | "
                   f"门下省:{'✓' if menxia else '✗'} | "
                   f"尚书省:{'✓' if shangshu else '✗'}")

    def _build_strategy(self, strategy_type: str, rotator_kwargs: dict = None):
        """根据策略类型构建策略实例（通过注册表）。rotator_kwargs 由 StrategyRotator 传入。"""
        config = StrategyConfig(symbol=self.symbol, timeframe=self.timeframe)

        # 自动轮动模式：初始默认多策略投票（RSI+MACD+BOLL），StrategyRotator 会根据市场状态轮动
        if strategy_type == "AUTO":
            rsi_s = build_strategy("RSI", config, rsi_period=self.rsi_period,
                                   oversold=self.oversold, overbought=self.overbought)
            macd_s = build_strategy("MACD", config)
            boll_s = build_strategy("BOLLINGER", config, period=20, std_dev=2.0)
            return MultiStrategyVote(
                strategies=[(rsi_s, 0.4), (macd_s, 0.3), (boll_s, 0.3)],
                threshold=0.3, name="RSI+MACD+BOLL",
            )

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
        elif strategy_type == "ATRSTOP":
            strategy_kwargs = {
                "ema_period": ATRSTOP_EMA_PERIOD,
                "atr_period": ATRSTOP_ATR_PERIOD,
                "atr_multiplier": ATRSTOP_ATR_MULTIPLIER
            }
        elif strategy_type == "SMA":
            strategy_kwargs = {"fast_period": 10, "slow_period": 30}
        elif strategy_type == "KDJ":
            pass  # KDJ 使用 FormulaStrategy 包装，无需额外参数
        elif strategy_type == "DONCHIAN":
            strategy_kwargs = {
                "channel_period": self.channel_period,
                "trend_ema_period": self.trend_ema_period,
            }

        # ── 多因子趋势策略（MULTIFACTOR）─────────────────────────────
        elif strategy_type == "MULTIFACTOR":
            logger.info(f"[{self.agent_id}] 初始化多因子趋势策略")
            from strategies import MultiFactorTrendStrategy
            return MultiFactorTrendStrategy(
                config=StrategyConfig(symbol=self.symbol, timeframe=self.timeframe),
                min_score=MULTIFACTOR_MIN_SCORE,
                ema_period=MULTIFACTOR_EMA_PERIOD,
                atr_period=MULTIFACTOR_ATR_PERIOD,
                atr_multiplier=MULTIFACTOR_ATR_MULTIPLIER,
                max_position_pct=MULTIFACTOR_MAX_POSITION,
                trailing_pct=MULTIFACTOR_TRAILING_PCT,
            )

        # ── 资金费率套利策略（FUNDING_ARB）───────────────────────────
        elif strategy_type == "FUNDING_ARB":
            logger.info(f"[{self.agent_id}] 初始化资金费率套利策略")
            from strategies import FundingRateArbitrageStrategy
            return FundingRateArbitrageStrategy(
                config=StrategyConfig(symbol=self.symbol, timeframe=self.timeframe),
                min_funding_rate=FUNDING_ARB_MIN_RATE,
                max_funding_rate=FUNDING_ARB_MAX_RATE,
                rebalance_hours=FUNDING_ARB_REBALANCE_H,
            )

        # ── 统计套利策略（STAT_ARB）─────────────────────────────────
        elif strategy_type == "STAT_ARB":
            logger.info(f"[{self.agent_id}] 初始化统计套利策略")
            from strategies import StatisticalArbitrageStrategy
            pair_symbol = STAT_ARB_PAIR_SYMBOL
            # 从配置字典获取配对币种（如 STAT_ARB_PAIR_ETH = "BTC"）
            stat_pairs = {}
            try:
                from config import STAT_ARB_PAIRS
                stat_pairs = STAT_ARB_PAIRS
            except Exception:
                pass
            pair_base = stat_pairs.get(self.symbol, pair_symbol)
            return StatisticalArbitrageStrategy(
                config=StrategyConfig(symbol=self.symbol, timeframe=self.timeframe),
                pair_symbol=pair_base,
                lookback=STAT_ARB_LOOKBACK,
                z_entry=STAT_ARB_Z_ENTRY,
                z_exit=STAT_ARB_Z_EXIT,
                z_exit_loss=STAT_ARB_Z_LOSS,
            )

        # 合并轮动器传入的参数
        if rotator_kwargs:
            strategy_kwargs.update(rotator_kwargs)
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
        elif self.exchange == "weex":
            # Weex 用于交易执行，数据从 Gate.io 拉取（Weex 不支持部分周期如 2h）
            return get_ohlcv(
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
        elif self.exchange == "weex":
            from weex import fetch_ticker as weex_ticker
            ticker = weex_ticker(self.symbol.split("/")[0])
            return ticker["price"] if ticker else None
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

        # ── 内置策略（RSI / SMA / MACD / BOLLINGER / AUTO-VOTE） ──
        closes = [c["close"] for c in candles]

        # 始终计算 RSI 用于日志和辅助判断
        rsi_vals = compute_rsi(closes, self.rsi_period)
        current_rsi = rsi_vals[-1] if len(rsi_vals) > 0 else 50.0
        prev_rsi = rsi_vals[-2] if len(rsi_vals) > 1 else 50.0

        # 委托给策略对象计算信号（而非硬编码 RSI 交叉）
        try:
            entry_trend = self.strategy_obj.populate_entry_trend(candles)
            exit_trend = self.strategy_obj.populate_exit_trend(candles)
            last_entry = entry_trend[-1] if entry_trend else Signal.HOLD
            last_exit = exit_trend[-1] if exit_trend else Signal.HOLD

            if isinstance(last_exit, Signal):
                last_exit = last_exit.value
            if isinstance(last_entry, Signal):
                last_entry = last_entry.value

            # 卖出优先于买入
            if last_exit == Signal.SELL.value:
                return Signal.SELL, current_rsi, prev_rsi
            if last_entry == Signal.BUY.value:
                return Signal.BUY, current_rsi, prev_rsi
        except Exception:
            pass

        # 回退：RSI 交叉 + 深度超卖双重判断
        if len(closes) < self.rsi_period + 2:
            return Signal.HOLD, current_rsi, prev_rsi

        # RSI 交叉买入：上穿超卖线
        if (current_rsi >= self.oversold and current_rsi > prev_rsi and prev_rsi <= self.oversold):
            return Signal.BUY, current_rsi, prev_rsi
        # RSI 交叉卖出：下穿超买线
        if (current_rsi <= self.overbought and current_rsi < prev_rsi and prev_rsi >= self.overbought):
            return Signal.SELL, current_rsi, prev_rsi
        # 深度超卖兜底：RSI < 20 直接触发买入（避免持续阴跌中永远不买）
        if current_rsi < 20.0 and current_rsi > prev_rsi:
            return Signal.BUY, current_rsi, prev_rsi

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
        pos_side_ai = self.position.get("side", "long") if self.position else "long"
        if self.position and entry_price and pos_side_ai == "long":
            unrealized = (current_price - entry_price) / entry_price * 100
        elif self.position and entry_price and pos_side_ai == "short":
            unrealized = (entry_price - current_price) / entry_price * 100

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

    async def _open_position(self, price: float, timestamp: int, rsi: float, ai_verdict: str,
                             side: str = "buy") -> bool:
        """开仓（已通过风控检查）— 支持多空双向"""
        if self.position is not None:
            return False

        quantity = (self.capital * LIVE_ORDER_CAPITAL_PCT) / price
        if quantity <= 0:
            return False

        is_short = (side == "sell")
        if is_short:
            # 做空：止损价在上方，止盈价在下方
            stop_loss = price * (1 + self.stop_loss_pct)
            take_profit = price * (1 - self.take_profit_pct)
        else:
            stop_loss = price * (1 - self.stop_loss_pct)
            take_profit = price * (1 + self.take_profit_pct)

        # ── 实盘路径：尚书省执行 ──
        if self.shangshu is not None and LIVE_TRADING_ENABLED:
            result = await self.shangshu.execute_open(
                symbol=self.symbol,
                side=side,
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
                f"[{self.agent_id}] === 实盘 {side.upper()} === 价格: ${exec_price:.4f} "
                f"数量: {quantity:.6f} 订单ID: {result.order_id}"
            )
        else:
            # ── 模拟路径（原有逻辑）─
            exec_price = price

        # 市价单成交价回填：exec_price=0 时用信号价代替
        safe_entry = exec_price if exec_price > 0 else price

        self.position = {
            "symbol": self.symbol,
            "entry_price": safe_entry,
            "entry_time": timestamp,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "quantity": quantity,
            "ai_verdict": ai_verdict,
            "entry_rsi": rsi,
            "side": "short" if is_short else "long",
            "is_live": self.shangshu is not None and LIVE_TRADING_ENABLED,
            "order_id": result.order_id if self.shangshu else None,
        }
        # 扣除/增加仓位成本（避免巨额回撤误算）
        position_cost = quantity * safe_entry
        if is_short:
            self.capital += position_cost   # 做空：收到卖出所得
        else:
            self.capital -= position_cost   # 做多：支付买入成本

        conn = sqlite3.connect(DB_PATH)
        _enable_wal(conn)
        c = conn.cursor()
        c.execute("""
            INSERT INTO positions (symbol, timeframe, entry_price, entry_time,
                                   stop_loss, take_profit, quantity, status, side, exchange)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
        """, (self.symbol, self.timeframe, safe_entry, timestamp, stop_loss, take_profit,
              quantity, "short" if is_short else "long", self.exchange))
        conn.commit()
        conn.close()

        # 通知门下省记录
        if self.menxia:
            self.menxia.record_open(
                self.symbol, exec_price, quantity, stop_loss, take_profit,
                side="short" if is_short else "long",
            )

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
        display_side_open = "SELL" if is_short else "BUY"
        if self._feishu and self._feishu_enabled:
            self._feishu.send_position_alert(
                symbol=self.symbol,
                side=display_side_open,
                price=exec_price,
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"AI验证:{ai_verdict}" if ai_verdict else "",
            )

        logger.info(
            "[%s] === %s === 价格: $%.2f  RSI: %.2f  数量: %.6f  止损: $%.2f  止盈: $%.2f  AI:%s",
            self.agent_id, display_side_open, exec_price, rsi, quantity, stop_loss, take_profit, ai_verdict
        )
        return True

    async def _close_position(self, price: float, timestamp: int, reason: str, rsi: float) -> bool:
        """平仓（同步通知门下省）— 支持多空双向"""
        if self.position is None:
            return False

        entry_price = self.position["entry_price"]
        quantity = self.position["quantity"]
        ai_verdict = self.position.get("ai_verdict", "")
        entry_time = self.position.get("entry_time", timestamp)
        pos_side_c = self.position.get("side", "long")

        if pos_side_c == "short":
            # 做空盈亏：价格下跌盈利
            pnl_pct = (entry_price - price) / entry_price * 100
            pnl_abs = quantity * (entry_price - price)
            close_side_c = "buy"   # 平空 = 买入回补
        else:
            pnl_pct = (price - entry_price) / entry_price * 100
            pnl_abs = quantity * (price - entry_price)
            close_side_c = "sell"  # 平多 = 卖出

        # ── 实盘路径：尚书省执行 ──
        if self.shangshu is not None and LIVE_TRADING_ENABLED:
            result = await self.shangshu.execute_close(
                symbol=self.symbol,
                side=close_side_c,
                quantity=quantity,
                order_type="market",
                agent_id=self.agent_id,
                reason=reason,
            )
            if not result.success:
                logger.error(f"[{self.agent_id}] 尚书省实盘平仓失败: {result.message}")
                return False
            exec_price = result.exec_price
            # 市价单成交价回填：exec_price=0 时用信号价（两种路径行为统一）
            if exec_price <= 0:
                exec_price = price
            # 平仓后用真实成交价重新计算 PnL（覆盖之前的预估值）
            if pos_side_c == "short":
                pnl_pct = (entry_price - exec_price) / entry_price * 100
            else:
                pnl_pct = (exec_price - entry_price) / entry_price * 100
            logger.info(
                f"[{self.agent_id}] === 实盘 {close_side_c.upper()} === 价格: ${exec_price:.4f} "
                f"盈亏: {pnl_pct:+.2f}% 订单ID: {result.order_id}"
            )
        else:
            exec_price = price

        if pos_side_c == "short":
            self.capital -= quantity * exec_price
        else:
            self.capital += quantity * exec_price

        conn = sqlite3.connect(DB_PATH)
        _enable_wal(conn)
        c = conn.cursor()
        c.execute("""
            INSERT INTO trades (symbol, timeframe, entry_price, entry_time,
                                exit_price, exit_time, quantity, pnl_pct, pnl_abs, exit_reason, ai_verdict)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.symbol, self.timeframe, entry_price, entry_time,
              exec_price, timestamp, quantity, pnl_pct, pnl_abs, reason, ai_verdict))
        c.execute("UPDATE positions SET status = ? WHERE status = 'open' AND symbol = ?",
                  (reason, self.symbol))
        conn.commit()
        conn.close()

        # 通知门下省更新每日亏损
        if self.menxia:
            self.menxia.record_close(self.symbol, pnl_pct)

        # 保存 trade_id 供后续反思复盘使用（TradeHistory 块内会置 None）
        _reflection_trade_id = self._current_trade_id

        # ── TradeHistory 记录平仓 ──
        if self._trade_history and self._current_trade_id is not None:
            try:
                entry_ts = entry_time
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
        display_close_side_c = "BUY" if pos_side_c == "short" else "SELL"
        if self._feishu and self._feishu_enabled:
            self._feishu.send_position_alert(
                symbol=self.symbol,
                side=display_close_side_c,
                price=exec_price,
                quantity=quantity,
                pnl_pct=pnl_pct,
                reason=reason,
            )

        logger.info(
            "[%s] === %s 平仓 === 价格: $%.2f  盈亏: %+.2f%%  原因: %s  RSI: %.2f",
            self.agent_id,
            display_close_side_c if reason not in ("stop_loss", "止损") else "止损",
            exec_price, pnl_pct, reason, rsi
        )

        self.position = None  # SQL 写入完成后再清空内存引用

        # ── 在线参数优化：每次平仓后评估是否需要调参 ──
        if self._optimizer:
            try:
                opt_result = self._optimizer.maybe_adjust(self.symbol)
                if opt_result.get("adjusted"):
                    for change in opt_result["changes"]:
                        pname = change["param"]
                        pnew = change["new"]
                        if pname == "oversold":
                            self.oversold = pnew
                        elif pname == "overbought":
                            self.overbought = pnew
                        elif pname == "stop_loss":
                            self.stop_loss_pct = pnew
                        elif pname == "take_profit":
                            self.take_profit_pct = pnew
                        elif pname == "rsi_period":
                            self.rsi_period = int(pnew)
                    # 重建策略以应用新参数
                    self.strategy_obj = self._build_strategy(self.strategy_name)
                    logger.info(
                        f"[{self.agent_id}] 参数已自动优化: "
                        f"OS={self.oversold} OB={self.overbought} "
                        f"SL={self.stop_loss_pct:.3f} TP={self.take_profit_pct:.3f}"
                    )
            except Exception as e:
                logger.warning(f"[{self.agent_id}] 在线优化异常: {e}")

        # ── 交易后反思复盘（AI 分析入场/出场逻辑）──
        if _REFLECTION_AVAILABLE and _reflection_trade_id is not None:
            try:
                _ref_svc = ReflectionService(
                    api_key=os.getenv("DEEPSEEK_API_KEY", os.getenv("OPENAI_API_KEY", "")),
                    base_url="https://api.deepseek.com/v1",
                    model="deepseek-chat",
                )
                _ref_svc.reflect_on_trade({
                    "id": _reflection_trade_id,
                    "symbol": self.symbol,
                    "entry_price": entry_price,
                    "exit_price": exec_price,
                    "pnl_pct": pnl_pct,
                    "exit_reason": reason,
                })
            except Exception:
                pass  # 反思失败不影响主流程

        return True

    async def _check_position_risk(self, price: float, timestamp: int, rsi: float) -> bool:
        """检查持仓是否触发止损/止盈（多空双向）"""
        if self.position is None:
            return False

        pos_side_risk = self.position.get("side", "long")
        if pos_side_risk == "short":
            pnl_pct = (self.position["entry_price"] - price) / self.position["entry_price"]
        else:
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

                # ── 策略轮动：根据市场状态自动选择策略 ──
                if self._rotator and regime_state:
                    pick = self._rotator.pick(regime_state)
                    new_strategy = pick["strategy"]
                    if new_strategy != self.strategy_name:
                        logger.info(f"[{self.agent_id}] 策略轮动: {self.strategy_name} → {new_strategy} "
                                    f"({pick['reason']})")
                        self.strategy_name = new_strategy
                        self.strategy_obj = self._build_strategy(new_strategy, pick.get("kwargs", {}))
                        result["strategy"] = new_strategy
                        result["rotation"] = pick
            except Exception as e:
                logger.warning(f"[{self.agent_id}] MarketRegime 获取失败: {e}")

        # 获取 24h 数据用于 AI 过滤器（不覆盖 current_price）
        price_data = self._fetch_price()
        price_change_24h_pct = 0.0
        volume_24h = 0.0
        if price_data is not None:
            # 尝试从实时数据中获取 24h 变化和成交量
            try:
                # 用 _fetch_price 返回的实时价作为 AI 过滤的参考价
                live_price = price_data
                from crypto_api import get_crypto_price
                ticker = get_crypto_price(self.symbol.split("/")[0])
                if isinstance(ticker, dict):
                    price_change_24h_pct = ticker.get("change_24h_pct", 0.0)
                    volume_24h = ticker.get("volume_24h", 0.0)
            except Exception:
                pass

        # ── 黑天鹅保护：BTC 单日跌幅 > BLACK_SWAN_DROP_PCT → 强平所有杠杆仓 ──
        btc_drop_pct = 0.0
        try:
            btc_ticker = get_crypto_price("BTC")
            if isinstance(btc_ticker, dict):
                btc_drop_pct = btc_ticker.get("change_24h_pct", 0.0)
                result["btc_24h_change_pct"] = btc_drop_pct
        except Exception:
            pass

        # 涨跌幅合理性校验：绝对值 > 0.99（即 99%）视为数据异常，丢弃
        if abs(btc_drop_pct) > 0.99:
            logger.warning(f"[{self.agent_id}] BTC 24h涨跌幅数据异常({btc_drop_pct:.4f})，已丢弃")
            btc_drop_pct = 0.0

        if btc_drop_pct < 0 and abs(btc_drop_pct) >= BLACK_SWAN_DROP_PCT:
            logger.warning(f"[{self.agent_id}] ⚠️ 黑天鹅预警：BTC 24h跌幅={btc_drop_pct:.1%}，超过阈值 {BLACK_SWAN_DROP_PCT:.1%}")
            result["black_swan"] = True
            # 有持仓则强平
            if self.position:
                await self._close_position(current_price, current_ts, "black_swan", rsi)
                result["message"] = f"黑天鹅强平：BTC跌幅{btc_drop_pct:.1%}"
            else:
                result["message"] = f"黑天鹅预警：禁止开仓（BTC跌幅{btc_drop_pct:.1%}）"
            result["signal"] = "HOLD"
            return result

        # ── 回撤锁定：总权益回撤 > MAX_DRAWDOWN_LOCK_PCT → 暂停所有新仓 ──
        drawdown_pct = (self.initial_capital - equity) / self.initial_capital
        if self.menxia:
            mx_status = self.menxia.get_status()
            total_dd = mx_status.get("total_drawdown_pct", drawdown_pct)
        else:
            total_dd = drawdown_pct
        if total_dd >= MAX_DRAWDOWN_LOCK_PCT:
            logger.warning(f"[{self.agent_id}] ⚠️ 回撤锁定：总回撤={total_dd:.1%}，超过阈值 {MAX_DRAWDOWN_LOCK_PCT:.1%}")
            result["drawdown_lock"] = True
            result["total_drawdown_pct"] = total_dd
            if self.position is None:
                result["message"] = f"回撤锁定：禁止开仓（回撤{total_dd:.1%}）"
                result["signal"] = "HOLD"
                return result

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
        # ── 初始化决策链变量（避免各分支未赋值导致 UnboundLocalError）──
        routing_info: Dict = {}
        can_open, reason = (False, "")
        trend = ""
        signal_val, _, _ = self._detect_signals(candles)
        signal_names = {Signal.BUY: "BUY", Signal.SELL: "SELL", Signal.HOLD: "HOLD"}
        result["signal"] = signal_names.get(signal_val, "HOLD")

        # ── 多周期信号确认 ──
        mtf_result = {}
        if self._mtf_confirmer and signal_val != Signal.HOLD:
            mtf_result = self._mtf_confirmer.confirm(signal_val, self.timeframe, candles)
            if not mtf_result.get("confirmed", True):
                signal_val = Signal.HOLD  # 多周期否决
                result["signal"] = f"HOLD（MTF否决: {mtf_result.get('reason','')}）"
                result["mtf"] = mtf_result
            else:
                result["mtf"] = mtf_result

        # AI 过滤（仅对 BUY/SELL 有效）
        ai_verdict = ""
        if signal_val != Signal.HOLD:
            filtered_sig, ai_verdict = self._apply_ai_filter(
                signal_val, current_price, rsi, price_change_24h_pct, volume_24h
            )
            result["ai_verdict"] = ai_verdict

            # ── 多空信号处理 ──
            # BUY + 持空 → 平空; BUY + 空仓 → 开多
            # SELL + 持多 → 平多; SELL + 空仓 → 开空
            if filtered_sig == Signal.BUY and self.position is not None and self.position.get("side") == "short":
                # 持有空仓，BUY信号→平空
                await self._close_position(current_price, current_ts, "signal_cover", rsi)
                result["signal"] = "COVER（平空）"
                # position already cleared, continue to equity calc below
            elif filtered_sig == Signal.BUY and self.position is None:
                # === 门下省风控审核（第一优先）===
                can_open, reason = (True, "")
                if self.menxia:
                    review = self.menxia.review_open(
                        symbol=self.symbol,
                        entry_price=current_price,
                        quantity=(self.capital * LIVE_ORDER_CAPITAL_PCT) / current_price,
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
                        quantity = (self.capital * LIVE_ORDER_CAPITAL_PCT) / current_price
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
                    # 趋势过滤：熊市禁止做多
                    trend = (self._current_regime or {}).get("trend", "")
                    if trend == "downtrend":
                        logger.warning(f"[{self.agent_id}] 趋势过滤：熊市禁止做多({self.symbol})")
                        result["signal"] = "HOLD（趋势过滤:熊市禁多）"
                    else:
                        await self._open_position(current_price, current_ts, rsi, ai_verdict, side="buy")
                        result["signal"] = "BUY"
                    if routing_info:
                        result["routing"] = routing_info
            elif filtered_sig == Signal.SELL and self.position is not None and self.position.get("side") == "long":
                # 持有多仓，SELL信号→平多
                await self._close_position(current_price, current_ts, "signal_exit", rsi)
                result["signal"] = "EXIT（平多）"
            elif filtered_sig == Signal.SELL and self.position is None:
                # 空仓，SELL信号→开空（门下省审核）
                can_open_short, reason_short = (True, "")
                if self.menxia:
                    review = self.menxia.review_open(
                        symbol=self.symbol,
                        entry_price=current_price,
                        quantity=(self.capital * LIVE_ORDER_CAPITAL_PCT) / current_price,
                        agent_id=self.agent_id,
                        signal_confidence=0.5,
                        indicators={"rsi": rsi},
                    )
                    can_open_short = review.approved
                    reason_short = review.reason
                if not can_open_short:
                    logger.warning(f"[{self.agent_id}] 门校省否决开空: {reason_short}")
                    result["signal"] = f"门校省否决({reason_short})"
                else:
                    # 趋势过滤：牛市禁止做空
                    trend = (self._current_regime or {}).get("trend", "")
                    if trend == "uptrend":
                        logger.warning(f"[{self.agent_id}] 趋势过滤：牛市禁止做空({self.symbol})")
                        result["signal"] = "HOLD（趋势过滤:牛市禁空）"
                    else:
                        await self._open_position(current_price, current_ts, rsi, ai_verdict, side="sell")
                        result["signal"] = "SHORT"
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

        # ── 决策链结构化摘要日志 ──
        decision_chain = {
            "raw": signal_names.get(signal_val, "HOLD"),
            "ai_filter": ai_verdict or "N/A",
            "menxia": "通过" if can_open else ("N/A" if not reason else f"否决({reason})"),
            "signal_router": routing_info.get("chosen_strategy", "N/A") if routing_info else "N/A",
            "trend_filter": "放行" if not trend else (f"禁多" if trend == "downtrend" else f"禁空" if trend == "uptrend" else trend),
            "final": result.get("signal", "?"),
        }
        logger.info(
            f"[{self.agent_id}] 📊 决策链: "
            f"技术={decision_chain['raw']} → AI={decision_chain['ai_filter']} → "
            f"门下={decision_chain['menxia']} → 路由={decision_chain['signal_router']} → "
            f"趋势={decision_chain['trend_filter']} → 最终={decision_chain['final']}"
        )

        # 持仓状态
        if self.position:
            entry = self.position["entry_price"]
            pos_side_disp = self.position.get("side", "long")
            if pos_side_disp == "short":
                pnl = (entry - current_price) / entry * 100
            else:
                pnl = (current_price - entry) / entry * 100
            result["position"] = {
                "entry_price": entry,
                "current_price": current_price,
                "pnl_pct": pnl,
                "side": pos_side_disp,
                "stop_loss": self.position["stop_loss"],
                "take_profit": self.position["take_profit"],
                "quantity": self.position["quantity"],
            }

        return result

    def _get_equity(self, current_price: float) -> float:
        if self.position:
            pos_side_eq = self.position.get("side", "long")
            qty = self.position["quantity"]
            entry = self.position["entry_price"]
            if pos_side_eq == "short":
                # 资本已含做空所得，权益 = 资本 + 浮动盈亏
                return self.capital + qty * (entry - current_price)
            else:
                # 资本已扣买入成本，权益 = 资本 + 当前市值
                return self.capital + qty * current_price
        return self.capital

    # ── 与交易所持仓同步（每次启动时校正本地DB）─────────────────────────────
    def _sync_exchange_position(self):
        """
        启动时查询交易所实际持仓，对比本地DB并校正。
        防止：用户在交易所手动开仓/平仓后，系统仍从DB读旧数据导致账户不一致。

        实盘模式下读取 frozen 保证金 > 0 → 有持仓；freeze=0 → 无持仓（清空DB）。
        模拟模式（shangshu=None）不做同步，避免误判。
        """
        if self.shangshu is None:
            # 模拟模式，不查交易所
            return

        try:
            balance = self.shangshu.fetch_balance()
            if balance is None:
                logger.warning(f"[{self.agent_id}] 同步持仓失败：无法获取账户余额")
                return

            frozen = balance.get('frozen', 0)
            logger.info(f"[{self.agent_id}] 同步持仓检查：frozen={frozen}")

            if frozen <= 0:
                # 交易所无持仓 → 清空本地DB对应交易对的open记录
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("""
                    UPDATE positions SET status='exchange_closed' 
                    WHERE symbol=? AND status='open' AND exchange=?
                """, (self.symbol, self.exchange))
                conn.commit()
                rows = c.rowcount
                conn.close()
                if rows > 0:
                    logger.warning(f"[{self.agent_id}] 发现{rows}条幽灵持仓已清除（交易所无持仓）")
                # 内存中无持仓
                self.position = None
            else:
                logger.info(f"[{self.agent_id}] 交易所有持仓（frozen={frozen}），保留本地DB数据")
        except Exception as e:
            logger.error(f"[{self.agent_id}] 持仓同步异常：{e}")

    # -------------------- 持久化 --------------------

    def _load_open_position(self):
        """从数据库恢复未平持仓"""
        conn = sqlite3.connect(DB_PATH)
        _enable_wal(conn)
        c = conn.cursor()
        c.execute("""
            SELECT symbol, entry_price, entry_time, stop_loss, take_profit, quantity, exchange, side
            FROM positions WHERE status = 'open' AND symbol = ? ORDER BY id DESC LIMIT 1
        """, (self.symbol,))
        row = c.fetchone()
        conn.close()
        if row:
            side_val = row[7] if len(row) > 7 else "long"
            self.position = {
                "symbol": row[0],
                "entry_price": row[1],
                "entry_time": row[2],
                "stop_loss": row[3],
                "take_profit": row[4],
                "quantity": row[5],
                "exchange": row[6] if len(row) > 6 else "binance",
                "side": side_val,
            }
            # 恢复时扣除/增加持仓成本（根据多空方向）
            entry_price = row[1]
            qty = row[5]
            side_val = row[7] if len(row) > 7 else "long"
            cost = qty * (entry_price if entry_price > 0 else 1.0)
            if side_val == "short":
                self.capital += cost  # 做空：收到卖出所得
            else:
                self.capital -= cost  # 做多：支付买入成本
            logger.info("[%s] 恢复未平持仓: %s 价格 $%.2f  数量 %.6f",
                        self.agent_id, row[0], row[1], row[5])

    def _log_equity(self, timestamp: int, price: float, equity: float, rsi: float):
        conn = sqlite3.connect(DB_PATH)
        _enable_wal(conn)
        c = conn.cursor()
        c.execute("""
            INSERT INTO equity_log (agent_id, timestamp, price, equity, position_value, in_position, rsi)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (self.agent_id, timestamp, price, equity,
              (self.position["quantity"] * (2 * self.position["entry_price"] - price) if self.position.get("side") == "short" else self.position["quantity"] * price) if self.position else 0.0,
              1 if self.position else 0, rsi))
        conn.commit()
        conn.close()

    def _log_signal(self, signal_type: str, price: float, rsi: float, ai_verdict: str):
        conn = sqlite3.connect(DB_PATH)
        _enable_wal(conn)
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
        self._feishu = _feishu if _feishu is not _feishu_sentinel else None
        self._last_trade_time: float = time.time()  # 上次成交时间
        self._idle_alert_hours: float = float(os.getenv("IDLE_ALERT_HOURS", "12"))  # 无交易告警阈值

        # ── 门下省：风控审核服务（所有 Agent 共享）──
        self.menxia: Optional[MenxiaSheng] = None
        if _MENXIA_AVAILABLE:
            # 飞书告警回调（风控等级变化时推送到群）
            def _risk_alert(level: str, msg: str):
                if self._feishu:
                    daily_loss = getattr(self.menxia, '_daily_loss', 0.0) * 100
                    total_exp = 0.0
                    try:
                        status = self.menxia.get_status()
                        total_exp = status.get('total_exposure_pct', 0.0)
                    except Exception:
                        pass
                    self._feishu.send_risk_alert(
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
                # 测试网优先用专用 Key，空则降级到实盘 Key
                api_key_to_use = (LIVE_TESTNET_API_KEY or LIVE_API_KEY)
                api_secret_to_use = (LIVE_TESTNET_API_SECRET or LIVE_API_SECRET)
                shangshu_kwargs = {
                    "exchange": LIVE_EXCHANGE,
                    "api_key": api_key_to_use,
                    "api_secret": api_secret_to_use,
                    "testnet": LIVE_TESTNET,
                    "db_path": DB_PATH,
                }
                if LIVE_EXCHANGE == "weex":
                    shangshu_kwargs["api_passphrase"] = WEEX_API_PASSPHRASE
                self.shangshu = ShangshuSheng(**shangshu_kwargs)
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
            # 第4字段：timeframe（如 2h/4h/1h），默认 4h
            timeframe = parts[3].strip() if len(parts) > 3 else "4h"
            # 自定义公式（FORMULA:名称 语法，第5字段指定公式名）
            custom_formula_str = None
            if len(parts) > 4:
                custom_formula_str = parts[4].strip()

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
            ch_val = params.get("channel_period", 20)
            ema_val = params.get("trend_ema_period", 50)

            agent = TradingAgent(
                agent_id=f"agent_{i+1}",
                symbol=symbol,
                strategy=strategy,
                exchange=exchange,
                timeframe=timeframe,
                rsi_period=rsi_p,
                oversold=os_val,
                overbought=ob_val,
                stop_loss_pct=sl_val,
                take_profit_pct=tp_val,
                initial_capital=LIVE_INITIAL_CAPITAL,
                channel_period=ch_val,
                trend_ema_period=ema_val,
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
                # 跟踪最后成交时间
                if result.get("signal") in ("BUY", "SHORT", "EXIT（平多）", "COVER（平空）"):
                    self._last_trade_time = time.time()
                results.append(result)
            except Exception as e:
                logger.error(f"[{agent.agent_id}] 检查失败: {e}")
                results.append({
                    "agent_id": agent.agent_id,
                    "symbol": agent.symbol,
                    "error": str(e),
                })

        # ── 长时间无交易告警 ──
        idle_hours = (time.time() - self._last_trade_time) / 3600
        if idle_hours >= self._idle_alert_hours:
            logger.warning(
                f"⚠️  IDLE_ALERT: 已 {idle_hours:.1f}h 无交易信号（阈值={self._idle_alert_hours}h），"
                f"请检查 API 连通性与市场状态"
            )

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
            pos_side_ps = s["position"].get("side", "long") if s["position"] else ""
            side_label = f"({'做空' if pos_side_ps == 'short' else '做多'})" if pos_side_ps else ""
            price_str = f"${s['current_price']:.2f}" if s["current_price"] else "N/A"
            print(f"\n  [{s['agent_id']}] {s['symbol']} @ {s['exchange']}  ({s['strategy']})")
            print(f"    当前价格    : {price_str}")
            print(f"    模拟资金    : ${s['capital']:.2f}")
            print(f"    总资产      : ${s['equity']:.2f}  ({s['total_return_pct']:+.2f}%)")
            print(f"    持仓状态    : {pos_info}{side_label}")
            if s["position"]:
                p = s["position"]
                if p.get("side") == "short":
                    pnl = (p["entry_price"] - s["current_price"]) / p["entry_price"] * 100 if s["current_price"] else 0
                else:
                    pnl = (s["current_price"] - p["entry_price"]) / p["entry_price"] * 100 if s["current_price"] else 0
                print(f"    入场价      : ${p['entry_price']:.2f}")
                print(f"    持仓盈亏    : {pnl:+.2f}%")
            total_return += s["total_return_pct"]

        avg_return = total_return / len(status_list) if status_list else 0
        print()
        print(f"  平均收益率  : {avg_return:+.2f}%")
        print("=" * 70)


# ============================================================
# 模块级编排器引用（供 Dashboard 等外部模块访问运行中实例）
# ============================================================

orchestrator = None

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
    global orchestrator
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
        # ── 进程互斥锁：防止双实例并行 ──
        pid_file = os.path.join(os.path.dirname(__file__), ".live_daemon.pid")
        if os.path.exists(pid_file):
            try:
                old_pid = int(open(pid_file).read().strip())
                os.kill(old_pid, 0)
                print(f"❌ 已有 live_trading 进程运行 (PID {old_pid})，拒绝启动")
                print(f"   如需强制重启，请先执行: kill {old_pid}")
                return
            except (OSError, ValueError):
                os.remove(pid_file)
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
        try:
            print(f"多 Agent 后台常驻模式已启动（{len(orchestrator.agents)} 个 Agent）")
            print(f"PID: {os.getpid()}  →  {pid_file}")
            print(f"检查间隔: {AGENT_CHECK_INTERVAL}s")
            print("按 Ctrl+C 停止")
            orchestrator.start_background()
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            print("\n正在停止...")
            orchestrator.stop_background()
        finally:
            try:
                os.remove(pid_file)
            except OSError:
                pass

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