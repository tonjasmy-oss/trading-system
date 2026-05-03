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

# 加密货币交易所配置（ccxt 统一数据层）
# 支持: binance, gateio, kraken, bitfinex, okx, bybit, bitget, hyperliquid
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
#  BTC/USDT:  10     18   65   4%    8%    17.89   +20.11%  4.98%  83.3%
#  ETH/USDT:  14     28   65   2%    4%    15.55   +24.93%  3.62%  66.7%
#  SOL/USDT:  10     20   65   1.5%  4%    11.51   +15.53%  3.86%  55.6%
#
# 多策略投票 VOTE：RSI(40%) + MACD(30%) + Bollinger(30%)，阈值 0.3
# 默认单策略参数（用于 VOTE 子策略）：
# ============================================================
STRATEGY_SYMBOL = os.getenv("STRATEGY_SYMBOL", "ETH/USDT")
STRATEGY_TIMEFRAME = os.getenv("STRATEGY_TIMEFRAME", "4h")

# ── 全局默认值（单策略 / VOTE 子策略用）───────────────────
STRATEGY_RSI_PERIOD = int(os.getenv("STRATEGY_RSI_PERIOD", "14"))
STRATEGY_RSI_OVERSOLD = float(os.getenv("STRATEGY_RSI_OVERSOLD", "28.0"))
STRATEGY_RSI_OVERBOUGHT = float(os.getenv("STRATEGY_RSI_OVERBOUGHT", "65.0"))
STRATEGY_STOP_LOSS = float(os.getenv("STRATEGY_STOP_LOSS", "0.020"))   # 2.0%
STRATEGY_TAKE_PROFIT = float(os.getenv("STRATEGY_TAKE_PROFIT", "0.04"))  # 4%
STRATEGY_CAPITAL_PCT = float(os.getenv("STRATEGY_CAPITAL_PCT", "1.0"))

# ── 逐标的 Grid Search 最优参数（VOTE 策略时按标的选用）───
OPTIMAL_PARAMS = {
    "BTC/USDT": dict(rsi_period=10, oversold=18.0, overbought=65.0, stop_loss=0.040, take_profit=0.080),
    "ETH/USDT": dict(rsi_period=14, oversold=28.0, overbought=65.0, stop_loss=0.020, take_profit=0.040),
    "SOL/USDT":  dict(rsi_period=10, oversold=20.0, overbought=65.0, stop_loss=0.015, take_profit=0.040),
    "SUI/USDT":  dict(rsi_period=6,  oversold=22.0, overbought=70.0, stop_loss=0.040, take_profit=0.080),  # Grid Search 2026-05-03 Score=5.66 Ret=33.49% DD=25.63% WR=50.0%
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
# 策略可选: RSI, SMA, BOLLINGER, MACD, GRID, VOLUME
# 交易所可选: binance, gateio, bitget, hyperliquid
# 示例: ETH/USDT:RSI:binance,SOL/USDT:RSI:hyperliquid,SUI/USDT:SMA:binance
AGENT_SYMBOLS = os.getenv(
    "AGENT_SYMBOLS",
    "BTC/USDT:VOTE:binance,ETH/USDT:VOTE:binance,SOL/USDT:VOTE:binance,SUI/USDT:VOTE:binance,ARB/USDT:VOTE:binance,AVAX/USDT:VOTE:binance,OP/USDT:VOTE:binance,LINK/USDT:VOTE:binance"
)

# ============================================================
# 三省六部架构配置（2026-05-02 新增）
# 门下省：风控审核 | 尚书省：执行调度 | 中书省：信号生成
# ============================================================

# --- 尚书省：实盘执行配置 ---
# 是否启用实盘交易（true=真实下单，false=模拟）
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
# 实盘交易所：binance / gateio / bybit / bitget / hyperliquid
LIVE_EXCHANGE = os.getenv("LIVE_EXCHANGE", "binance")
# 实盘 API Key（建议使用只读+交易权限的 Trade-only Key）
LIVE_API_KEY = os.getenv("LIVE_API_KEY", "")
LIVE_API_SECRET = os.getenv("LIVE_API_SECRET", "")
# 测试网模式（不消耗真实资金）
LIVE_TESTNET = os.getenv("LIVE_TESTNET", "true").lower() == "true"
# 单笔下单金额占比（每次开仓使用资金的 %）
LIVE_ORDER_CAPITAL_PCT = float(os.getenv("LIVE_ORDER_CAPITAL_PCT", "1.0"))
# 实盘初始资金（每个 Agent）
LIVE_INITIAL_CAPITAL = float(os.getenv("LIVE_INITIAL_CAPITAL", "10000.0"))

# --- 门下省：风控审核配置 ---
# 单日亏损 > 5% → CAUTION（禁止开仓）
RISK_MAX_DAILY_LOSS_PCT = float(os.getenv("RISK_MAX_DAILY_LOSS_PCT", "0.05"))
# 单日亏损 > 10% → LOCK（全系统停止）
RISK_MAX_DAILY_LOSS_LOCK = float(os.getenv("RISK_MAX_DAILY_LOSS_LOCK", "0.10"))
# 总持仓暴露度上限（默认 30%）
RISK_MAX_TOTAL_EXPOSURE = float(os.getenv("RISK_MAX_TOTAL_EXPOSURE", "0.30"))
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
