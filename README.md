# Trading System · 交易系统

**三省六部制**量化交易系统，支持加密货币与股票的双市场回测 / 模拟 / 实盘交易。核心理念：信号生成（**中书省**）、风控审核（**门下省**）、执行调度（**尚书省**）三层分离，规则硬编码、逻辑可审计。

> **v3.3 更新（2026-06-04）**: 借鉴 Vibe-Trading — Shadow Account / 回测验证增强 / 策略多格式导出。

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    中书省 · 信号生成层                             │
│   RSI / MACD / Bollinger / DONCHIAN / VOTE / ATRSTOP / KDJ       │
│   COINGLASS / MULTIFACTOR / FUNDING_ARB / STAT_ARB               │
│   分层策略 (LayeredStrategy)  ·  StrategySpec JSON 编译器          │
│   ✦ Swarm 多 Agent 投票 (29 预设)  ·  ✦ 452 因子库桥接             │
├──────────────────────────────────────────────────────────────────┤
│                    门下省 · 风控审核层                             │
│   11条风控规则 · 四级递进 (NORMAL→CAUTION→WARNING→LOCKED)          │
│   市场状态感知 · 业绩感知轮动 (回测融合) · 策略感知参数域                       │
├──────────────────────────────────────────────────────────────────┤
│                    尚书省 · 执行调度层                             │
│   Binance / Gate.io / Weex / OKX / Bybit / Hyperliquid            │
├──────────────────────────────────────────────────────────────────┤
│  刑部 · 违规记录   户部 · 权益曲线   仓部 · 持仓管理                 │
│  实验管线 · 策略寻优   MCP Server v2 · 多通道通知                   │
│  ✦ Goal 运行时 · 回测审计追踪                                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 功能一览

| 功能 | 说明 |
|------|------|
| **多交易所** | Binance / Gate.io / Weex / OKX / Bybit / Hyperliquid |
| **15+ 策略** | RSI / MACD / Bollinger / DONCHIAN / SMA / KDJ / ATRSTOP / MULTIFACTOR / VOTE / FUNDING_ARB / STAT_ARB / COINGLASS / RSI_LAYERED / EMA_CROSS_LAYERED |
| **Swarm 投票** | 29 种预设（投资委员会/风险委员会/量化台/宏观论坛等），多 Agent 角色加权投票 |
| **因子库** | 452 因子桥接 (alpha101 / gtja191 / qlib158)，统一 Registry API |
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
| **业绩感知轮动** ✦ | 融合回测数据 + 实盘表现，自动排除亏损策略，优选正收益策略 |
| **策略感知优化** ✦ | 8种策略独立参数域，轮动时自动切换，DB持久化重启不丢 |
| **投票制调参** | 多规则投票取净方向，消除止损同时被调高调低冲突 |
| **多周期确认** | 1h/4h/1d 信号一致性验证 |
| **Shadow Account** ✦ | 历史交易聚类分析→提取盈利规则→影子回测对比实际表现 |
| **回测验证增强** ✦ | 蒙特卡洛模拟 + Bootstrap CI + Walk-Forward + 基准对比 |
| **策略导出** ✦ | Pine Script v6 (7种) + MQL5 (4种)，一键复制到 TradingView/MT5 |
| **安全重启** | `restart.sh` 优雅停止 + 端口清理 + 验证 |

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
├── swarm_bridge.py               # ✦ Swarm 预设桥接（29预设，差异化权重）
├── factor_bridge.py              # ✦ 因子库桥接（452因子，Registry API）
├── goal_bridge.py                # ✦ Research Goal 运行时（回测审计追踪）
│
├── components/                    # 模块化组件
│   ├── experiment_pipeline.py    # 策略实验管线
│   ├── layered_strategy.py       # 分层策略架构
│   ├── strategy_spec.py          # StrategySpec JSON 编译器
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
├── exchanges/                     # 交易所适配器
│   ├── base.py                   # BaseExchangeAdapter
│   ├── binance.py                # Binance 适配器
│   ├── okx.py                    # OKX 适配器
│   └── factory.py                # ExchangeFactory
│
├── agent_gateway/                 # Agent Gateway（Token 鉴权）
├── mcp_server/                    # MCP Server v2（17 tools）
├── agent/                         # Agent Skills
├── frontend/                      # Web 前端
│
├── swarm_backtest.py              # ✦ Swarm 批量回测（29预设 × N标的）
├── strategies.py                  # 15+ 策略实现 + STRATEGY_REGISTRY
├── backtest.py                    # 单标的回测引擎
├── batch_backtest.py              # 批量回测 + Grid Search
├── crypto_api.py                  # CCXT/Gate.io 封装
├── trade_history.py               # 交易历史记录
├── tdx_compiler.py                # 通达信公式编译器
│
├── shadow_account.py              # ✦ 交易影子账户（规则提取+回测对比）
├── backtest_validation.py         # ✦ 回测验证增强（蒙特卡洛/Walk-Forward）
├── strategy_exporter.py           # ✦ 策略导出（Pine Script/MQL5）
├── restart.sh                     # 安全重启脚本
├── run_dashboard.sh               # Dashboard 启动（setsid）
├── run_live_daemon.sh             # Live Daemon 启动
├── requirements.txt               # Python 依赖
├── .gitignore                     # Git 忽略规则（*.db/.env 等）
└── README.md
```

---

## 安装部署

### 环境要求
- Python >= 3.10
- SQLite（内置）或 PostgreSQL（可选）
- 交易所 API Key（实盘必需）

### 快速启动

```bash
git clone https://github.com/tonjasmy-oss/trading-system.git
cd trading-system

pip install ccxt pandas numpy fastapi uvicorn akshare aiohttp python-dotenv pyyaml

# 复制并编辑环境变量
cp .env.example .env
vim .env

# 启动
bash restart.sh
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

# 多 Agent（支持 SWARM:preset_name）
MULTI_AGENT_ENABLED=true
AGENT_SYMBOLS=BTC/USDT:VOTE:binance,ETH/USDT:SWARM:derivatives_strategy_desk:binance,SOL/USDT:SWARM:commodity_research_team:binance:2h,SUI/USDT:SWARM:sector_rotation_team:binance:2h,XAUT/USDT:SWARM:portfolio_review_board:gateio:4h
AGENT_CHECK_INTERVAL=60

# Vibe-Trading 集成
FACTOR_ENABLED=true
SWARM_ENABLED=true
SWARM_DEFAULT_PRESET=crypto_trading_desk
SWARM_THRESHOLD=0.25
GOAL_ENABLED=true

# 飞书告警
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# 风控
RISK_MAX_DAILY_LOSS_PCT=0.05
RISK_MAX_TOTAL_EXPOSURE=0.45
STRATEGY_AUTO_ROTATE=false
```

---

## Swarm 预设

29 种 Vibe-Trading Swarm 预设，每种预设包含 3~8 个 Agent 角色，通过加权投票聚合信号。回测基于 XAUT/USDT 4h（2024-05 ~ 2026-05）数据。

| 排名 | 预设 | 类型 | 夏普 | 收益 | 回撤 |
|------|------|------|------|------|------|
| 1 | **portfolio_review_board** | 组合评审 | 1.92 | +128% | -7.4% |
| 2 | **credit_research_team** | 信用研究 | 1.61 | +107% | -13.2% |
| 3 | **investment_committee** | 投委会 | 1.59 | +107% | -16.2% |
| 4 | **derivatives_strategy_desk** | 衍生品策略 | 1.53 | +93% | -13.2% |
| 5 | **risk_committee** | 风险委员会 | 1.53 | +93% | -13.2% |
| 6 | crypto_trading_desk | 加密货币交易 | 1.51 | +57% | -3.8% |
| 7 | sentiment_intelligence_team | 情绪情报 | 1.44 | +88% | -11.3% |
| 8 | technical_analysis_panel | 技术分析 | 1.43 | +66% | -7.5% |
| 9 | macro_strategy_forum | 宏观论坛 | 1.42 | +92% | -16.1% |

完整 29 种预设列表见 `swarm_bridge.py` 中的 `_PRESET_ROLE_OVERRIDES`。

---

## 闭环优化（v3.2 新增）

策略轮动器和参数优化器形成闭环反馈：

```
 市场状态 → 轮动器选策略 → 开仓交易 → 平仓
     ↑                                    ↓
 业绩表 ← record_outcome               优化器调参 (投票制)
     ↑                                    ↓
 轮动器下次决策 ← _apply_performance_penalty   策略感知参数域切换
```

**核心机制**：
- `strategy_performance` 表：记录每策略每行情下的实盘盈亏，预填 172 条回测数据
- 轮动时先查业绩表：历史上亏 >50% → 降60分排除；盈亏比 <0.8 → 降10分；盈利 >2x → +15分
- 策略切换时优化器自动换参数域（如 RSI 参数 ↔ ATRSTOP 参数），旧参数存档到 `optimizer_state`

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
| **COINGLASS** | 情绪 | 通用 | 市场情绪指标 |
| **SWARM** | 多Agent | 通用 | 29种预设加权投票 |

---

## Dashboard API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Dashboard 首页 |
| `/api/system/status` | GET | 系统运行状态 |
| `/api/sansheng/status` | GET | 三省六部架构详情 |
| `/api/positions` | GET | 当前持仓 |
| `/api/portfolio/value` | GET | 账户市值统计 |
| `/api/market/prices` | GET | 全市场实时行情 |
| `/api/experiment/pipeline/regime` | GET | 市场状态 + 策略推荐 |
| `/api/experiment/pipeline/run` | GET | 策略实验管线 |
| `/api/backtest/compare` | GET | 多策略对比回测排行 |
| `/api/strategy/export` | GET | 策略多格式导出（Pine/MQL5） |
| `/api/replay/stats` | GET | 交易复盘KPI |
| `/api/replay/trades` | GET | 交易历史 |
| `/api/replay/heatmap` | GET | 策略×市场热力图 |
| `/api/agent/v1/*` | ANY | Agent Gateway (需AGENT_TOKEN) |

---

## 安全说明

- **无硬编码密钥**：所有 API Key / Secret / Token 均通过环境变量注入
- **AGENT_TOKEN**：敏感操作强制验证，保存于 `.agent_token`（权限 600），不进入 Git
- **数据库隔离**：所有 `.db` / `.log` / `nohup_*.out` / `.pid` 文件通过 `.gitignore` 排除
- **实盘保护**：门下省 11 条风控规则，四级递进，一票否决制
- **.env 过滤**：`.gitignore` 排除所有 `.env*` 文件，示例配置在 `.env.example`

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
fuser -k 8081/tcp; bash restart.sh
```

**回测数据不足？**
```bash
# 数据自动从 CCXT 在线拉取，首次运行需等待
python3 -c "from history_cache import init_cache_db; init_cache_db()"
```

---

⚠️ **风险提示**：量化交易存在风险，请先用模拟盘测试，确认策略有效后再使用实盘。
