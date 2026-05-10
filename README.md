# Trading System · 交易系统

**三省六部制**量化交易系统，支持加密货币与股票的双市场回测 / 模拟 / 实盘交易。核心理念：信号生成（**中书省**）、风控审核（**门下省**）、执行调度（**尚书省**）三层分离，规则硬编码、逻辑可审计。

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                    中书省 · 信号生成层                         │
│   RSI / MACD / Bollinger Bands / VOTE 多策略投票 / 公式策略    │
│   通达信公式编译器（TDX → Python）                              │
├──────────────────────────────────────────────────────────────┤
│                    门下省 · 风控审核层                         │
│   仓位/频率/EMA过滤/成交量/涨跌停/连错上限  8条规则一票否决       │
├──────────────────────────────────────────────────────────────┤
│                    尚书省 · 执行调度层                         │
│   Binance / Gate.io / Weex / OKX / Bybit / Hyperliquid / Alpaca / Tiger │
├──────────────────────────────────────────────────────────────┤
│  刑部 · 违规记录（SQLite）   户部 · 权益曲线   仓部 · 持仓管理   │
└──────────────────────────────────────────────────────────────┘
```

---

## 功能一览

| 功能 | 说明 |
|------|------|
| **多交易所** | Binance / Gate.io / Weex / OKX / Bybit / Hyperliquid / Alpaca / Tiger |
| **加密货币策略** | RSI / MACD / Bollinger Bands / VOTE 多策略投票 / 自定义公式 |
| **股票策略** | A股均线交叉 + RSI / 港股 / 美股 |
| **通达信公式** | TDX 公式字符串 → Python 可执行函数（Lexing / Parsing / Evaluation） |
| **Grid Search** | 参数批量优化，RSI_period / oversold / overbought / stop_loss / take_profit |
| **实盘交易** | CCXT 统一接口，模拟盘/实盘切换（需 AGENT_TOKEN） |
| **Dashboard** | Web 可视化（K线、持仓、权益曲线、买卖点标注、热力图、审计报告） |
| **飞书告警** | 持仓变化、价格异动实时推送 |
| **OHLCV 缓存** | SQLite 本地缓存（TTL=1天），避免重复调接口 |
| **MCP Server** | Model Context Protocol 服务（stdio 模式） |
| **Agent Gateway** | Agent Token 鉴权，敏感操作受保护 |
| **市场状态识别** | 趋势 / 震荡 / 高波动 多状态切换策略权重 |
| **Reflection Agent** | 交易审计报告，自动分析亏损原因 |
| **在线参数优化** | 每笔平仓后评估表现，自动微调 RSI/止损/止盈（24h冷却） |
| **策略轮动** | 根据市场状态（趋势/波动）自动切换最优策略 |
| **多周期确认** | 1h/4h/1d 信号一致性验证，不一致则否决 |
| **Weex v3 API** | 独立 REST API 适配器（HMAC-SHA256 Base64 签名） |

---

## 目录结构

```
trading-system/
├── config.py                     # 全局配置（环境变量模式，无硬编码密钥）
├── dashboard.py                  # Web Dashboard（FastAPI + Lightweight Charts，1919行）
├── run_dashboard.sh              # 标准化启动脚本（自动生成 AGENT_TOKEN）
│
├── live_trading.py               # 实盘引擎（三省六部制，1472行）
├── shangshu_sheng.py             # 尚书省 · 执行调度层（652行）
├── menxia_sheng.py               # 门下省 · 风控审核层（498行）
│
├── components/                    # 重构后的模块化组件
│   ├── signal_engine.py          # 信号引擎（RSI/SMA/MACD/BOLL/VOTE/Formula，435行）
│   ├── position_manager.py       # 仓位管理器（开仓/平仓/止损/DB记录，257行）
│   ├── signal_router.py          # 信号路由（分发到对应交易所适配器）
│   ├── market_regime.py          # 市场状态识别（趋势/震荡/高波动，383行）
│   ├── auditor.py                # Reflection Agent 审计（433行）
│   ├── online_optimizer.py       # 在线参数自动优化（363行）
│   ├── strategy_rotator.py       # 市场状态感知策略轮动
│   ├── mtf_confirmer.py          # 多周期信号确认
│   ├── correlation_guard.py      # 相关性风控
│   ├── position_sizer.py         # 动态仓位计算
│   └── __init__.py
│
├── agent_gateway/                 # Agent Gateway（Token 鉴权）
│   ├── fastapi_routes.py        # 敏感操作受 AGENT_TOKEN 保护（205行）
│   └── __init__.py
│
├── mcp_server/                    # MCP Server（Model Context Protocol）
│   ├── trading_mcp.py           # stdio 模式，支持工具调用（255行）
│   └── __init__.py
│
├── vibe_integration/               # Vibe-Trading 股票回测集成
│   └── stock_backtest.py        # 多市场回测引擎（TTL=1天 SQLite 缓存，1222行）
│
├── stock_data/                     # 股票数据层
│   ├── stock_api.py              # A股/港股/美股统一数据接口（170行）
│   └── __init__.py
│
├── stock_trading/                  # 股票券商适配器
│   ├── unified_trader.py         # 统一交易接口（613行）
│   ├── trading_api.py            # AlpacaTrader + TigerTrader
│   └── __init__.py
│
├── batch_backtest.py               # 批量回测 + Grid Search 参数优化
├── backtest.py                     # 单标的回测引擎
├── strategies.py                   # 策略实现（RSI / MACD / Bollinger）
├── crypto_api.py                   # CCXT 封装
├── trade_history.py                # 交易历史记录
├── portfolio.py                    # 持仓管理
├── monitor.py                      # 价格监控
├── risk_manager.py                  # 风险管理器
├── multi_strategy_vote.py           # 多策略投票聚合器
├── signal_review.py                 # 信号复盘分析
├── formula_tester.py                # 公式策略回测工具
├── tdx_compiler.py                  # 通达信公式编译器（Lexing/Parsing/Evaluation）
├── test_tdx_compiler.py             # TDX 编译器单元测试
├── history_cache.py                 # 历史数据缓存管理
├── weex.py                          # Weex v3 API 适配器（独立 REST，HMAC-SHA256）
├── database.py                      # SQLite 数据库（公共模块）
│
├── trading_system.db               # 主数据库（成交记录 + 违规记录）
├── live_trading.db                # 实盘数据库（权益日志）
├── market_regime.db                # 市场状态数据库
├── signal_review.db                # 信号复盘数据库
├── history_cache.db                # 历史数据缓存
├── ohlcv_cache/                    # OHLCV 缓存文件
│
├── .gitignore                      # Git 忽略规则（*.db / .env 等）
├── .agent_token                    # 自动生成的 AGENT_TOKEN（不进入 Git）
├── requirements.txt                # Python 依赖
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
CRYPTO_API_KEY=***
CRYPTO_API_SECRET=***

# Agent Gateway（自动生成，无需手动配置）
# AGENT_TOKEN=***  →  .agent_token 文件

# 飞书告警（可选）
FEISHU_APP_ID=your_feishu_app_id
FEISHU_APP_SECRET=your_feishu_secret
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

## Dashboard 功能详情

Dashboard 为单页应用（1919行），集成以下功能模块：

### Tab 1：📈 实时行情
- 市场切换：A股 / 港股 / 美股 / 加密货币
- 指数卡片：上证/深证/创业板、恒生、纳斯达克/道琼斯、比特币/以太坊
- 实时价格：涨跌颜色、百分比变化
- 持仓状态：多空方向、持仓数量、浮盈浮亏

### Tab 2：💼 持仓管理
- 投资组合总览
- 各标的持仓明细
- 权益曲线（Equity Curve）

### Tab 3：💰 交易操作
- 实盘/模拟盘切换（受 AGENT_TOKEN 保护）
- 下单接口（支持多交易所）

### Tab 4：🔬 策略回测（复盘）
- 综合绩效统计（胜率/盈亏比/最大回撤/夏普比率）
- 策略 × 市场状态 热力图
- 出场原因分析
- 交易历史记录
- **Reflection Agent 审计报告**：自动分析亏损原因并给出改进建议

---

## Dashboard API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Dashboard 首页 |
| `/dashboard` | GET | Dashboard 主页面 |
| `/api/status` | GET | 系统状态（三省六部各层状态） |
| `/api/positions` | GET | 当前持仓 |
| `/api/portfolio` | GET | 投资组合摘要 |
| `/api/trading/mode` | POST | 切换模拟盘/实盘（**需 AGENT_TOKEN**） |
| `/api/stock/chart` | GET | 股票K线 + 指标数据（TTL缓存） |
| `/api/replay/kpi` | GET | 回测绩效 KPI |
| `/api/replay/heatmap` | GET | 策略 × 市场热力图 |
| `/api/replay/trades` | GET | 交易历史（分页） |
| `/api/replay/audit` | GET | Reflection Agent 审计报告 |
| `/api/agent/v1/*` | ANY | Agent Gateway 路由（**需 AGENT_TOKEN**） |

---

## 已优化标的（Grid Search 最优参数）

| 标的 | RSI_P | Oversold | Overbought | StopLoss | TakeProfit | Score |
|------|-------|----------|------------|---------|------------|-------|
| BTC/USDT | 10 | 18 | 65 | 4% | 8% | 17.89 |
| ETH/USDT | 14 | 28 | 65 | 2% | 4% | 15.55 |
| SOL/USDT | 10 | 20 | 65 | 1.5% | 4% | 11.51 |
| SUI/USDT | （待优化） | | | | | |

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
| **Weex** | ✅ v3 | 独立 REST API 适配器，HMAC-SHA256 Base64 |

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

## 通达信公式编译器

系统内置 TDX（通达信）公式字符串编译器，可将公式转换为 Python 可执行函数：

```python
from tdx_compiler import compile_formula

formula_str = "MA(CLOSE,5)>MA(CLOSE,20) AND RSI(14)<30"
func = compile_formula(formula_str)
result = func(ohlcv_data)  # 返回 True/False
```

支持函数：MA / EMA / RSI / MACD / Bollinger Bands / KD / ATR / VOL / MAX / MIN / ABS / IF

---

## 安全说明

- **AGENT_TOKEN**：实盘切换和敏感操作强制验证，通过 `run_dashboard.sh` 自动生成
- **无硬编码密钥**：所有 API Key / Secret / Token 均通过环境变量注入
- **实盘保护**：未配置 `AGENT_TOKEN` 时，实盘切换接口返回 503
- **Token 存储**：`AGENT_TOKEN` 保存于 `.agent_token` 文件（权限 600），不进入 Git
- **数据库隔离**：所有 `.db` 文件通过 `.gitignore` 排除，不进入版本控制

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

**通达信公式编译报错？**
```bash
python3 test_tdx_compiler.py  # 运行单元测试验证
```
