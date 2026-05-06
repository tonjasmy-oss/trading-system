"""
Web Dashboard 模块 - 交易监控系统
所有功能集成到 Web 界面
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import asyncio
import os

from portfolio import Portfolio
from monitor import quick_price_check
from feishu_alert import FeishuAlert
feishu_alert = FeishuAlert()
from database import init_db, get_positions, get_trades, get_alerts
from config import PRICE_CHECK_INTERVAL

app = FastAPI(title="交易监控系统", version="2.0.0")
portfolio = Portfolio()

# 全局监控状态
_monitor_status = {"status": "stopped", "message": "未启动"}
_start_time = None

# ========== 数据模型 ==========

class TradeRequest(BaseModel):
    symbol: str
    market: str
    quantity: float
    price: float
    action: str = "buy"  # buy or sell

class AlertRequest(BaseModel):
    symbol: str
    market: str
    alert_type: str
    price: float
    threshold: float
    message: str

class MonitorAction(BaseModel):
    action: str  # start, stop

# ========== API 接口 ==========

@app.get("/")
async def root():
    return {"message": "交易监控系统 API", "version": "2.0.0", "status": _monitor_status}

@app.get("/api/system/status")
async def get_system_status():
    """获取系统状态"""
    return {
        "monitor": _monitor_status,
        "uptime": int(asyncio.get_event_loop().time()) if _start_time else 0,
        "start_time": _start_time
    }

@app.get("/api/sansheng/status")
async def get_sansheng_status():
    """获取三省六部架构状态"""
    from config import LIVE_TRADING_ENABLED, LIVE_EXCHANGE, LIVE_TESTNET
    from menxia_sheng import MenxiaSheng, RiskLevel
    from shangshu_sheng import ShangshuSheng

    menxia_ok = MenxiaSheng is not None
    shangshu_ok = ShangshuSheng is not None

    # 尝试获取门下省状态
    menxia_info = {}
    if menxia_ok:
        try:
            from live_trading import orchestrator
            if orchestrator and orchestrator.menxia:
                ms = orchestrator.menxia.get_status()
                menxia_info = {
                    "level": ms["risk_level"],
                    "daily_loss_pct": ms["daily_loss_pct"],
                    "exposure_pct": ms["total_exposure_pct"],
                    "open_positions": ms["open_positions"],
                    "daily_trades": ms["daily_trades"],
                    "can_open": ms["can_open"],
                }
        except Exception:
            pass

    return {
        "live_trading": LIVE_TRADING_ENABLED,
        "exchange": LIVE_EXCHANGE,
        "testnet": LIVE_TESTNET,
        "menxia_available": menxia_ok,
        "shangshu_available": shangshu_ok,
        "menxia": menxia_info,
    }

@app.post("/api/monitor")
async def monitor_control(action: MonitorAction):
    """控制监控"""
    global _monitor_status
    
    if action.action == "start":
        _monitor_status = {"status": "running", "message": "监控已启动"}
        return {"success": True, "status": _monitor_status}
    elif action.action == "stop":
        _monitor_status = {"status": "stopped", "message": "监控已停止"}
        return {"success": True, "status": _monitor_status}
    else:
        raise HTTPException(status_code=400, detail="无效操作")

def update_monitor_status(status: str, message: str):
    """更新监控状态"""
    global _monitor_status
    _monitor_status = {"status": status, "message": message}

@app.get("/api/positions")
async def get_positions_api():
    """获取持仓"""
    return portfolio.get_positions()

@app.get("/api/portfolio/value")
async def get_portfolio_value():
    """获取持仓市值和盈亏"""
    return portfolio.get_position_value()

@app.get("/api/trades")
async def get_trades_api(limit: int = 50):
    """获取交易历史"""
    return portfolio.get_trades(limit)

@app.get("/api/alerts")
async def get_alerts_api(limit: int = 20):
    """获取告警历史"""
    return get_alerts(limit)

@app.post("/api/trade")
async def trade_api(req: TradeRequest):
    """交易接口"""
    if req.quantity <= 0 or req.price <= 0:
        raise HTTPException(status_code=400, detail="数量和价格必须为正数")
    
    if req.action.lower() == "buy":
        success = portfolio.buy(req.symbol, req.market, req.quantity, req.price)
        message = f"买入 {req.symbol} {req.quantity} @ {req.price}"
    else:
        success = portfolio.sell(req.symbol, req.market, req.quantity, req.price)
        message = f"卖出 {req.symbol} {req.quantity} @ {req.price}"
    
    return {"success": success, "message": message}

@app.get("/api/price/{market}/{symbol}")
async def get_price_api(symbol: str, market: str):
    """获取单个实时价格"""
    data = quick_price_check(symbol, market)
    if data:
        return data
    raise HTTPException(status_code=404, detail="价格获取失败")

@app.get("/api/market/prices")
async def get_all_prices():
    """获取所有市场实时行情"""
    from stock_api import get_stock
    from crypto_api import get_crypto_price
    
    prices = []
    
    # A股
    for symbol in ["600000", "000001", "000002", "600519"]:
        data = get_stock(symbol, "CN")
        if data:
            prices.append(data)
    
    # 港股
    for symbol in ["00700", "09988", "03690"]:
        data = get_stock(symbol, "HK")
        if data:
            prices.append(data)
    
    # 美股
    for symbol in ["AAPL", "TSLA", "NVDA", "MSFT"]:
        data = get_stock(symbol, "US")
        if data:
            prices.append(data)
    
    # 加密货币
    for symbol in ["BTC", "ETH", "BNB", "SOL"]:
        data = get_crypto_price(symbol)
        if data:
            prices.append({
                "symbol": data.get("symbol"),
                "market": "CRYPTO",
                "name": data.get("symbol"),
                "price": data.get("price"),
                "prev_close": data.get("price") * (1 - data.get("change_24h", 0) / 100) if data.get("change_24h") else data.get("price"),
                "change": data.get("price") * data.get("change_24h", 0) / 100 if data.get("change_24h") else 0,
                "change_pct": data.get("change_24h", 0),
                "high_24h": data.get("high_24h"),
                "low_24h": data.get("low_24h"),
            })
    
    return prices

@app.post("/api/alert/test")
async def test_alert(req: AlertRequest):
    """发送测试告警"""
    try:
        feishu_send_alert(
            symbol=req.symbol,
            market=req.market,
            alert_type=req.alert_type,
            price=req.price,
            threshold=req.threshold,
            message=req.message
        )
        return {"success": True, "message": "告警已发送"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ================================================================
# P3 股票 K线 API（支持 A股/港股/美股）
# ================================================================

@app.get("/api/stock/chart")
async def get_stock_chart_data(
    codes: str = "600000.SH",
    start_date: str = "2024-01-01",
    end_date: str = "2025-01-01",
    strategy: str = "ma_cross",
    fast: int = 20,
    slow: int = 60,
):
    """
    获取股票 K线 + 回测信号图表数据
    codes: 逗号分隔代码，如 600000.SH,000001.SZ
    返回: OHLCV K线, Equity Curve, 买卖点, 指标
    """
    try:
        import pandas as pd
        from vibe_integration.stock_backtest import fetch_stock_data, SimpleMASignal, RSISignal
        code_list = [c.strip() for c in codes.split(",")]

        # 加载数据
        data_map = fetch_stock_data(code_list, start_date, end_date)
        if not data_map:
            raise HTTPException(status_code=404, detail="数据获取失败")

        # 生成信号
        if strategy == "rsi":
            sig_gen = RSISignal(period=14, oversold=30.0, overbought=70.0)
        else:
            sig_gen = SimpleMASignal(fast=fast, slow=slow)
        signal_map = sig_gen.generate(data_map)

        # 只返回第一个标的的 K线（前端单标的图表）
        first_code = code_list[0]
        if first_code not in data_map:
            first_code = list(data_map.keys())[0]
        df = data_map[first_code]

        # OHLCV
        ohlc = [
            {
                "t": int(pd.Timestamp(ts).timestamp()),
                "o": round(float(r["open"]), 2),
                "h": round(float(r["high"]), 2),
                "l": round(float(r["low"]), 2),
                "c": round(float(r["close"]), 2),
            }
            for ts, r in df.iterrows()
        ]

        # Equity curve（简化模拟）
        equity_curve = []
        equity = 1000000.0
        in_pos = False
        entry_price = 0.0
        sigs = signal_map.get(first_code, pd.Series(0, index=df.index))
        for (ts, row), sig_val in zip(df.iterrows(), sigs):
            ts_sec = int(pd.Timestamp(ts).timestamp())
            if in_pos:
                pnl = (row["close"] - entry_price) / entry_price
                if pnl <= -0.05:
                    equity *= (1 + pnl * 0.5)
                    in_pos = False
                elif pnl >= 0.10:
                    equity *= (1 + pnl * 0.9)
                    in_pos = False
            if sig_val == 1 and not in_pos:
                in_pos = True
                entry_price = row["close"]
            equity_curve.append({"t": ts_sec, "v": round(equity, 2)})

        # 买卖点
        buy_markers = []
        sell_markers = []
        entry_p = 0.0
        for (ts, row), sig_val in zip(df.iterrows(), sigs):
            ts_sec = int(pd.Timestamp(ts).timestamp())
            if sig_val == 1:
                buy_markers.append({"t": ts_sec, "price": round(float(row["close"]), 2)})
                entry_p = float(row["close"])
            elif sig_val == -1:
                sell_markers.append({"t": ts_sec, "price": round(float(row["close"]), 2)})

        # 均线指标
        indicators_out = {}
        if strategy == "ma_cross" or strategy == "ma":
            ma_fast_vals = df["close"].rolling(fast).mean()
            ma_slow_vals = df["close"].rolling(slow).mean()
            indicators_out["ma_fast"] = [
                {"t": int(pd.Timestamp(ts).timestamp()), "v": round(float(v), 2)}
                for ts, v in zip(df.index, ma_fast_vals) if not pd.isna(v)
            ]
            indicators_out["ma_slow"] = [
                {"t": int(pd.Timestamp(ts).timestamp()), "v": round(float(v), 2)}
                for ts, v in zip(df.index, ma_slow_vals) if not pd.isna(v)
            ]
        elif strategy == "rsi":
            delta = df["close"].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, 1e-10)
            rsi = 100 - (100 / (1 + rs))
            indicators_out["rsi"] = [
                {"t": int(pd.Timestamp(ts).timestamp()), "v": round(float(v), 2)}
                for ts, v in zip(df.index, rsi) if not pd.isna(v)
            ]

        return {
            "code": first_code,
            "ohlc": ohlc,
            "equity_curve": equity_curve,
            "buy_markers": buy_markers,
            "sell_markers": sell_markers,
            "indicators": indicators_out,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取股票图表失败: {str(e)}")


# ================================================================
# P2 回测图表 API（Equity Curve + 买卖点标注）
# ================================================================

@app.get("/api/backtest/chart/{strategy_name}")
async def get_backtest_chart_data(strategy_name: str):
    """
    获取回测图表数据：Equity Curve + 买卖点标注
    strategy_name: "KDJ" | "MACD" | "MA_CROSS" | "CCI" | "RSI" | "BOLL" | "WR" | "MultiVote"
    """
    import random, math
    from tdx_compiler import FormulaStrategy, BUILTIN_FORMULAS, TdxCompiler
    from multi_strategy_vote import MultiStrategyVote
    from strategies import RSIStrategy, SMAcrossStrategy, StrategyConfig

    # 生成确定性 K 线
    n = 300
    base_price = 2000.0
    random.seed(42)
    price = base_price
    candles = []
    for i in range(n):
        r = random.uniform(-0.025, 0.025) + 0.0005
        o = price
        c = price * (1 + r)
        h = max(o, c) * (1 + random.uniform(0, 0.008))
        l = min(o, c) * (1 - random.uniform(0, 0.008))
        candles.append({
            "timestamp": 1600000000000 + i * 4 * 3600000,
            "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(c, 2),
            "volume": round(random.uniform(200, 800), 2),
        })
        price = c

    # 选择策略
    # 多策略投票阈值（0.0 表示过半即触发，适合分散信号的多策略组合）
    VOTE_THRESHOLD = float(os.getenv("LIVE_VOTE_THRESHOLD", "0.0"))
    if strategy_name == "MultiVote":
        rsi_s = RSIStrategy(StrategyConfig(symbol="ETH/USDT", timeframe="4h", stop_loss=0.05, take_profit=0.10))
        sma_s = SMAcrossStrategy(StrategyConfig(symbol="ETH/USDT", timeframe="4h", stop_loss=0.05, take_profit=0.10))
        macd_s = FormulaStrategy(formula=BUILTIN_FORMULAS["MACD"], symbol="ETH/USDT", stop_loss=0.05, take_profit=0.10)
        vote = MultiStrategyVote(
            [(rsi_s, 0.4), (sma_s, 0.3), (macd_s, 0.3)],
            threshold=VOTE_THRESHOLD,
            name="RSI40%+SMA30%+MACD30%",
        )
        strategy = vote
    elif strategy_name in BUILTIN_FORMULAS:
        strategy = FormulaStrategy(
            formula=BUILTIN_FORMULAS[strategy_name],
            symbol="ETH/USDT",
            timeframe="4h",
            stop_loss=0.05, take_profit=0.10,
        )
    else:
        raise HTTPException(status_code=404, detail=f"未知策略: {strategy_name}")

    # 计算指标
    indicators = strategy.populate_indicators(candles)
    entry_signals = strategy.populate_entry_trend(candles)
    try:
        exit_signals = strategy.populate_exit_trend(candles)
    except Exception:
        exit_signals = [0] * len(candles)

    # 简化指标序列（只返回最后 200 个，前 100 个数据不足）
    n_show = min(250, n)
    start = max(0, n - n_show)
    candles_show = candles[start:]
    timestamps = [c["timestamp"] for c in candles_show]
    closes = [c["close"] for c in candles_show]

    # K线数据（前端 lightweight-charts 用）
    ohlc = [
        {"t": c["timestamp"] // 1000, "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"]}
        for c in candles_show
    ]

    # Equity Curve（模拟）
    equity = 10000.0
    equity_curve = []
    in_pos = False
    entry_price = 0.0
    for i, c in enumerate(candles_show):
        if in_pos:
            pnl = (c["close"] - entry_price) / entry_price
            if pnl <= -0.05:
                equity *= (1 + pnl * 0.5)
                in_pos = False
            elif pnl >= 0.10:
                equity *= (1 + pnl * 0.9)
                in_pos = False
        if entry_signals[start + i] == 1 and not in_pos:
            in_pos = True
            entry_price = c["close"]
        equity_curve.append({"t": c["timestamp"] // 1000, "v": round(equity, 2)})

    # 买卖点
    buy_markers = []
    sell_markers = []
    for i in range(len(candles_show)):
        idx = start + i
        if entry_signals[idx] == 1:
            buy_markers.append({
                "t": candles_show[i]["timestamp"] // 1000,
                "price": candles_show[i]["close"],
                "label": "买入",
            })
        if exit_signals[idx] == -1 or (exit_signals[idx] == 0 and entry_signals[idx] == 0 and i > 0 and entry_signals[idx - 1] == 1):
            # 简化：每次持仓结束视为卖出
            pass
        # 用 equity 不变来判断持仓结束（简化逻辑）
        if i > 0 and in_pos and abs(equity_curve[i]["v"] - equity_curve[i - 1]["v"]) < 0.01:
            if i == len(candles_show) - 1 or entry_signals[min(idx + 1, n - 1)] == 0:
                sell_markers.append({
                    "t": candles_show[i]["timestamp"] // 1000,
                    "price": candles_show[i]["close"],
                    "label": "卖出",
                })
                in_pos = False

    # 指标线
    indicators_out = {}
    for k, v in indicators.items():
        valid = [x for x in v[start:] if x != 0]
        if valid:
            indicators_out[k] = [{"t": timestamps[i] // 1000, "v": round(val, 4)}
                                  for i, val in enumerate(v[start:]) if val != 0]

    # 策略投票权重
    strategy_weights = {}
    if strategy_name == "MultiVote":
        for s, w in vote.strategies:
            strategy_weights[s.__class__.__name__] = w

    return {
        "strategy": strategy_name,
        "ohlc": ohlc,
        "equity_curve": equity_curve,
        "buy_markers": buy_markers,
        "sell_markers": sell_markers,
        "indicators": indicators_out,
        "weights": strategy_weights,
    }


@app.get("/api/backtest/strategies")
async def list_backtest_strategies():
    """列出所有可回测的策略"""
    return {
        "single": list(BUILTIN_FORMULAS.keys()),
        "multi": ["MultiVote"],
    }

# ========== HTML Dashboard ==========

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>交易监控系统</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #1a1a2e; color: #eee; }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { color: #00d4ff; text-align: center; margin-bottom: 30px; }
        h2 { color: #00d4ff; border-bottom: 1px solid #333; padding-bottom: 10px; margin-top: 30px; }
        .card { background: #16213e; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        .status-badge { display: inline-block; padding: 5px 15px; border-radius: 20px; font-size: 12px; font-weight: bold; }
        .status-running { background: #00c853; color: #000; }
        .status-stopped { background: #ff1744; color: #fff; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px 8px; text-align: left; border-bottom: 1px solid #333; }
        th { background: #0f3460; color: #00d4ff; }
        tr:hover { background: #1f4068; }
        .price-up { color: #00e676; }
        .price-down { color: #ff1744; }
        .profit { color: #00e676; }
        .loss { color: #ff1744; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: bold; transition: all 0.3s; }
        .btn-primary { background: #00d4ff; color: #000; }
        .btn-success { background: #00c853; color: #000; }
        .btn-danger { background: #ff1744; color: #fff; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.4); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        input, select { padding: 10px 15px; border: 1px solid #333; border-radius: 8px; background: #0f3460; color: #fff; font-size: 14px; width: 100%; margin-bottom: 10px; }
        .form-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
        .tab-bar { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        .tab { padding: 10px 20px; background: #16213e; border: none; border-radius: 8px; color: #888; cursor: pointer; }
        .tab.active { background: #00d4ff; color: #000; font-weight: bold; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .refresh-info { text-align: right; color: #666; font-size: 12px; margin-top: 10px; }
        .actions { display: flex; gap: 10px; margin: 15px 0; flex-wrap: wrap; }
    </style>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
    <div class="container">
        <h1>📊 交易监控系统 v2.0 <span id="live-badge" style="font-size:14px;color:#ff9800;display:none;">● 实盘模式</span></h1>
        
        <!-- 系统状态 -->
        <div class="card">
            <h2>系统状态</h2>
            <div class="grid">
                <div>
                    <p>行情监控: <span id="monitor-status" class="status-badge status-stopped">检测中...</span></p>
                    <p>实盘状态: <span id="live-status" class="status-badge" style="background:#555;color:#fff;">模拟模式</span></p>
                    <p>门下省: <span id="menxia-status" class="status-badge" style="background:#555;color:#fff;">离线</span></p>
                    <p>尚书省: <span id="shangshu-status" class="status-badge" style="background:#555;color:#fff;">离线</span></p>
                </div>
                <div class="actions">
                    <button class="btn btn-success" onclick="controlMonitor('start')">▶ 启动监控</button>
                    <button class="btn btn-danger" onclick="controlMonitor('stop')">■ 停止监控</button>
                    <button class="btn btn-primary" onclick="testAlert()">🔔 测试告警</button>
                </div>
            </div>
        </div>
        
        <!-- Tab 导航 -->
        <div class="tab-bar">
            <button class="tab active" onclick="switchTab('market')">📈 实时行情</button>
            <button class="tab" onclick="switchTab('portfolio')">💼 持仓管理</button>
            <button class="tab" onclick="switchTab('trade')">💰 交易操作</button>
            <button class="tab" onclick="switchTab('alerts')">🔔 告警记录</button>
            <button class="tab" onclick="switchTab('backtest')">📊 回测图表</button>
        </div>
        
        <!-- 实时行情 -->
        <div id="tab-market" class="tab-content active">
            <div class="card">
                <h2>实时行情</h2>
                <div id="market-prices">加载中...</div>
                <p class="refresh-info">自动刷新间隔: 30秒 | <button class="btn btn-primary" onclick="loadAll()">🔄 手动刷新</button></p>
            </div>
        </div>
        
        <!-- 持仓管理 -->
        <div id="tab-portfolio" class="tab-content">
            <div class="card">
                <h2>当前持仓</h2>
                <div id="positions">加载中...</div>
            </div>
            <div class="card">
                <h2>市值统计</h2>
                <div id="portfolio-value">加载中...</div>
            </div>
        </div>
        
        <!-- 交易操作 -->
        <div id="tab-trade" class="tab-content">
            <div class="card">
                <h2>买入/卖出</h2>
                <div class="form-row">
                    <div>
                        <label>市场</label>
                        <select id="trade-market">
                            <option value="CN">A股</option>
                            <option value="HK">港股</option>
                            <option value="US">美股</option>
                            <option value="CRYPTO">加密货币</option>
                        </select>
                    </div>
                    <div>
                        <label>代码</label>
                        <input type="text" id="trade-symbol" placeholder="如: BTC, AAPL, 600000">
                    </div>
                    <div>
                        <label>数量</label>
                        <input type="number" id="trade-quantity" placeholder="数量">
                    </div>
                    <div>
                        <label>价格</label>
                        <input type="number" id="trade-price" placeholder="价格">
                    </div>
                </div>
                <div class="actions">
                    <button class="btn btn-success" onclick="executeTrade('buy')">✅ 买入</button>
                    <button class="btn btn-danger" onclick="executeTrade('sell')">✅ 卖出</button>
                </div>
                <div id="trade-result"></div>
            </div>
        </div>
        
        <!-- 告警记录 -->
        <div id="tab-alerts" class="tab-content">
            <div class="card">
                <h2>告警历史</h2>
                <div id="alerts">加载中...</div>
            </div>
        </div>

        <!-- 回测图表 P2 -->
        <div id="tab-backtest" class="tab-content">
            <div class="card">
                <h2>📊 回测图表 — Equity Curve + K线买卖点</h2>
                <div class="form-row" style="margin-bottom:15px;">
                    <div>
                        <label>策略选择</label>
                        <select id="bt-strategy" onchange="loadBacktestChart()">
                            <optgroup label="单策略">
                                <option value="KDJ">KDJ 随机指标</option>
                                <option value="MACD" selected>MACD 指数平滑</option>
                                <option value="RSI">RSI 相对强弱</option>
                                <option value="CCI">CCI 顺势指标</option>
                                <option value="BOLL">BOLL 布林带</option>
                                <option value="WR">WR 威廉指标</option>
                                <option value="MA_CROSS">MA 均线交叉</option>
                            </optgroup>
                            <optgroup label="多策略投票">
                                <option value="MultiVote">多策略投票 (RSI 40% + SMA 30% + MACD 30%)</option>
                            </optgroup>
                        </select>
                    </div>
                    <div style="display:flex;align-items:flex-end;">
                        <button class="btn btn-primary" onclick="loadBacktestChart()">🔄 加载图表</button>
                    </div>
                </div>

                <!-- 策略权重显示（MultiVote 时） -->
                <div id="bt-weights" style="display:none;margin-bottom:10px;font-size:13px;color:#aaa;"></div>

                <!-- Equity Curve 图 -->
                <div class="card" style="background:#0d1b2a;">
                    <h3 style="color:#00d4ff;margin:0 0 10px 0;">💰 Equity Curve（权益曲线）</h3>
                    <div id="equity-chart" style="height:200px;"></div>
                </div>

                <!-- K线图 -->
                <div class="card" style="background:#0d1b2a;">
                    <h3 style="color:#00d4ff;margin:0 0 10px 0;">📈 K线 + 买卖点标注</h3>
                    <div id="candlestick-chart" style="height:320px;"></div>
                </div>

                <!-- 指标图 -->
                <div class="card" style="background:#0d1b2a;">
                    <h3 style="color:#00d4ff;margin:0 0 10px 0;">📉 技术指标</h3>
                    <div id="indicator-chart" style="height:180px;"></div>
                </div>

                <!-- 信号统计 -->
                <div id="bt-stats" style="margin-top:10px;font-size:13px;color:#aaa;"></div>
            </div>
        </div>
    </div>
    
    <script>
        // 加载所有数据
        async function loadAll() {
            await loadMarketPrices();
            await loadPositions();
            await loadPortfolioValue();
            await loadAlerts();
            await loadMonitorStatus();
            await loadSanshengStatus();
        }
        
        async function loadSanshengStatus() {
            try {
                const res = await fetch('/api/sansheng/status');
                const data = await res.json();

                // 实盘模式标签
                const liveBadge = document.getElementById('live-badge');
                if (data.live_trading) {
                    liveBadge.style.display = 'inline';
                }

                // 实盘状态
                const liveEl = document.getElementById('live-status');
                if (data.live_trading) {
                    liveEl.textContent = data.testnet ? '测试网' : '实盘';
                    liveEl.style.background = data.testnet ? '#ff9800' : '#00c853';
                    liveEl.style.color = '#000';
                } else {
                    liveEl.textContent = '模拟模式';
                    liveEl.style.background = '#555';
                }

                // 门下省状态
                const mxEl = document.getElementById('menxia-status');
                if (data.menxia_available) {
                    const mx = data.menxia || {};
                    const levelColors = {'normal': '#00c853', 'caution': '#ff9800', 'warning': '#ff5722', 'locked': '#ff1744'};
                    mxEl.textContent = mx.level ? `正常(${mx.level})` : '正常';
                    mxEl.style.background = levelColors[mx.level] || '#00c853';
                    mxEl.style.color = '#000';
                } else {
                    mxEl.textContent = '未启用';
                    mxEl.style.background = '#555';
                }

                // 尚书省状态
                const ssEl = document.getElementById('shangshu-status');
                if (data.shangshu_available && data.live_trading) {
                    ssEl.textContent = data.exchange || '已连接';
                    ssEl.style.background = '#00c853';
                    ssEl.style.color = '#000';
                } else {
                    ssEl.textContent = '离线';
                    ssEl.style.background = '#555';
                }
            } catch(e) { console.error('三省六部状态加载失败:', e); }
        }
        
        async function loadMonitorStatus() {
            try {
                const res = await fetch('/api/system/status');
                const data = await res.json();
                const status = data.monitor?.status || 'stopped';
                const el = document.getElementById('monitor-status');
                el.textContent = status === 'running' ? '运行中' : '已停止';
                el.className = 'status-badge ' + (status === 'running' ? 'status-running' : 'status-stopped');
            } catch(e) { console.error(e); }
        }
        
        async function controlMonitor(action) {
            try {
                await fetch('/api/monitor', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({action})
                });
                await loadMonitorStatus();
                alert(action === 'start' ? '监控已启动' : '监控已停止');
            } catch(e) { alert('操作失败: ' + e); }
        }
        
        async function loadMarketPrices() {
            try {
                const res = await fetch('/api/market/prices');
                const prices = await res.json();
                if (prices && prices.length) {
                    document.getElementById('market-prices').innerHTML = `
                        <table>
                            <tr><th>市场</th><th>代码</th><th>名称</th><th>最新价</th><th>涨跌</th><th>24h高</th><th>24h低</th><th>成交量</th></tr>
                            ${prices.map(p => {
                                const chg = p.change_pct || 0;
                                const cls = chg >= 0 ? 'price-up' : 'price-down';
                                const sign = chg >= 0 ? '+' : '';
                                const vol = p.volume_24h || p.volume || 0;
                                const volNum = parseFloat(vol) || 0;
                                const volStr = volNum > 1e8 ? (volNum/1e8).toFixed(2) + '亿' : volNum > 1e4 ? (volNum/1e4).toFixed(2) + '万' : volNum.toFixed(2);
                                return `<tr>
                                    <td>${p.market}</td>
                                    <td><b>${p.symbol}</b></td>
                                    <td>${p.name || '-'}</td>
                                    <td class="${cls}">¥${(p.price||0).toLocaleString()}</td>
                                    <td class="${cls}">${sign}${chg.toFixed(2)}%</td>
                                    <td>¥${(p.high_24h||p.high||0).toLocaleString()}</td>
                                    <td>¥${(p.low_24h||p.low||0).toLocaleString()}</td>
                                    <td>${volStr}</td>
                                </tr>`;
                            }).join('')}
                        </table>`;
                } else {
                    document.getElementById('market-prices').innerHTML = '<p>暂无行情数据</p>';
                }
            } catch(e) { document.getElementById('market-prices').innerHTML = '<p>加载失败: ' + e.message + '</p>'; }
        }
        
        async function loadPositions() {
            try {
                const res = await fetch('/api/positions');
                const positions = await res.json();
                if (positions && positions.length) {
                    document.getElementById('positions').innerHTML = `
                        <table>
                            <tr><th>代码</th><th>市场</th><th>数量</th><th>成本价</th><th>当前价</th><th>盈亏</th><th>盈亏率</th></tr>
                            ${positions.map(p => {
                                const pnl = p.pnl || 0;
                                const pnl_pct = p.pnl_pct || 0;
                                const cls = pnl >= 0 ? 'profit' : 'loss';
                                return `<tr>
                                    <td><b>${p.symbol}</b></td>
                                    <td>${p.market}</td>
                                    <td>${p.quantity}</td>
                                    <td>¥${p.avg_price?.toFixed(4) || 0}</td>
                                    <td>¥${p.current_price?.toFixed(4) || '-'}</td>
                                    <td class="${cls}">¥${pnl.toFixed(2)}</td>
                                    <td class="${cls}">${pnl_pct.toFixed(2)}%</td>
                                </tr>`;
                            }).join('')}
                        </table>`;
                } else {
                    document.getElementById('positions').innerHTML = '<p>暂无持仓 - 请在"交易操作"中添加</p>';
                }
            } catch(e) { document.getElementById('positions').innerHTML = '<p>加载失败</p>'; }
        }
        
        async function loadPortfolioValue() {
            try {
                const res = await fetch('/api/portfolio/value');
                const value = await res.json();
                const pnl = value.total_pnl || 0;
                const pnl_pct = value.total_pnl_pct || 0;
                const cls = pnl >= 0 ? 'profit' : 'loss';
                document.getElementById('portfolio-value').innerHTML = `
                    <table>
                        <tr><th>总成本</th><th>总市值</th><th>总盈亏</th><th>盈亏率</th></tr>
                        <tr>
                            <td>¥${(value.total_cost||0).toFixed(2)}</td>
                            <td>¥${(value.total_value||0).toFixed(2)}</td>
                            <td class="${cls}">¥${pnl.toFixed(2)}</td>
                            <td class="${cls}">${pnl_pct.toFixed(2)}%</td>
                        </tr>
                    </table>`;
            } catch(e) { document.getElementById('portfolio-value').innerHTML = '<p>加载失败</p>'; }
        }
        
        async function loadAlerts() {
            try {
                const res = await fetch('/api/alerts');
                const alerts = await res.json();
                if (alerts && alerts.length) {
                    document.getElementById('alerts').innerHTML = `
                        <table>
                            <tr><th>时间</th><th>市场</th><th>代码</th><th>类型</th><th>价格</th><th>说明</th></tr>
                            ${alerts.slice(0,50).map(a => `<tr>
                                <td>${new Date(a.created_at).toLocaleString()}</td>
                                <td>${a.market}</td>
                                <td><b>${a.symbol}</b></td>
                                <td>${a.alert_type}</td>
                                <td>¥${a.price}</td>
                                <td>${a.message || '-'}</td>
                            </tr>`).join('')}
                        </table>`;
                } else {
                    document.getElementById('alerts').innerHTML = '<p>暂无告警记录</p>';
                }
            } catch(e) { document.getElementById('alerts').innerHTML = '<p>加载失败</p>'; }
        }
        
        async function executeTrade(action) {
            const symbol = document.getElementById('trade-symbol').value.trim();
            const market = document.getElementById('trade-market').value;
            const quantity = parseFloat(document.getElementById('trade-quantity').value);
            const price = parseFloat(document.getElementById('trade-price').value);
            
            if (!symbol || !quantity || !price) {
                alert('请填写完整交易信息');
                return;
            }
            
            try {
                const res = await fetch('/api/trade', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({symbol, market, quantity, price, action})
                });
                const result = await res.json();
                document.getElementById('trade-result').innerHTML = 
                    `<p style="color:${result.success?'#00e676':'#ff1744'};margin-top:10px;">${result.message}</p>`;
                if (result.success) {
                    loadPositions();
                    loadPortfolioValue();
                }
            } catch(e) {
                document.getElementById('trade-result').innerHTML = '<p style="color:#ff1744">交易失败: ' + e.message + '</p>';
            }
        }
        
        async function testAlert() {
            if (confirm('确定发送测试告警到飞书？')) {
                try {
                    await fetch('/api/alert/test', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            symbol: 'BTC',
                            market: 'CRYPTO',
                            alert_type: '测试告警',
                            price: 67142.50,
                            threshold: 5,
                            message: '交易监控系统告警测试'
                        })
                    });
                    alert('测试告警已发送，请检查飞书群');
                } catch(e) { alert('发送失败: ' + e); }
            }
        }
        
        function switchTab(tab) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab[onclick="switchTab('${tab}')"]`).classList.add('active');
            document.getElementById('tab-' + tab).classList.add('active');
            if (tab === 'backtest') loadBacktestChart();
        }

        // ================================================================
        // P2 回测图表
        // ================================================================

        let equityChart = null;
        let candleChart = null;
        let indicatorChart = null;
        let loadedStrategy = null;

        async function loadBacktestChart() {
            const strategy = document.getElementById('bt-strategy').value;
            const weightsDiv = document.getElementById('bt-weights');
            const statsDiv = document.getElementById('bt-stats');

            try {
                const res = await fetch(`/api/backtest/chart/${strategy}`);
                if (!res.ok) throw new Error('加载失败');
                const data = await res.json();

                // 显示权重
                if (data.weights && Object.keys(data.weights).length > 0) {
                    const wParts = Object.entries(data.weights).map(([k, v]) => `${k} ${(v*100).toFixed(0)}%`).join(' + ');
                    weightsDiv.style.display = 'block';
                    weightsDiv.innerHTML = `投票权重：${wParts}（阈值=${strategy === 'MultiVote' ? '0.3' : '-'})`;
                } else {
                    weightsDiv.style.display = 'none';
                }

                // 加载 equity chart
                if (!equityChart) {
                    equityChart = LightweightCharts.createChart(document.getElementById('equity-chart'), {
                        width: document.getElementById('equity-chart').clientWidth || 800,
                        height: 200,
                        layout: { backgroundColor: '#0d1b2a', textColor: '#aaa' },
                        grid: { vertLines: { color: '#1a2a3a' }, horzLines: { color: '#1a2a3a' } },
                    });
                }
                const eqSeries = equityChart.addAreaSeries({
                    topColor: 'rgba(0,212,255,0.4)', bottomColor: 'rgba(0,212,255,0.05)', lineColor: '#00d4ff', lineWidth: 2
                });
                eqSeries.setData(data.equity_curve.map(d => ({ time: d.t, value: d.v })));
                equityChart.timeScale().fitContent();

                // 加载 K线图
                if (!candleChart) {
                    candleChart = LightweightCharts.createChart(document.getElementById('candlestick-chart'), {
                        width: document.getElementById('candlestick-chart').clientWidth || 800,
                        height: 320,
                        layout: { backgroundColor: '#0d1b2a', textColor: '#aaa' },
                        grid: { vertLines: { color: '#1a2a3a' }, horzLines: { color: '#1a2a3a' } },
                    });
                }
                const candleSeries = candleChart.addCandlestickSeries({ upColor: '#00e676', downColor: '#ff1744', borderVisible: false });
                candleSeries.setData(data.ohlc.map(d => ({ time: d.t, open: d.o, high: d.h, low: d.l, close: d.c })));

                // 买标记
                if (data.buy_markers && data.buy_markers.length > 0) {
                    candleSeries.createPriceLine({
                        price: data.buy_markers[0].price, color: '#00e676', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '买入'
                    });
                }
                candleChart.timeScale().fitContent();

                // 指标图
                if (!indicatorChart) {
                    indicatorChart = LightweightCharts.createChart(document.getElementById('indicator-chart'), {
                        width: document.getElementById('indicator-chart').clientWidth || 800,
                        height: 180,
                        layout: { backgroundColor: '#0d1b2a', textColor: '#aaa' },
                        grid: { vertLines: { color: '#1a2a3a' }, horzLines: { color: '#1a2a3a' } },
                    });
                }
                const indKeys = Object.keys(data.indicators || {});
                if (indKeys.length > 0) {
                    const firstInd = indKeys[0];
                    const indSeries = indicatorChart.addLineSeries({
                        color: '#ff9800', lineWidth: 1, title: firstInd
                    });
                    indSeries.setData(data.indicators[firstInd].map(d => ({ time: d.t, value: d.v })));
                    indicatorChart.timeScale().fitContent();
                }

                // 信号统计
                const buyCount = (data.buy_markers || []).length;
                const sellCount = (data.sell_markers || []).length;
                const eqFinal = data.equity_curve[data.equity_curve.length - 1].v;
                const eqInit = data.equity_curve[0].v;
                const retPct = ((eqFinal - eqInit) / eqInit * 100).toFixed(2);
                statsDiv.innerHTML = `策略: <b>${data.strategy}</b> | 买入信号: ${buyCount} 次 | 卖出信号: ${sellCount} 次 | 最终权益: $${eqFinal.toFixed(2)} | 模拟收益率: <b style="color:${retPct >= 0 ? '#00e676' : '#ff1744'}">${retPct >= 0 ? '+' : ''}${retPct}%</b>`;

            } catch(e) {
                statsDiv.innerHTML = `<span style="color:#ff1744;">图表加载失败: ${e.message}</span>`;
            }
        }

        // 初始化
        loadAll();
        setInterval(loadAll, 30000);  // 每30秒刷新
    </script>
</body>
</html>
"""

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML

def run_server(host: str = "0.0.0.0", port: int = 8081):
    """启动 Web 服务"""
    init_db()
    uvicorn.run(app, host=host, port=port, log_level="warning")

if __name__ == "__main__":
    print("启动交易监控系统 Dashboard...")
    run_server()
