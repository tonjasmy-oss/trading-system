# Trading System
量化交易系统，采用**三省六部制**架构，支持加密货币与股票的双市场回测/模拟/实盘交易。

---

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    中书省 · 信号生成层                    │
│   RSI / MACD / Bollinger Bands / VOTE 多策略投票 / 公式策略  │
├─────────────────────────────────────────────────────────┤
│                    门下省 · 风控审核层                    │
│   仓位/频率/EMA过滤/成交量/涨跌停/连错上限 8条规则一票否决   │
├─────────────────────────────────────────────────────────┤
│                    尚书省 · 执行调度层                    │
│   Binance / Gate.io / Hyperliquid / Alpaca / Tiger 多交易所│
├─────────────────────────────────────────────────────────┤
│         刑部 · 违规与成交记录（SQLite）                   │
│         户部 · 权益曲线追踪                              │
└─────────────────────────────────────────────────────────┘
```

---

## 功能一览

| 功能 | 说明 |
|------|------|
| **多交易所** | Binance / Gate.io / OKX / Bybit / Hyperliquid / Alpaca / Tiger |
| **加密货币策略** | RSI / MACD / Bollinger Bands / VOTE 多策略投票 / 自定义公式 |
| **股票策略** | A股均线交叉/RSI / 港股 / 美股 |
| **Grid Search** | 参数批量优化，RSI_period / oversold / overbought / stop_loss / take_profit |
| **实盘交易** | CCXT 统一接口，模拟盘/实盘切换（需 AGENT_TOKEN） |
| **Dashboard** | Web 可视化（K线、持仓、权益曲线、买卖点标注） |
| **飞书告警** | 持仓变化、价格异动实时推送 |
| **缓存层** | SQLite OHLCV 缓存（TTL=1天），避免重复调接口 |
| **MCP Server** | Model Context Protocol 服务（stdio 模式） |
| **Agent Gateway** | Agent Token 鉴权，敏感操作受保护 |

---

## 目录结构

```
trading-system/
├── config.py                  # 全局配置（环境变量模式，无硬编码密钥）
├── dashboard.py               # Web Dashboard（FastAPI + Lightweight Charts）
├── run_dashboard.sh           # 标准化启动脚本（自动生成 AGENT_TOKEN）
│
├── live_trading.py            # 实盘引擎（三省六部制，1211行）
├── shangshu_sheng.py          # 尚书省 · 执行调度层（多交易所 CCXT 封装）
├── menxia_sheng.py            # 门下省 · 风控审核层（8条规则）
│
├── components/                # 重构后的模块化组件（新增）
│   ├── signal_engine.py       # 信号引擎（RSI/SMA/MACD/BOLL/VOTE/Formula）
│   ├── position_manager.py    # 仓位管理器（开仓/平仓/止损/DB记录）
│   └── __init__.py
│
├── vibe_integration/          # Vibe-Trading 股票回测集成
│   └── stock_backtest.py     # 多市场回测引擎（TTL=1天 SQLite 缓存）
│
├── stock_data/                # 股票数据层
│   └── stock_api.py          # A股/港股/美股统一数据接口
│
├── stock_trading/             # 股票券商适配器
│   ├── trading_api.py         # AlpacaTrader + TigerTrader
│   └── unified_trader.py     # 统一交易接口
│
├── agent_gateway/             # Agent Gateway（Token 鉴权）
│   └── fastapi_routes.py     # 敏感操作受 AGENT_TOKEN 保护
│
├── mcp_server/                # MCP Server（Model Context Protocol）
│   └── trading_mcp.py        # stdio 模式，支持工具调用
│
├── batch_backtest.py          # 批量回测 + Grid Search 参数优化
├── backtest.py                # 单标的回测引擎
├── strategies.py              # 策略实现（RSI / MACD / Bollinger）
├── crypto_api.py              # CCXT 封装
├── database.py                # SQLite 数据库
├── feishu_alert.py            # 飞书告警
├── monitor.py                 # 价格监控
├── portfolio.py               # 持仓管理
│
├── trading_system.db          # 主数据库（成交记录 + 违规记录）
├── live_trading.db            # 实盘数据库（权益日志）
├── ohlcv_cache/               # OHLCV 缓存（TTL=1天）
│
├── requirements.txt           # Python 依赖
└── README.md
```

---

## 安装部署

### 环境要求
- Python >= 3.11
- SQLite（内置，无需安装）
- 交易所 API Key（实盘必需）

### 快速启动

```bash
# 克隆
git clone https://github.com/tonjasmy-oss/trading-system.git
cd trading-system

# 安装依赖
pip install -r requirements.txt

# 或安装核心依赖（推荐）
pip install ccxt pandas numpy fastapi uvicorn akshare aiohttp python-dotenv

# 启动 Dashboard（自动生成 AGENT_TOKEN）
bash run_dashboard.sh
# 访问 http://localhost:8081
```

### 环境变量配置

```env
# 交易所
CRYPTO_EXCHANGE=gateio
CRYPTO_API_KEY=your_api_key
CRYPTO_API_SECRET=your_api_secret

# Agent Gateway（自动生成，无需手动配置）
# AGENT_TOKEN=自动生成于 .agent_token 文件

# 飞书告警（可选）
FEISHU_APP_ID=your_feishu_app_id
FEISHU_APP_SECRET=your_feishu_app_secret
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# 风险控制
PRICE_CHECK_INTERVAL=60
PRICE_CHANGE_THRESHOLD=0.05

# 实盘开关（默认 False）
LIVE_TRADING_ENABLED=false
LIVE_TESTNET=true
```

---

## 运行

### Dashboard（Web 界面）
```bash
bash run_dashboard.sh
# 访问 http://localhost:8081
```

### 加密货币回测
```bash
# Grid Search 最优参数
python3 batch_backtest.py --grid-search --symbols BTC ETH SOL SUI

# 单标的回测
python3 backtest.py --symbol ETH/USDT --timeframe 4h
```

### 股票回测（A股 / 港股 / 美股）
```bash
# A股 — 均线交叉策略（浦发银行 + 平安银行）
python3 backtest.py --codes 600000.SH,000001.SZ \
  --start 2024-01-01 --end 2025-01-01 --strategy ma_cross --fast 5 --slow 20

# 港股 — 腾讯控股
python3 backtest.py --codes 00700.HK \
  --start 2024-01-01 --end 2025-01-01 --strategy ma_cross

# 美股 — Apple RSI策略
python3 backtest.py --codes AAPL \
  --start 2024-01-01 --end 2025-01-01 --strategy rsi --rsi-period 14
```

### 实盘交易
```bash
# 模拟盘（默认）
python3 live_trading.py --symbol ETH/USDT --exchange binance

# 实盘（需配置 AGENT_TOKEN）
LIVE_TRADING_ENABLED=true LIVE_TESTNET=false bash run_dashboard.sh --live
```

---

## Dashboard API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Dashboard 首页 |
| `/api/status` | GET | 系统状态（三省六部各层状态） |
| `/api/positions` | GET | 当前持仓 |
| `/api/portfolio` | GET | 投资组合摘要 |
| `/api/trading/mode` | POST | 切换模拟盘/实盘（**需 AGENT_TOKEN**） |
| `/api/stock/chart` | GET | 股票K线 + 指标数据（TTL缓存） |
| `/api/agent/v1/*` | ANY | Agent Gateway 路由（**需 AGENT_TOKEN**） |

---

## 已优化标的（Grid Search 最优参数）

| 标的 | RSI_P | Oversold | Overbought | StopLoss | TakeProfit | Score |
|------|-------|----------|------------|---------|------------|-------|
| BTC/USDT | 10 | 18 | 65 | 4% | 8% | 17.89 |
| ETH/USDT | 14 | 28 | 65 | 2% | 4% | 15.55 |
| SOL/USDT | 10 | 20 | 65 | 1.5% | 4% | 11.51 |
| SUI/USDT | (待优化) | | | | | |

---

## 支持的交易所

### 加密货币
| 交易所 | 状态 | 备注 |
|--------|------|------|
| Binance | ✅ | 推荐实盘 |
| Gate.io | ✅ | 默认数据源 |
| OKX | ✅ | |
| Bybit | ✅ | |
| Hyperliquid | ✅ | |
| Kraken | ✅ | |
| Bitfinex | ✅ | |

### 股票
| 市场 | 数据源 | 券商 | 规则 |
|------|--------|------|------|
| A股 | akshare（免费）| TigerTrader | T+1, 印花税0.05% |
| 港股 | akshare（免费）| TigerTrader | T+0, 印花税0.1%双边 |
| 美股 | yfinance（免费）| AlpacaTrader | T+0, 零佣金, 分数股 |

---

## 策略说明

| 策略 | 说明 |
|------|------|
| **RSI** | 相对强弱指标，Oversold 买入 / Overbought 卖出 |
| **MACD** | 指数平滑移动平均线，金叉买入/死叉卖出 |
| **Bollinger Bands** | 布林带突破，下轨买入/上轨卖出 |
| **VOTE** | RSI(40%) + MACD(30%) + Bollinger(30%) 多策略投票 |
| **ma_cross** | 均线交叉，快线金叉买入/死叉卖出 |
| **Formula** | 自定义公式，TDX 公式字符串 → Python 函数 |

---

## 安全说明

- **AGENT_TOKEN**：实盘切换和敏感操作强制验证，通过 `run_dashboard.sh` 自动生成
- **无硬编码密钥**：所有 API Key / Secret / Token 均通过环境变量注入
- **实盘保护**：未配置 `AGENT_TOKEN` 时，实盘切换接口返回 503
- **Token 存储**：`AGENT_TOKEN` 保存于 `.agent_token` 文件（权限 600），不进入 Git

---

## A股 T+1 注意事项

A股实行 T+1 制度，当日买入次日才能卖出。策略需考虑：
- 买入信号产生后，次日才能执行买入
- 止损/止盈按自然日计算，非按买入当日

---

⚠️ **风险提示**：量化交易存在风险，请先用模拟盘测试，确认策略有效后再使用实盘。

---

## 故障排除

**akshare 导入失败？**
```bash
pip install akshare
```

**CCXT 导入失败？**
```bash
pip install ccxt
```

**数据库权限问题？**
```bash
chmod 666 trading_system.db live_trading.db
```

**Dashboard 端口被占用？**
```bash
# 修改端口，编辑 dashboard.py 中的
# uvicorn dashboard:app --host 0.0.0.0 --port 8082
```
