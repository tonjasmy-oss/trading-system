# Trading System

加密货币量化交易系统，支持 Grid Search 参数优化、多策略投票、实盘交易。

## 功能

- **多交易所支持**：Binance / Gate.io / OKX / Bybit / Bitget / Hyperliquid / Kraken / Bitfinex
- **多策略**：RSI / MACD / Bollinger Bands / VOTE 多策略投票
- **Grid Search**：参数批量优化，RSI_period / oversold / overbought / stop_loss / take_profit
- **实盘交易**：CCXT 统一接口，支持模拟盘/实盘
- **Dashboard**：Web 可视化界面（K线、持仓、信号）
- **飞书告警**：持仓变化、价格异动实时推送
- **股票支持**：A股（akshare）/ 港股（akshare）/ 美股（yfinance + akshare）回测与实盘

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

### 股票回测（A股 / 港股 / 美股）

```bash
# A股（浦发银行 + 平安银行，均线交叉策略）
python3 backtest.py --codes 600000.SH,000001.SZ --start 2024-01-01 --end 2025-01-01 --strategy ma_cross --fast 5 --slow 20

# 港股（腾讯控股）
python3 backtest.py --codes 00700.HK --start 2024-01-01 --end 2025-01-01 --strategy ma_cross

# 美股（Apple）
python3 backtest.py --codes AAPL --start 2024-01-01 --end 2025-01-01 --strategy rsi --rsi-period 14

# RSI 策略
python3 backtest.py --codes 600000.SH --strategy rsi --rsi-period 14 --rsi-oversold 30 --rsi-overbought 70
```

### 实盘交易

```bash
# 模拟盘（默认）
python3 live_trading.py --symbol ETH/USDT --exchange binance

# 实盘
LIVE_TESTNET=false python3 live_trading.py --symbol ETH/USDT --exchange binance
```

---

## 股票交易（Vibe-Trading 集成）

基于 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) 的核心引擎适配，支持多市场：

| 市场 | 数据源 | 代码格式 | 规则 |
|------|--------|---------|------|
| A股 | akshare（免费）| `600000.SH`, `000001.SZ` | T+1, 涨跌停±10%/20%, 印花税0.05% |
| 港股 | akshare（免费）| `00700.HK` | T+0, 印花税0.1%双边 |
| 美股 | yfinance + akshare（免费）| `AAPL`, `TSLA` | T+0, 零佣金, 分数股 |

**核心模块：**
- `vibe_integration/stock_backtest.py` — 多市场统一回测引擎（ChinaAEngine / GlobalEquityEngine）
- `stock_data/stock_api.py` — 已有，统一数据接口
- `stock_trading/trading_api.py` — 已有，AlpacaTrader（美股）+ TigerTrader（港/A股）

```python
from vibe_integration import run_stock_backtest

result = run_stock_backtest(
    codes=["600000.SH", "000001.SZ"],
    start_date="2024-01-01",
    end_date="2025-01-01",
    strategy="ma_cross",
    signal_params={"fast": 20, "slow": 60},
    initial_cash=1_000_000.0,
)
```

**Dashboard API：**
```
GET /api/stock/chart?codes=600000.SH&start_date=2024-01-01&end_date=2025-01-01&strategy=ma_cross
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
| Weex | ✅ 已适配 | weex.py 适配器完成 |

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
## 股票市场支持

### A股 (上海/深圳)
```python
from stock_data.stock_api import get_a_stock_ohlcv
df = get_a_stock_ohlcv("600000.SH", period="1y")  # 浦发银行
df = get_a_stock_ohlcv("000001.SZ", period="1y")  # 平安银行
```

### 港股
```python
from stock_data.stock_api import get_hk_stock_ohlcv
df = get_hk_stock_ohlcv("00700.HK", period="1y")  # 腾讯
```

### 美股
```python
from stock_data.stock_api import get_us_stock_ohlcv
df = get_us_stock_ohlcv("AAPL", period="1y")  # 苹果
df = get_us_stock_ohlcv("TSLA", period="1y")  # 特斯拉
```

### 统一接口
```python
from stock_data.stock_api import get_stock_ohlcv
df = get_stock_ohlcv("600000.SH")  # 自动识别A股
df = get_stock_ohlcv("00700.HK")  # 自动识别港股
df = get_stock_ohlcv("AAPL")       # 自动识别美股
```

### 实盘交易
```python
# 美股 - Alpaca (免费API)
from stock_trading.trading_api import AlpacaTrader
trader = AlpacaTrader(paper=True)  # 模拟盘
trader.buy("AAPL", 10)
trader.sell("AAPL", 10)

# 港股/A股 - 老虎证券
from stock_trading.trading_api import TigerTrader
trader = TigerTrader()
trader.buy("00700.HK", 100, market="HK")
```

## 股票市场支持

### A股 (上海/深圳)
```python
from stock_data.stock_api import get_a_stock_ohlcv

# 获取K线数据
df = get_a_stock_ohlcv("600000.SH", period="daily")  # 浦发银行 (沪市)
df = get_a_stock_ohlcv("000001.SZ", period="daily")  # 平安银行 (深市)
df = get_a_stock_ohlcv("000001.SZ", period="weekly")  # 周线

# 获取实时行情
info = get_a_stock_realtime("600000.SH")
```

### 港股
```python
from stock_data.stock_api import get_hk_stock_ohlcv

# 获取K线数据（最近365个交易日）
df = get_hk_stock_ohlcv("00700.HK")  # 腾讯
df = get_hk_stock_ohlcv("09988.HK")  # 阿里巴巴

# 获取实时行情
info = get_hk_stock_realtime("00700.HK")
```

### 美股
```python
from stock_data.stock_api import get_us_stock_ohlcv

# 获取K线数据
df = get_us_stock_ohlcv("AAPL", period="1y")   # 苹果
df = get_us_stock_ohlcv("TSLA", period="1mo", interval="1h")  # 特斯拉小时线

# 获取实时行情
info = get_us_stock_realtime("AAPL")
```

### 统一接口
```python
from stock_data.stock_api import get_stock_ohlcv

df = get_stock_ohlcv("600000.SH")  # 自动识别A股
df = get_stock_ohlcv("00700.HK")   # 自动识别港股
df = get_stock_ohlcv("AAPL")       # 自动识别美股
```

### 实盘交易
```python
# 美股 - Alpaca (免费API，需注册 https://alpaca.markets)
from stock_trading.trading_api import AlpacaTrader

trader = AlpacaTrader(paper=True)  # 模拟盘
trader.buy("AAPL", 10)
trader.sell("AAPL", 10)

# 港股/A股 - 老虎证券 (需注册 https://www.tigerbrokers.com)
from stock_trading.trading_api import TigerTrader

trader = TigerTrader()
trader.buy("00700.HK", 100, market="HK")
```

### 数据源
| 市场 | 数据源 | 费用 | 说明 |
|------|--------|------|------|
| A股 | akshare (东方财富) | 免费 | 日/周/月线，支持前复权 |
| 港股 | akshare (新浪财经) | 免费 | 最近365个交易日 |
| 美股 | yfinance | 免费 | 日线/分钟线，支持复权 |

### 安装依赖
```bash
pip3 install akshare yfinance pandas
```

### A股T+1注意事项
A股实行T+1制度，当日买入次日才能卖出。策略需考虑：
- 买入信号产生后，次日才能执行买入
- 止损/止盈按自然日计算，非按买入当日
