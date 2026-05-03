# Trading System

加密货币量化交易系统，支持 Grid Search 参数优化、多策略投票、实盘交易。

## 功能

- **多交易所支持**：Binance / Gate.io / OKX / Bybit / Bitget / Hyperliquid / Kraken / Bitfinex
- **多策略**：RSI / MACD / Bollinger Bands / VOTE 多策略投票
- **Grid Search**：参数批量优化，RSI_period / oversold / overbought / stop_loss / take_profit
- **实盘交易**：CCXT 统一接口，支持模拟盘/实盘
- **Dashboard**：Web 可视化界面（K线、持仓、信号）
- **飞书告警**：持仓变化、价格异动实时推送

---

## 安装部署

### 环境要求

- Python >= 3.10
- Redis（可选，用于缓存加速）
- 交易所 API Key（实盘必需）

### 1. 克隆仓库

```bash
git clone https://github.com/tonjasmy-oss/trading-system.git
cd trading-system
```

### 2. 创建虚拟环境（推荐）

```bash
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate      # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

**核心依赖：**

| 包 | 用途 |
|----|------|
| `ccxt` | 交易所统一接口 |
| `pandas` | 数据分析 |
| `numpy` | 数值计算 |
| `fastapi` + `uvicorn` | Web Dashboard |
| `requests` | HTTP 请求 |
| `python-dotenv` | 环境变量管理 |
| `redis` | 缓存加速（可选） |
| `aiohttp` | 异步 HTTP（可选） |

> 如 `ccxt` 未在 requirements.txt 中，请手动安装：
> ```bash
> pip install ccxt
> ```

### 4. 配置环境变量

```bash
# 创建 .env 文件
touch .env

# 编辑 .env，填入以下配置：
```

**.env 示例配置：**

```env
# 交易所选择：binance / gateio / okx / bybit / bitget / hyperliquid
CRYPTO_EXCHANGE=binance
CRYPTO_API_KEY=your_api_key_here
CRYPTO_API_SECRET=your_api_secret_here

# 飞书告警（可选）
FEISHU_APP_ID=your_feishu_app_id
FEISHU_APP_SECRET=your_feishu_app_secret
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# 风险控制
PRICE_CHECK_INTERVAL=60
PRICE_CHANGE_THRESHOLD=0.05
```

### 5. 初始化数据库

```bash
python3 -c "from database import init_db; init_db()"
```

---

## 运行

### Dashboard（Web 界面）

```bash
python3 dashboard.py
# 访问 http://localhost:8081
```

### Grid Search 参数优化

```bash
# 单标的
python3 batch_backtest.py --grid-search --grid-symbol ETH/USDT

# 批量标的
python3 batch_backtest.py --grid-search --symbols BTC ETH SOL SUI

# 指定策略
python3 batch_backtest.py --grid-search --grid-symbol BTC/USDT --strategy RSI
```

### 回测

```bash
python3 backtest.py --symbol ETH/USDT --timeframe 4h
```

### 实盘交易

```bash
# 模拟盘（默认）
python3 live_trading.py --symbol ETH/USDT --exchange binance

# 实盘
LIVE_TESTNET=false python3 live_trading.py --symbol ETH/USDT --exchange binance
```

---

## 已优化标的（Grid Search 最优参数）

| 标的 | RSI_P | Oversold | Overbought | StopLoss | TakeProfit | Score |
|------|-------|----------|------------|---------|------------|-------|
| BTC/USDT | 10 | 18 | 65 | 4% | 8% | 17.89 |
| ETH/USDT | 14 | 28 | 65 | 2% | 4% | 15.55 |
| SOL/USDT | 10 | 20 | 65 | 1.5% | 4% | 11.51 |
| SUI/USDT | (待优化) | | | | | |
| KYVE/USDT | (待优化) | | | | | |
| PYTH/USDT | (待优化) | | | | | |

---

## 目录结构

```
trading-system/
├── config.py              # 配置文件
├── dashboard.py           # Web Dashboard (Flask + Lightweight Charts)
├── backtest.py            # 单标的回测引擎
├── batch_backtest.py      # 批量回测 + Grid Search
├── strategies.py          # 策略实现（RSI / MACD / Bollinger）
├── live_trading.py        # 实盘交易
├── multi_strategy_vote.py # 多策略投票
├── risk_manager.py        # 风控管理
├── crypto_api.py          # CCXT 封装
├── database.py            # SQLite 数据库
├── feishu_alert.py        # 飞书告警
├── monitor.py             # 价格监控
├── portfolio.py           # 持仓管理
├── shangshu_sheng.py      # 尚书省执行层
├── menxia_sheng.py        # 门下省风控层
├── requirements.txt       # Python 依赖
└── README.md              # 本文档
```

---

## 支持交易所

| 交易所 | 状态 | 备注 |
|--------|------|------|
| Binance | ✅ | 推荐实盘 |
| Gate.io | ✅ | 默认数据源 |
| OKX | ✅ | |
| Bybit | ✅ | |
| Bitget | ✅ | |
| Hyperliquid | ✅ | |
| Kraken | ✅ | |
| Bitfinex | ✅ | |
| Weex | ⚠️ | 需额外适配 |

---

## 策略说明

| 策略 | 说明 |
|------|------|
| RSI | 相对强弱指标，Oversold 买入 / Overbought 卖出 |
| MACD | 指数平滑移动平均线，金叉买入/死叉卖出 |
| Bollinger Bands | 布林带突破，下轨买入/上轨卖出 |
| VOTE | RSI(40%) + MACD(30%) + Bollinger(30%) 多策略投票 |

---

## 故障排除

**CCXT 导入失败？**
```bash
pip install ccxt
```

**数据库初始化失败？**
```bash
chmod 666 trading_system.db
```

**Redis 未运行？**
```bash
redis-server --daemonize yes
```

---

⚠️ **风险提示**：量化交易存在风险，请先用模拟盘测试，确认策略有效后再使用实盘。