"""
配置文件 - 交易监控系统
"""
import os
from dotenv import load_dotenv

load_dotenv()

# 飞书配置
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")

# Redis 配置
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# 数据库
DB_PATH = os.getenv("DB_PATH", "trading_system.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_TYPE = os.getenv("DB_TYPE", "sqlite")  # "sqlite" 或 "postgresql"

# 加密货币交易所配置（ccxt 统一数据层）
# 支持: binance, gateio, kraken, bitfinex, okx, bybit, bitget, hyperliquid, weex
# 默认 gateio（兼容原系统），可改为 binance 获得更高流动性数据
CRYPTO_EXCHANGE = os.getenv("CRYPTO_EXCHANGE", "gateio")
CRYPTO_API_KEY = os.getenv("CRYPTO_API_KEY", "")
CRYPTO_API_SECRET = os.getenv("CRYPTO_API_SECRET", "")

# Bitget 交易账户 API Key（当 CRYPTO_EXCHANGE=bitget 时使用）
BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_API_SECRET = os.getenv("BITGET_API_SECRET", "")
BITGET_API_PASSPHRASE = os.getenv("BITGET_API_PASSPHRASE", "")

# Weex 交易账户 API Key（当 CRYPTO_EXCHANGE=weex 时使用）
WEEX_API_KEY = os.getenv("WEEX_API_KEY", "")
WEEX_API_SECRET = os.getenv("WEEX_API_SECRET", "")
WEEX_API_PASSPHRASE = os.getenv("WEEX_API_PASSPHRASE", "")

# API 配置
COINGECKO_API = "https://api.coingecko.com/api/v3"
YAHOO_FINANCE_API = "https://query1.finance.yahoo.com"

# 监控配置
PRICE_CHECK_INTERVAL = int(os.getenv("PRICE_CHECK_INTERVAL", "60"))  # 秒
PRICE_CHANGE_THRESHOLD = float(os.getenv("PRICE_CHANGE_THRESHOLD", "0.05"))  # 5%

# ============================================================
# ============================================================
# 策略参数（Grid Search 最优结果，2026-05-03，每标的独立优化）
#
#  标的        RSI_P  OS   OB   SL    TP    Score   收益率  最大回撤  胜率
#  BTC/USDT:  10     28   65   4.0%   8.0%  17.89   +20.11%  4.98%   83.3%
#  ETH/USDT:  14     30   65   2.0%   4.0%  15.55   +24.93%  3.62%   66.7%
#  SOL/USDT:  10     28   65   1.5%   4.0%  11.51   +15.53%  3.86%   55.6%
#  SUI/USDT:  10     28   65   1.2%   2.5%  —       —        —       —  (2026-05-09)
#
# 多策略投票 VOTE：RSI(40%) + MACD(30%) + Bollinger(30%)，阈值 0.3
# 默认单策略参数（用于 VOTE 子策略）：
# ============================================================
STRATEGY_SYMBOL = os.getenv("STRATEGY_SYMBOL", "ETH/USDT")
STRATEGY_TIMEFRAME = os.getenv("STRATEGY_TIMEFRAME", "4h")

# ── 全局默认值（单策略 / VOTE 子策略用）───────────────────
STRATEGY_RSI_PERIOD = int(os.getenv("STRATEGY_RSI_PERIOD", "14"))
STRATEGY_RSI_OVERSOLD = float(os.getenv("STRATEGY_RSI_OVERSOLD", "30.0"))
STRATEGY_RSI_OVERBOUGHT = float(os.getenv("STRATEGY_RSI_OVERBOUGHT", "65.0"))
STRATEGY_STOP_LOSS = float(os.getenv("STRATEGY_STOP_LOSS", "0.030"))   # 3.0% (2026-06-04: 宽松化)
STRATEGY_TAKE_PROFIT = float(os.getenv("STRATEGY_TAKE_PROFIT", "0.04"))  # 4%
STRATEGY_CAPITAL_PCT = float(os.getenv("STRATEGY_CAPITAL_PCT", "1.0"))

# ── 逐标的 Grid Search 最优参数（VOTE 策略时按标的选用）───
OPTIMAL_PARAMS = {
    "BTC/USDT": dict(rsi_period=10, oversold=28.0, overbought=65.0, stop_loss=0.040, take_profit=0.080),
    "ETH/USDT": dict(rsi_period=14, oversold=30.0, overbought=65.0, stop_loss=0.020, take_profit=0.040),
    "SOL/USDT":  dict(rsi_period=10, oversold=28.0, overbought=65.0, stop_loss=0.015, take_profit=0.040,
                      channel_period=25, trend_ema_period=10),  # Donchian 2h 参数补充 (2026-06-07)
    "SUI/USDT":  dict(rsi_period=10, oversold=28.0, overbought=65.0, stop_loss=0.03, take_profit=0.05,
                      channel_period=30, trend_ema_period=10),  # Donchian 2h Grid Search 2026-05-22
    "XAUT/USDT": dict(rsi_period=14, oversold=28.0, overbought=65.0, stop_loss=0.015, take_profit=0.030,
                      channel_period=14, trend_ema_period=30),  # Donchian 2h窄参数 回测2026-05-30: +40.53% 夏普0.99
    # KYVE/USDT, PYTH/USDT: 无交易所历史数据（数据不足0条），暂沿用全局默认值 stop_loss=0.025 take_profit=0.050
}

# ── 自定义通达信公式（用户可在此添加自己的公式）───
# 格式: "公式名": "通达信公式代码"
# 使用方式: AGENT_SYMBOLS 中指定策略为 FORMULA:公式名
# 示例: "MY_RSI": "RSV:=(CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;..."
CUSTOM_FORMULAS = {
    # "MY_MACD": "DIF:=EMA(CLOSE,12)-EMA(CLOSE,26);DEA:=EMA(DIF,9);MACD:=(DIF-DEA)*2;买:CROSS(DIF,DEA);",
}

# ============================================================
# VergeX AI 整合配置
# ============================================================

# --- AI 信号过滤（VergeX AI 多模型架构）---
# 启用后，技术信号会经过 AI 模型宏观验证
# 可选: "deepseek", "openai", "minimax", ""（空=禁用）
AI_MODEL = os.getenv("AI_MODEL", "")
AI_SIGNAL_FILTER_ENABLED = os.getenv("AI_SIGNAL_FILTER_ENABLED", "false").lower() == "true"
AI_CONFIDENCE_THRESHOLD_SUI = float(os.getenv("AI_CONFIDENCE_THRESHOLD_SUI", "0.50"))
AI_CONFIDENCE_THRESHOLD_SOL = float(os.getenv("AI_CONFIDENCE_THRESHOLD_SOL", "0.50"))
AI_CONFIDENCE_THRESHOLD_XAUT = float(os.getenv("AI_CONFIDENCE_THRESHOLD_XAUT", "0.50"))

# --- Hyperliquid 支持（VergeX AI 链上DEX）---
# Hyperliquid 钱包地址（用于签名认证）
HYPERLIQUID_WALLET_ADDRESS = os.getenv("HYPERLIQUID_WALLET_ADDRESS", "")
# 是否优先使用 Hyperliquid（链上DEX，无需交易所API）
USE_HYPERLIQUID = os.getenv("USE_HYPERLIQUID", "false").lower() == "true"

# --- Trade-only API 安全验证 ---
# 是否在启动前验证 API Key 权限为 Trade-only
VALIDATE_TRADE_ONLY = os.getenv("VALIDATE_TRADE_ONLY", "true").lower() == "true"

# --- 多 Agent 并行配置 ---
# 启用多标的策略轮询（每个标的独立运行策略引擎）
MULTI_AGENT_ENABLED = os.getenv("MULTI_AGENT_ENABLED", "false").lower() == "true"
# Agent 检查间隔（秒），默认 60 秒轮询一次所有标的
AGENT_CHECK_INTERVAL = int(os.getenv("AGENT_CHECK_INTERVAL", "60"))

# --- 多 Agent 标的配置（格式：SYMBOL:STRATEGY:EXCHANGE）---
# 策略可选: RSI, SMA, BOLLINGER, MACD, GRID, VOLUME, VOTE, AUTO, DONCHIAN,
#           ATRSTOP, MULTIFACTOR, FUNDING_ARB, STAT_ARB, COINGLASS,
#           FACTOR (Vibe-Trading 因子), SWARM (默认预设),
#           SWARM:preset_name (指定预设，如 SWARM:crypto_trading_desk)
# 交易所可选: binance, gateio, bitget, hyperliquid, weex
# 示例: BTC/USDT:SWARM:crypto_trading_desk:binance,ETH/USDT:FACTOR:binance
AGENT_SYMBOLS = os.getenv(
    "AGENT_SYMBOLS",
    "BTC/USDT:EVR:binance:4h,ETH/USDT:SWARM:derivatives_strategy_desk:binance,SOL/USDT:SWARM:commodity_research_team:binance:2h,SUI/USDT:SWARM:sector_rotation_team:binance:2h,XAUT/USDT:SWARM:portfolio_review_board:gateio:4h"
)

# ============================================================
# 三省六部架构配置（2026-05-02 新增）
# 门下省：风控审核 | 尚书省：执行调度 | 中书省：信号生成
# ============================================================

# --- 尚书省：实盘执行配置 ---
# 是否启用实盘交易（true=真实下单，false=模拟）
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
# 是否启用市场感知策略自动轮动（当当前策略适配度<40且连续HOLD>10次时自动切换）
STRATEGY_AUTO_ROTATE = os.getenv("STRATEGY_AUTO_ROTATE", "false").lower() == "true"
# 实盘交易所：binance / gateio / bybit / bitget / hyperliquid / weex
LIVE_EXCHANGE = os.getenv("LIVE_EXCHANGE", "binance")
# 实盘 API Key（建议使用只读+交易权限的 Trade-only Key）
LIVE_API_KEY     = os.getenv("LIVE_API_KEY", "") or os.getenv("WEEX_API_KEY", "")
LIVE_API_SECRET  = os.getenv("LIVE_API_SECRET", "") or os.getenv("WEEX_API_SECRET", "")
# 测试网 API Key（Bybit/Binance 测试网专用，空则用实盘 Key 自动降级）
LIVE_TESTNET_API_KEY     = os.getenv("LIVE_TESTNET_API_KEY", "")
LIVE_TESTNET_API_SECRET = os.getenv("LIVE_TESTNET_API_SECRET", "")
# 测试网模式（不消耗真实资金，默认 true）
LIVE_TESTNET = os.getenv("LIVE_TESTNET", "true").lower() == "true"
# 测试网时优先使用测试网 Key（空则复用实盘 Key）
def _resolve_testnet_keys():
    if LIVE_TESTNET:
        return (LIVE_TESTNET_API_KEY or LIVE_API_KEY,
                LIVE_TESTNET_API_SECRET or LIVE_API_SECRET)
    return (LIVE_API_KEY, LIVE_API_SECRET)
# 单笔下单金额占比（每次开仓使用资金的 %）
LIVE_ORDER_CAPITAL_PCT = float(os.getenv("LIVE_ORDER_CAPITAL_PCT", "1.0"))
# 实盘初始资金（每个 Agent）
LIVE_INITIAL_CAPITAL = float(os.getenv("LIVE_INITIAL_CAPITAL", "10000.0"))

# --- 门下省：风控审核配置 ---
# 单日亏损 > 5% → CAUTION（禁止开仓）
RISK_MAX_DAILY_LOSS_PCT = float(os.getenv("RISK_MAX_DAILY_LOSS_PCT", "0.05"))
# 单日亏损 > 10% → LOCK（全系统停止）
RISK_MAX_DAILY_LOSS_LOCK = float(os.getenv("RISK_MAX_DAILY_LOSS_LOCK", "0.10"))
# 总持仓暴露度上限（默认 45%，自动均分到各标的）
RISK_MAX_TOTAL_EXPOSURE = float(os.getenv("RISK_MAX_TOTAL_EXPOSURE", "0.45"))
# 单标的持仓上限（默认 15%）
RISK_MAX_POSITION_PER_SYMBOL = float(os.getenv("RISK_MAX_POSITION_PER_SYMBOL", "0.15"))
# 单日最大开仓次数
RISK_MAX_DAILY_TRADES = int(os.getenv("RISK_MAX_DAILY_TRADES", "10"))
# 最大持仓时间（小时），超时强制平仓
RISK_MAX_HOLDING_HOURS = int(os.getenv("RISK_MAX_HOLDING_HOURS", "72"))

# --- 尚书省：通达信公式兼容（预留接口）---
# 通达信公式编译服务地址（未来对接金策智算 tdx/ 模块）
TDX_SERVER_HOST = os.getenv("TDX_SERVER_HOST", "localhost")
TDX_SERVER_PORT = int(os.getenv("TDX_SERVER_PORT", "8765"))

# ============================================================
# 策略1：多因子趋势系统（Multi-Factor Trend Strategy）参数
# ============================================================
MULTIFACTOR_MIN_SCORE      = int(os.getenv("MULTIFACTOR_MIN_SCORE", "65"))       # 最低打分阈值
MULTIFACTOR_EMA_PERIOD     = int(os.getenv("MULTIFACTOR_EMA_PERIOD", "200"))     # EMA周期（200小时）
MULTIFACTOR_ATR_PERIOD     = int(os.getenv("MULTIFACTOR_ATR_PERIOD", "14"))     # ATR周期
MULTIFACTOR_ATR_MULTIPLIER = float(os.getenv("MULTIFACTOR_ATR_MULTIPLIER", "2.5"))  # ATR止损倍数
MULTIFACTOR_MAX_POSITION   = float(os.getenv("MULTIFACTOR_MAX_POSITION", "0.08"))  # 最大单币仓位 8%
MULTIFACTOR_TRAILING_PCT   = float(os.getenv("MULTIFACTOR_TRAILING_PCT", "0.10"))  # 移动止盈回调比例
MULTIFACTOR_STOP_LOSS_PCT  = float(os.getenv("MULTIFACTOR_STOP_LOSS_PCT", "0.12"))  # 止损-12%
MULTIFACTOR_TP1_PCT       = float(os.getenv("MULTIFACTOR_TP1_PCT", "0.25"))    # 第一止盈目标+25%
MULTIFACTOR_TP2_PCT       = float(os.getenv("MULTIFACTOR_TP2_PCT", "0.50"))    # 第二止盈目标+50%
MULTIFACTOR_FUNDING_THRESH = float(os.getenv("MULTIFACTOR_FUNDING_THRESH", "0.0005"))  # 资金费率上限 0.05%

# ============================================================
# 策略2：ATR 止损趋势策略（ATR Stop Trend Strategy）参数
# ============================================================
# 最优参数来自 2026-05-18 Grid Search（80组合，ProcessPoolExecutor 8并发）
# ema_period=10  atr_period=28  atr_multiplier=1.5  →  sharpe=1.23  ret=+1.7%  dd=0.7%
ATRSTOP_EMA_PERIOD     = int(os.getenv("ATRSTOP_EMA_PERIOD", "10"))
ATRSTOP_ATR_PERIOD     = int(os.getenv("ATRSTOP_ATR_PERIOD", "28"))
ATRSTOP_ATR_MULTIPLIER = float(os.getenv("ATRSTOP_ATR_MULTIPLIER", "1.5"))

# ============================================================
# 策略3：资金费率套利（Funding Rate Arbitrage）参数
# ============================================================
FUNDING_ARB_MIN_RATE   = float(os.getenv("FUNDING_ARB_MIN_RATE", "0.0003"))    # 最小资金费率 0.03%
FUNDING_ARB_MAX_RATE   = float(os.getenv("FUNDING_ARB_MAX_RATE", "0.0100"))    # 最大资金费率 1%（避免陷阱）
FUNDING_ARB_REBALANCE_H = int(os.getenv("FUNDING_ARB_REBALANCE_H", "6"))       # 检查间隔（小时）
FUNDING_ARB_TARGET_MONTHLY = float(os.getenv("FUNDING_ARB_TARGET_MONTHLY", "0.015"))  # 月度目标收益 1.5%

# ============================================================
# 策略3：统计套利（Statistical Arbitrage）参数
# ============================================================
STAT_ARB_PAIR_SYMBOL   = os.getenv("STAT_ARB_PAIR_SYMBOL", "ETH")              # 配对标的
STAT_ARB_LOOKBACK      = int(os.getenv("STAT_ARB_LOOKBACK", "30"))            # Z-score回看窗口
STAT_ARB_Z_ENTRY       = float(os.getenv("STAT_ARB_Z_ENTRY", "2.0"))          # 入场Z-score阈值
STAT_ARB_Z_EXIT        = float(os.getenv("STAT_ARB_Z_EXIT", "0.5"))           # 平仓Z-score阈值
STAT_ARB_Z_LOSS        = float(os.getenv("STAT_ARB_Z_LOSS", "3.5"))           # 止损Z-score阈值

# 预设配对列表
STAT_ARB_PAIRS = {
    "BTC-ETH":  ("BTC/USDT", "ETH/USDT"),
    "SOL-AVAX": ("SOL/USDT", "AVAX/USDT"),
    "SOL-NEAR": ("SOL/USDT", "NEAR/USDT"),
}

# ============================================================
# 全局风控补充（黑天鹅保护）
# ============================================================
BLACK_SWAN_DROP_PCT   = float(os.getenv("BLACK_SWAN_DROP_PCT", "0.08"))   # BTC单日跌>8%强平所有杠杆仓
MAX_DRAWDOWN_LOCK_PCT = float(os.getenv("MAX_DRAWDOWN_LOCK_PCT", "0.15"))  # 总资金回撤>15%暂停所有新仓

# ============================================================
# Vibe-Trading 集成配置（v0.1.9 桥接 — 2026-06-02）
# ============================================================

# ── 因子库集成 ──────────────────────────────────────────────
# 是否启用因子策略（从 Vibe-Trading Alpha Zoo 加载 456 个因子）
FACTOR_ENABLED = os.getenv("FACTOR_ENABLED", "true").lower() == "true"
# 默认因子列表（用于 FACTOR 策略类型）
# 格式: zoo.alpha_id，多个用逗号分隔
FACTOR_DEFAULT_ALPHAS = os.getenv(
    "FACTOR_DEFAULT_ALPHAS",
    "alpha101_042,gtja191_006,qlib158_beta10"
).split(",")
# 因子信号 z-score 阈值
FACTOR_THRESHOLD = float(os.getenv("FACTOR_THRESHOLD", "0.5"))
# 因子信号连续确认 K线数
FACTOR_LOOKBACK = int(os.getenv("FACTOR_LOOKBACK", "3"))

# ── Swarm 集成 ──────────────────────────────────────────────
# 是否启用 Swarm 策略（从 Vibe-Trading 29 种预设加载）
SWARM_ENABLED = os.getenv("SWARM_ENABLED", "true").lower() == "true"
# 默认 Swarm 预设（用于 SWARM 策略类型）
SWARM_DEFAULT_PRESET = os.getenv("SWARM_DEFAULT_PRESET", "crypto_trading_desk")
# Swarm 投票阈值
SWARM_THRESHOLD = float(os.getenv("SWARM_THRESHOLD", "0.25"))

# ── Research Goal 集成 ──────────────────────────────────────
# 是否启用研究目标运行时（回测优化审计追踪）
GOAL_ENABLED = os.getenv("GOAL_ENABLED", "true").lower() == "true"
# Goal 数据库路径
GOAL_DB_PATH = os.getenv("GOAL_DB_PATH", "")   # 空=使用默认路径

# ============================================================
# AGENT_SYMBOLS 统一解析（Dashboard 和 live_trading 共用，避免字段串位）
# ============================================================

_KNOWN_EXCHANGES = {"binance", "gateio", "weex", "okx", "bybit", "bitget", "hyperliquid"}

def parse_agent_config_list(agent_symbols_str: str = None):
    """解析 AGENT_SYMBOLS 返回 Agent 配置列表。
    支持: SYMBOL:STRATEGY:EXCHANGE:TIMEFRAME 或 SYMBOL:SWARM:preset:EXCHANGE:TIMEFRAME
    """
    if agent_symbols_str is None:
        agent_symbols_str = AGENT_SYMBOLS
    results = []
    for i, item in enumerate(agent_symbols_str.split(",")):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        sym = parts[0].strip()
        raw_strategy = parts[1].strip().upper() if len(parts) > 1 else "RSI"

        swarm_preset = None
        if raw_strategy == "SWARM" and len(parts) > 2:
            if parts[2].strip().lower() not in _KNOWN_EXCHANGES:
                swarm_preset = parts[2].strip()

        strat = f"SWARM:{swarm_preset}" if swarm_preset else raw_strategy
        ex_idx = 3 if swarm_preset else 2
        tf_idx = 4 if swarm_preset else 3
        exch = parts[ex_idx].strip().lower() if len(parts) > ex_idx else "binance"
        tf = parts[tf_idx].strip() if len(parts) > tf_idx else "4h"

        results.append({
            "agent": f"agent_{i+1}",
            "symbol": sym,
            "strategy": strat,
            "exchange": exch,
            "timeframe": tf,
        })
    return results