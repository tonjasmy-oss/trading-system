# Trading System

加密货币量化交易系统，支持 Grid Search 参数优化、多策略投票、实盘交易。

## 功能

- **多交易所支持**：Binance / Gate.io / OKX / Bybit / Bitget / Hyperliquid
- **多策略**：RSI / MACD / Bollinger Bands / VOTE 多策略投票
- **Grid Search**：参数批量优化，RSI_period / oversold / overbought / stop_loss / take_profit
- **实盘交易**：CCXT 统一接口，支持模拟盘/实盘
- **Dashboard**：Web 可视化界面（K线、持仓、信号）
- **飞书告警**：持仓变化、价格异动实时推送

## 文件结构

```
├── config.py           # 配置文件（API Key、策略参数）
├── dashboard.py        # Web Dashboard（Flask + Lightweight Charts）
├── backtest.py         # 单标的回测
├── batch_backtest.py   # 批量回测 + Grid Search
├── strategies.py       # 策略实现
├── live_trading.py     # 实盘交易
├── multi_strategy_vote.py  # 多策略投票
├── risk_manager.py     # 风控管理
├── crypto_api.py       # CCXT 封装
└── requirements.txt    # Python 依赖
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入交易所 API Key
```

### 3. 运行 Dashboard

```bash
python3 dashboard.py
# 访问 http://localhost:8081
```

### 4. Grid Search 参数优化

```bash
python3 batch_backtest.py --grid-search --grid-symbol SUI/USDT
```

### 5. 实盘交易

```bash
python3 live_trading.py --symbol ETH/USDT --exchange binance
```

## 已优化标的（Grid Search 最优参数）

| 标的 | RSI_P | Oversold | Overbought | StopLoss | TakeProfit |
|------|-------|----------|------------|---------|------------|
| BTC/USDT | 10 | 18 | 65 | 4% | 8% |
| ETH/USDT | 14 | 28 | 65 | 2% | 4% |
| SOL/USDT | 10 | 20 | 65 | 1.5% | 4% |

## 支持交易所

| 交易所 | 状态 | 备注 |
|--------|------|------|
| Binance | ✅ | 默认实盘 |
| Gate.io | ✅ | 默认数据源 |
| OKX | ✅ | |
| Bybit | ✅ | |
| Bitget | ✅ | |
| Hyperliquid | ✅ | |
| Kraken | ✅ | |
| Bitfinex | ✅ | |
| Weex | ⚠️ | 需额外适配 |

## 策略说明

- **RSI**：相对强弱指标，逢低买入/逢高卖出
- **MACD**：指数平滑移动平均线，金叉/死叉信号
- **Bollinger Bands**：布林带突破策略
- **VOTE**：RSI(40%) + MACD(30%) + Bollinger(30%) 多策略投票

## 注意事项

⚠️ 量化交易存在风险，请先用模拟盘测试，确认策略有效后再使用实盘。