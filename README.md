# Trading System · 交易系统

**三省六部制**量化交易系统，支持加密货币与股票的双市场回测 / 模拟 / 实盘交易。核心理念：信号生成（**中书省**）、风控审核（**门下省**）、执行调度（**尚书省**）三层分离，规则硬编码、逻辑可审计。

> **v3 更新（2026-06-01）**：借鉴 QuantDinger 进行了 7 项增强 —— 市场状态感知策略推荐、实验管线、分层策略架构、StrategySpec JSON 编译器、多通道通知、MCP Server v2。

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                    中书省 · 信号生成层                         │
│   RSI / MACD / Bollinger / DONCHIAN / VOTE / ATRSTOP / KDJ   │
│   分层策略 (LayeredStrategy)  ·  StrategySpec JSON 编译器      │
├──────────────────────────────────────────────────────────────┤
│                    门下省 · 风控审核层                         │
│   11条风控规则 · 四级递进 (NORMAL→CAUTION→WARNING→LOCKED)      │
│   市场状态感知 · 策略适配度评分 · 💡 自动推荐                   │
├──────────────────────────────────────────────────────────────┤
│                    尚书省 · 执行调度层                         │
│   Binance / Gate.io / Weex / OKX / Bybit / Hyperliquid        │
├──────────────────────────────────────────────────────────────┤
│  刑部 · 违规记录   户部 · 权益曲线   仓部 · 持仓管理             │
│  实验管线 · 策略寻优   MCP Server v2 · 多通道通知               │
└──────────────────────────────────────────────────────────────┘
```

---

## 功能一览

| 功能 | 说明 |
|------|------|
| **多交易所** | Binance / Gate.io / Weex / OKX / Bybit / Hyperliquid |
| **11种策略** | RSI / MACD / Bollinger / DONCHIAN / SMA / KDJ / ATRSTOP / MULTIFACTOR / VOTE / FUNDING_ARB / STAT_ARB |
| **分层策略架构** | 指标层 → 信号层 → 风险层 三层分离，AI 可生成 |
| **策略实验管线** | Regime → Generate → Backtest → Score(6因子) → Best 闭环 |
| **StrategySpec** | JSON 策略规格 → Python 类自动编译 |
| **市场状态感知** | 9种状态 × 策略适配度(0-100) + 💡 策略推荐 + 自动轮动 |
| **Grid Search** | 参数批量优化，支持所有策略类型 |
| **实盘交易** | CCXT 统一接口，门下省一票否决制风控 |
| **Dashboard** | Web 可视化（K线、持仓、权益曲线、回测对比、审计报告） |
| **多通道通知** | 飞书 / Telegram / Discord / Email / Webhook |
| **MCP Server v2** | 17 tools + 审计日志 + SSE/HTTP/stdio 三传输 |
| **通达信公式** | TDX 公式字符串 → Python 可执行函数 |
| **OHLCV 缓存** | SQLite 本地缓存（TTL=1天） |
| **DataProvider** | 统一数据抽象层（限流+熔断） |
| **在线参数优化** | 每笔平仓后自动微调参数（24h冷却） |
| **多周期确认** | 1h/4h/1d 信号一致性验证 |

---

## 目录结构

```
trading-system/
├── config.py                     # 全局配置（环境变量模式）
├── dashboard.py                  # Web Dashboard（FastAPI，含实验管线API）
├── live_trading.py               # 实盘引擎（三省六部制，多Agent编排）
├── shangshu_sheng.py             # 尚书省 · 执行调度层
├── menxia_sheng.py               # 门下省 · 风控审核层（11条规则，四级递进）
│
├── components/                    # 模块化组件
│   ├── experiment_pipeline.py    # [v3] 策略实验管线（530行）
│   ├── layered_strategy.py       # [v3] 分层策略架构（451行）
│   ├── strategy_spec.py          # [v3] StrategySpec JSON 编译器（405行）
│   ├── signal_engine.py          # 信号引擎
│   ├── market_regime.py          # 市场状态识别 + 策略推荐引擎
│   ├── strategy_rotator.py       # 市场感知策略轮动
│   ├── signal_router.py          # 多候选信号路由 (Best-of-N)
│   ├── mtf_confirmer.py          # 多周期信号确认
│   ├── online_optimizer.py       # 在线参数自动优化
│   ├── auditor.py                # Reflection Agent 审计
│   ├── correlation_guard.py      # 相关性风控
│   └── position_sizer.py         # 动态仓位计算
│
├── data_providers/                # 统一数据抽象层
│   ├── base.py                   # BaseDataProvider 抽象基类
│   ├── crypto.py                 # CryptoDataProvider（限流+熔断）
│   ├── stock.py / us_stock.py    # 股票数据源
│   ├── factory.py                # DataProviderFactory
│   └── compat.py                 # 向后兼容适配
│
├── agent_gateway/                 # Agent Gateway（Token 鉴权）
├── mcp_server/                    # MCP Server v2（17 tools）
│   └── trading_mcp.py            # 审计日志 + SSE/HTTP/stdio
│
├── notify.py                      # 多通道通知（飞书/Telegram/Discord/Email/Webhook）
├── strategies.py                  # 11种策略实现 + STRATEGY_REGISTRY
├── backtest.py                    # 单标的回测引擎
├── batch_backtest.py              # 批量回测 + Grid Search
├── crypto_api.py                  # CCXT/Gate.io 封装
├── trade_history.py               # 交易历史记录
├── tdx_compiler.py                # 通达信公式编译器
│
├── .gitignore                     # Git 忽略规则（*.db/.env 等）
├── requirements.txt               # Python 依赖
└── README.md
```

---

## 安装部署

### 环境要求
- Python >= 3.10
- SQLite（内置）
- 交易所 API Key（实盘必需）

### 快速启动

```bash
git clone https://github.com/tonjasmy-oss/trading-system.git
cd trading-system

pip install ccxt pandas numpy fastapi uvicorn akshare aiohttp python-dotenv

# 复制并编辑环境变量
cp .env.example .env
vim .env

# 启动
python3 -u live_trading.py --daemon &
python3 -m uvicorn dashboard:app --host 0.0.0.0 --port 8081 &
# 访问 http://localhost:8081
```

---

## 环境变量配置

```env
# 交易所
CRYPTO_EXCHANGE=gateio
CRYPTO_API_KEY=***
CRYPTO_API_SECRET=***

# AI 信号过滤
AI_MODEL=deepseek
AI_SIGNAL_FILTER_ENABLED=true
DEEPSEEK_API_KEY=sk-***

# 实盘交易
LIVE_TRADING_ENABLED=true
LIVE_EXCHANGE=weex
LIVE_API_KEY=***
LIVE_API_SECRET=***

# 多 Agent
MULTI_AGENT_ENABLED=true
AGENT_SYMBOLS=SUI/USDT:DONCHIAN:weex:2h,SOL/USDT:DONCHIAN:weex:2h,XAUT/USDT:DONCHIAN:weex:2h
AGENT_CHECK_INTERVAL=60

# 市场感知策略自动轮动（默认 false，仅日志推荐）
STRATEGY_AUTO_ROTATE=false

# 飞书告警
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# Telegram（可选）
# TELEGRAM_BOT_TOKEN=***
# TELEGRAM_CHAT_ID=***

# Discord（可选）
# DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxx

# 风控
RISK_MAX_DAILY_LOSS_PCT=0.05
RISK_MAX_TOTAL_EXPOSURE=0.45
```

---

## v3 新增功能

### 市场状态感知策略推荐 (P0-1)

决策链日志实时显示市场状态和当前策略适配度：

```
[agent_1] 📊 决策链: ... | 市场=ranging/high 策略适配=40
[agent_2] 📊 决策链: ... | 市场=ranging/medium 策略适配=30 💡推荐: BOLLINGER(适配90)
```

9种市场状态 × 策略适配度映射表，DONCHIAN 在上行趋势适配 95，震荡市仅 25-40。

### 策略实验管线 (P0-2)

```bash
# API 调用
curl 'http://localhost:8081/api/experiment/pipeline/run?symbol=SOL/USDT&timeframe=2h&strategies=DONCHIAN,BOLLINGER,RSI'

# CLI
python3 -c "from components.experiment_pipeline import quick_experiment; print(quick_experiment('SOL/USDT','2h'))"
```

输出：市场状态 → 51个候选策略 → 批量回测(14秒) → 6因子评分 → 最优策略排行。

### 分层策略架构 (P1-1)

```python
from components.layered_strategy import LayeredStrategy, StrategyRisk, RsiLayeredStrategy

# 指标层 / 信号层 / 风险层 三层分离
s = RsiLayeredStrategy(rsi_period=14, oversold=28, overbought=65)
print(s.risk_config)  # StrategyRisk(stop_loss=0.02, take_profit=0.04, ...)
```

### StrategySpec JSON 编译器 (P1-3)

AI 用 JSON 描述策略，系统自动编译为可执行 Python 类：

```json
{
  "name": "rsi_oversold_bounce",
  "indicators": [{"name":"rsi","type":"RSI","params":{"period":14}}],
  "entry_conditions": [{"indicator":"rsi","operator":"cross_above","value":28}],
  "risk": {"stop_loss":0.02, "take_profit":0.04}
}
```

### MCP Server v2 (P2-2)

17 个 MCP 工具，支持 SSE / HTTP / stdio 三传输：

```bash
python3 mcp_server/trading_mcp.py
# stdio → 直接用于 Claude Code / Cursor
# MCP_TRANSPORT=sse MCP_PORT=8000 → HTTP 服务
```

工具：`system_status` / `detect_regime` / `run_experiment` / `compare_strategies` / `get_replay_stats` 等。

---

## Dashboard API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Dashboard 首页 |
| `/api/system/status` | GET | 系统运行状态 |
| `/api/sansheng/status` | GET | 三省六部架构详情（Agent策略/风控/模块） |
| `/api/positions` | GET | 当前持仓 |
| `/api/portfolio/value` | GET | 账户市值统计 |
| `/api/market/prices` | GET | 全市场实时行情 |
| `/api/experiment/pipeline/regime` | GET | [v3] 市场状态 + 策略推荐 |
| `/api/experiment/pipeline/run` | GET | [v3] 策略实验管线 |
| `/api/experiment/pipeline/quick` | GET | [v3] 对所有Agent标的快速实验 |
| `/api/backtest/compare` | GET | 多策略对比回测排行 |
| `/api/backtest/strategies` | GET | 可回测策略列表 |
| `/api/replay/stats` | GET | 交易复盘KPI |
| `/api/replay/trades` | GET | 交易历史（含市场状态标注） |
| `/api/replay/heatmap` | GET | 策略×市场热力图 |
| `/api/data/status` | GET | 数据源健康状态 |
| `/api/agent/v1/*` | ANY | Agent Gateway（需AGENT_TOKEN） |

---

## 策略说明

| 策略 | 类型 | 适合市场 | 说明 |
|------|------|----------|------|
| **RSI** | 摆动 | 下跌/震荡 | 超卖买入/超买卖出 |
| **DONCHIAN** | 趋势 | 上升/下跌 | 海龟通道突破，趋势市最优 |
| **BOLLINGER** | 均值回归 | 震荡 | 布林带上下轨突破回归 |
| **MACD** | 趋势 | 上升/下跌 | 金叉买入/死叉卖出 |
| **KDJ** | 摆动 | 低波动震荡 | 摆动交易 |
| **ATRSTOP** | 趋势 | 高波动 | ATR动态止损趋势跟随 |
| **SMA** | 趋势 | 低波动上升 | 均线交叉 |
| **VOTE** | 混合 | 通用 | RSI+MACD+BOLL三策略投票 |
| **MULTIFACTOR** | 趋势 | 上升 | 多因子综合评分 |
| **FUNDING_ARB** | 套利 | 震荡 | 资金费率套利 |
| **STAT_ARB** | 套利 | 震荡 | 统计套利 |
| **RSI_LAYERED** | 摆动 | 下跌/震荡 | [v3] 分层实现示例 |
| **EMA_CROSS_LAYERED** | 趋势 | 上升/下跌 | [v3] 分层实现示例 |

---

## 已优化标的参数

| 标的 | 策略 | 参数 | 回测结果 |
|------|------|------|----------|
| BTC/USDT | VOTE | RSI_P=10 OS=28 OB=65 SL=4% TP=8% | Score=17.89, +20.11% |
| ETH/USDT | VOTE | RSI_P=14 OS=30 OB=65 SL=2% TP=4% | Score=15.55, +24.93% |
| SOL/USDT | VOTE | RSI_P=10 OS=28 OB=65 SL=1.5% TP=4% | Score=11.51, +15.53% |
| SUI/USDT | DONCHIAN | ch=30 ema=10 SL=3% TP=5% | Grid Search 2026-05-22 |
| XAUT/USDT | DONCHIAN | ch=14 ema=30 SL=1.5% TP=3% | +40.53%, Sharpe=0.99 |

---

## 安全说明

- **无硬编码密钥**：所有 API Key / Secret / Token 均通过环境变量注入
- **AGENT_TOKEN**：敏感操作强制验证，保存于 `.agent_token`（权限 600），不进入 Git
- **数据库隔离**：所有 `.db` / `.log` / `nohup_*.out` / `.pid` 文件通过 `.gitignore` 排除
- **实盘保护**：门下省 11 条风控规则，四级递进 (NORMAL→CAUTION→WARNING→LOCKED)，一票否决制

---

## 故障排除

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
fuser -k 8081/tcp; python3 -m uvicorn dashboard:app --host 0.0.0.0 --port 8081
```

**MCP Server 连接问题？**
```bash
# 检查 Dashboard 是否运行
curl http://localhost:8081/api/system/status
# 直接 stdio 模式启动
python3 mcp_server/trading_mcp.py
```

---

⚠️ **风险提示**：量化交易存在风险，请先用模拟盘测试，确认策略有效后再使用实盘。
