"""
Web Dashboard 模块 - 交易监控系统
所有功能集成到 Web 界面
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import asyncio
import os
import time

from portfolio import Portfolio
from monitor import quick_price_check
from feishu_alert import FeishuAlert
feishu_alert = FeishuAlert()
from trade_history import TradeHistory
from components.market_regime import MarketRegime
from components.auditor import Auditor
from database import init_db, get_positions, get_trades, get_alerts
from config import PRICE_CHECK_INTERVAL

app = FastAPI(title="交易监控系统", version="2.0.0")

# Agent Gateway路由
try:
    from agent_gateway.fastapi_routes import agent_router
    app.include_router(agent_router)
    print("[Dashboard] Agent Gateway /api/agent/v1 mounted")
except Exception as e:
    print(f"[Dashboard] Agent Gateway not available: {e}")

portfolio = Portfolio()

# 全局监控状态
_monitor_status = {"status": "stopped", "message": "未启动"}
_start_time = None

# 复盘模块实例（全工作线程共享，避免重复初始化）
_replay_th = None
_replay_mr = None
_replay_auditor = None

def _get_replay_modules():
    global _replay_th, _replay_mr, _replay_auditor
    if _replay_th is None:
        _replay_th = TradeHistory()
    if _replay_mr is None:
        _replay_mr = MarketRegime()
    if _replay_auditor is None:
        _replay_auditor = Auditor()
    return _replay_th, _replay_mr, _replay_auditor


# ================================================================
# P3 交易复盘 API
# ================================================================

@app.get("/api/replay/stats")
async def get_replay_stats():
    """综合绩效统计：胜率/盈亏比/期望值/最大回撤"""
    try:
        th, mr, _ = _get_replay_modules()
        stats = th.get_performance_stats()
        # 补充市场状态分布（通过 MarketRegime 查询）
        try:
            recent = mr.get_historical_regime(symbol="BTC/USDT", hours_back=720)
            trend_counts = {}
            for r in recent:
                t = r.get("trend") or "unknown"
                trend_counts[t] = trend_counts.get(t, 0) + 1
            regime_dist = [{"regime": k, "count": v} for k, v in trend_counts.items()]
        except Exception:
            regime_dist = []
        return {**stats, "regime_distribution": regime_dist}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/replay/trades")
async def get_replay_trades(limit: int = 50):
    """交易历史列表（含市场状态标注）"""
    try:
        th, mr, _ = _get_replay_modules()
        trades = th.get_recent_trades(limit)
        enriched = []
        for t in trades:
            entry_time = t.get("opened_at") or t.get("open_time") or ""
            if entry_time:
                regime = mr.get_regime_at(str(entry_time)[:19])
                t["market_trend"] = regime.get("trend", "unknown") if regime else "unknown"
                t["market_volatility"] = regime.get("volatility", "unknown") if regime else "unknown"
                t["market_volume"] = regime.get("volume", "unknown") if regime else "unknown"
            else:
                t["market_trend"] = "unknown"
                t["market_volatility"] = "unknown"
                t["market_volume"] = "unknown"
            enriched.append(t)
        return enriched
    except Exception as e:
        return []


@app.get("/api/replay/heatmap")
async def get_replay_heatmap():
    """策略 × 市场状态 热力图"""
    try:
        th, _, _ = _get_replay_modules()
        heatmap = th.get_pnl_by_market_regime()
        return heatmap if heatmap else {}
    except Exception as e:
        return {}


@app.get("/api/replay/exit_analysis")
async def get_replay_exit_analysis():
    """按出场原因分析：止损/止盈/其他"""
    try:
        th, _, _ = _get_replay_modules()
        return th.get_pnl_by_exit_reason()
    except Exception as e:
        return {}


@app.get("/api/replay/audit")
async def get_replay_audit_report():
    """最新审计报告"""
    try:
        _, _, auditor = _get_replay_modules()
        report = auditor.run_audit(days_back=30)
        return report
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/replay/run-audit")
async def run_replay_audit():
    """手动触发审计并保存报告"""
    try:
        _, _, auditor = _get_replay_modules()
        report = auditor.run_audit_and_save(days_back=30)
        return {"success": True, "report": report}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ================================================================
# P2 回测图表 API（Equity Curve + 买卖点标注）
# ================================================================

@app.get("/api/signal/review")
async def get_signal_review():
    """返回信号复盘统计"""
    try:
        from signal_review import get_review
        review = get_review()
        summary = review.get_recent_summary(20)
        patterns = review.get_error_patterns(5)
        return {
            "quality_score": review.get_signal_quality_score(),
            "accuracy": summary["accuracy"],
            "avg_confidence": summary["avg_confidence"],
            "total_reviewed": summary["total"],
            "error_patterns": [{"pattern": p, "count": c} for p, c in patterns],
            "recommendation": "proceed" if summary["accuracy"] >= 0.5 else "caution"
        }
    except Exception as e:
        return {"error": str(e), "quality_score": 0.5, "recommendation": "unknown"}


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

class ModeRequest(BaseModel):
    mode: str  # 'live' or 'sim'
    token: str  # 必须提供 AGENT_TOKEN 才能切换

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

# 实盘切换保护状态（内存中，不能完全防重启擦除，但能防误操作）
_last_mode_change = {"time": 0, "cooldown_seconds": 10}

@app.post("/api/trading/mode")
async def set_trading_mode(req: ModeRequest):
    """切换实盘/模拟模式 - 必须提供有效token，且有10秒冷却"""
    # 1. Token 验证 — 必须设置了 AGENT_TOKEN 才能切换实盘
    expected_token = os.getenv("AGENT_TOKEN")
    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="系统未配置 AGENT_TOKEN，无法切换实盘模式（安全保护）"
        )
    if req.token != expected_token:
        raise HTTPException(status_code=403, detail="无效Token，拒绝切换")

    # 2. 冷却保护：防止重复快速切换
    now = time.time()
    if now - _last_mode_change["time"] < _last_mode_change["cooldown_seconds"]:
        elapsed = round(now - _last_mode_change["time"], 1)
        raise HTTPException(
            status_code=429,
            detail=f"切换过于频繁，请 {round(_last_mode_change['cooldown_seconds'] - elapsed)} 秒后再试"
        )

    if req.mode not in ("live", "sim"):
        raise HTTPException(status_code=400, detail="模式必须是 'live' 或 'sim'")

    import config
    config.LIVE_TRADING_ENABLED = (req.mode == "live")
    _last_mode_change["time"] = now

    return {
        "success": True,
        "mode": req.mode,
        "message": f"已切换到{'实盘' if req.mode == 'live' else '模拟'}模式"
    }

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
    注意: 耗时的数据获取在线程池中执行，超时30秒
    """
    async def _heavy_fetch(
        code_list: list, start_date: str, end_date: str
    ):
        """在线程池中运行阻塞型数据获取"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,  # 默认 ThreadPoolExecutor
            lambda: fetch_stock_data(code_list, start_date, end_date)
        )

    try:
        import pandas as pd
        from vibe_integration.stock_backtest import fetch_stock_data, SimpleMASignal, RSISignal

        # 最多等待30秒，超时则返回504
        code_list = [c.strip() for c in codes.split(",")]
        try:
            data_map = await asyncio.wait_for(
                _heavy_fetch(code_list, start_date, end_date),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="数据获取超时（>30秒），请减少标的数量或稍后重试")
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

@app.get("/api/signal/review")
async def get_signal_review():
    """返回信号复盘统计"""
    try:
        from signal_review import get_review
        review = get_review()
        summary = review.get_recent_summary(20)
        patterns = review.get_error_patterns(5)
        return {
            "quality_score": review.get_signal_quality_score(),
            "accuracy": summary["accuracy"],
            "avg_confidence": summary["avg_confidence"],
            "total_reviewed": summary["total"],
            "error_patterns": [{"pattern": p, "count": c} for p, c in patterns],
            "recommendation": "proceed" if summary["accuracy"] >= 0.5 else "caution"
        }
    except Exception as e:
        return {"error": str(e), "quality_score": 0.5, "recommendation": "unknown"}

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
        use_confidence = True
    elif strategy_name in BUILTIN_FORMULAS:
        strategy = FormulaStrategy(
            formula=BUILTIN_FORMULAS[strategy_name],
            symbol="ETH/USDT",
            timeframe="4h",
            stop_loss=0.05, take_profit=0.10,
        )
        use_confidence = False
    else:
        raise HTTPException(status_code=404, detail=f"未知策略: {strategy_name}")

    # 简化指标序列（只返回最后 200 个，前 100 个数据不足）
    n_show = min(250, n)
    start = max(0, n - n_show)

    # 计算指标
    indicators = strategy.populate_indicators(candles)
    if use_confidence:
        entry_signals, confidences = strategy.populate_signals_with_confidence(candles)
        confidence_map = {start + i: confidences[i] for i in range(len(confidences))}
    else:
        entry_signals = strategy.populate_entry_trend(candles)
        confidence_map = {}
    try:
        exit_signals = strategy.populate_exit_trend(candles)
    except Exception:
        exit_signals = [0] * len(candles)
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
            conf = confidence_map.get(idx, 0.0)
            conf_label = "🟢" if conf >= 0.7 else ("🟡" if conf >= 0.4 else "🔴")
            buy_markers.append({
                "t": candles_show[i]["timestamp"] // 1000,
                "price": candles_show[i]["close"],
                "label": f"买入 {conf_label}{conf:.2f}",
                "confidence": conf,
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

    # 指标线（修复：indicator数组可能比candles_show长，用min防止越界）
    indicators_out = {}
    n_candles_show = len(candles_show)
    for k, v in indicators.items():
        if len(v) <= start:
            continue
        valid = [x for x in v[start:] if x != 0]
        if valid:
            indicators_out[k] = [{"t": candles_show[min(i, n_candles_show - 1)]["timestamp"] // 1000, "v": round(val, 4)}
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
        "confidence_enabled": use_confidence,
        "signal_stats": {
            "total_signals": sum(1 for s in entry_signals if s != 0),
            "high_conf": sum(1 for c in confidences if c >= 0.7) if use_confidence else 0,
            "low_conf": sum(1 for c in confidences if 0 < c < 0.4) if use_confidence else 0,
        }
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
        .market-tabs { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
        .market-tab { padding: 12px 24px; background: #0f3460; border: 2px solid #1a2a3a; border-radius: 10px; color: #888; cursor: pointer; font-size: 16px; font-weight: bold; transition: all 0.3s; display: flex; align-items: center; gap: 8px; }
        .market-tab:hover { border-color: #00d4ff; color: #fff; }
        .market-tab.active { border-color: #00d4ff; color: #00d4ff; background: #0f3460; }
        .market-tab.cn.active { border-color: #ff4444; color: #ff4444; }
        .market-tab.hk.active { border-color: #00b140; color: #00b140; }
        .market-tab.us.active { border-color: #0066cc; color: #0066cc; }
        .market-tab.crypto.active { border-color: #f7931a; color: #f7931a; }
        .mode-toggle { display: flex; gap: 10px; margin-left: auto; align-items: center; }
        .mode-btn { padding: 8px 16px; border: 2px solid #333; border-radius: 8px; background: #16213e; color: #888; cursor: pointer; font-weight: bold; transition: all 0.3s; }
        .mode-btn:hover { border-color: #00d4ff; }
        .mode-btn.active { border-color: #00d4ff; background: #00d4ff; color: #000; }
        .mode-btn.live.active { border-color: #00c853; background: #00c853; }
        .index-card { display: flex; align-items: center; gap: 15px; padding: 15px; background: #0f3460; border-radius: 10px; margin-bottom: 10px; }
        .index-icon { font-size: 28px; }
        .index-info { flex: 1; }
        .index-name { color: #888; font-size: 12px; }
        .index-value { font-size: 20px; font-weight: bold; color: #fff; }
        .index-change { font-size: 14px; }
        .price-up { color: #00e676; }
        .price-down { color: #ff1744; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .refresh-info { text-align: right; color: #666; font-size: 12px; margin-top: 10px; }
        .actions { display: flex; gap: 10px; margin: 15px 0; flex-wrap: wrap; }
        /* 系统状态卡片 */
        .sys-stat-card { background: #0f3460; border-radius: 10px; padding: 14px 16px; border-left: 3px solid #00d4ff; transition: all 0.3s; }
        .sys-stat-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
        .sys-stat-card.running { border-left-color: #00c853; }
        .sys-stat-card.warning { border-left-color: #ff9800; }
        .sys-stat-card.danger { border-left-color: #ff1744; }
        .sys-stat-label { font-size: 11px; color: #888; margin-bottom: 6px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; }
        .sys-stat-value { font-size: 14px; font-weight: bold; }
        .sys-stat-card .status-badge { font-size: 11px; padding: 4px 10px; }
        /* 运行时间等统计 */
        #uptime-display { font-family: 'Courier New', monospace; }
    </style>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
    <div class="container">
        <h1>📊 交易监控系统 v2.0</h1>
        
        <!-- 市场切换 Tab -->
        <div class="market-tabs">
            <button class="market-tab cn active" onclick="switchMarket('CN')" id="tab-cn">
                🇨🇳 A股
            </button>
            <button class="market-tab hk" onclick="switchMarket('HK')" id="tab-hk">
                🇭🇰 港股
            </button>
            <button class="market-tab us" onclick="switchMarket('US')" id="tab-us">
                🇺🇸 美股
            </button>
            <button class="market-tab crypto" onclick="switchMarket('CRYPTO')" id="tab-crypto">
                ₿ 加密货币
            </button>
            <div class="mode-toggle">
                <button class="mode-btn active" id="btn-sim" onclick="setMode('sim')">🟡 模拟</button>
                <button class="mode-btn live" id="btn-live" onclick="setMode('live')">🟢 实盘</button>
            </div>
        </div>
        
        <!-- 指数行情卡片 -->
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;">
                <h2 style="margin:0;">📊 市场指数</h2>
                <span id="market-time" style="color:#666;font-size:12px;">--</span>
            </div>
            <div id="index-cards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;">
                <!-- 动态加载 -->
            </div>
        </div>
        
        <!-- 系统状态 -->
        <div class="card">
            <h2>🏛 系统状态</h2>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:15px;">
                <div class="sys-stat-card" id="stat-monitor">
                    <div class="sys-stat-label">📡 行情监控</div>
                    <div class="sys-stat-value"><span id="monitor-status" class="status-badge status-stopped">检测中...</span></div>
                </div>
                <div class="sys-stat-card" id="stat-mode">
                    <div class="sys-stat-label">🎯 交易模式</div>
                    <div class="sys-stat-value"><span id="live-status" class="status-badge" style="background:#555;color:#fff;">模拟模式</span></div>
                </div>
                <div class="sys-stat-card" id="stat-menxia">
                    <div class="sys-stat-label">📋 门下省</div>
                    <div class="sys-stat-value"><span id="menxia-status" class="status-badge" style="background:#555;color:#fff;">离线</span></div>
                </div>
                <div class="sys-stat-card" id="stat-shangshu">
                    <div class="sys-stat-label">⚙️ 尚书省</div>
                    <div class="sys-stat-value"><span id="shangshu-status" class="status-badge" style="background:#555;color:#fff;">离线</span></div>
                </div>
            </div>
            <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
                <div style="font-size:12px;color:#888;">
                    <span id="uptime-display">⏱ 运行时间: --</span>
                    <span style="margin-left:20px;">📊 今日交易: <span id="daily-trades-count" style="color:#00d4ff;">0</span> 笔</span>
                    <span style="margin-left:20px;">💰 持仓: <span id="position-count" style="color:#00d4ff;">0</span> 个</span>
                </div>
                <div class="actions" style="margin:0;">
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
            <button class="tab" onclick="switchTab('replay')">🔍 交易复盘</button>
        </div>
        
        <!-- 实时行情 -->
        <div id="tab-market" class="tab-content active">
            <div class="card">
                <h2 id="market-title">📈 A股实时行情</h2>
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
                        <select id="trade-market" onchange="syncMarketFromTrade()">
                            <option value="CN" ${currentMarket==='CN'?'selected':''}>🇨🇳 A股 (¥)</option>
                            <option value="HK" ${currentMarket==='HK'?'selected':''}>🇭🇰 港股 (HK$)</option>
                            <option value="US" ${currentMarket==='US'?'selected':''}>🇺🇸 美股 ($)</option>
                            <option value="CRYPTO" ${currentMarket==='CRYPTO'?'selected':''}>₿ 加密货币 (₿)</option>
                        </select>
                    </div>
                    <div>
                        <label>代码</label>
                        <input type="text" id="trade-symbol" placeholder="输入代码，如: 600000, 00700, AAPL, BTC">
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

        <!-- 交易复盘 P3 -->
        <div id="tab-replay" class="tab-content">
            <!-- KPI 绩效卡片 -->
            <div class="card">
                <h2>📊 综合绩效统计</h2>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:15px;" id="replay-kpi"></div>
            </div>

            <!-- 策略×市场热力图 -->
            <div class="card">
                <h2>🔥 策略 × 市场状态 热力图</h2>
                <div id="replay-heatmap" style="overflow-x:auto;"></div>
            </div>

            <!-- 出场原因分析 -->
            <div class="card">
                <h2>📋 出场原因分析</h2>
                <div id="replay-exits" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;"></div>
            </div>

            <!-- 交易历史 -->
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;">
                    <h2 style="margin:0;">📜 交易历史</h2>
                    <div style="display:flex;gap:8px;">
                        <select id="replay-trade-limit" onchange="loadReplayTrades()" style="width:auto;margin:0;">
                            <option value="20">最近20笔</option>
                            <option value="50" selected>最近50笔</option>
                            <option value="100">最近100笔</option>
                        </select>
                        <button class="btn btn-primary" onclick="loadReplayTrades()" style="margin:0;">🔄 刷新</button>
                    </div>
                </div>
                <div id="replay-trades" style="overflow-x:auto;"></div>
            </div>

            <!-- 审计洞察 -->
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;">
                    <h2 style="margin:0;">🧠 Reflection Agent 审计报告</h2>
                    <button class="btn btn-primary" onclick="runReplayAudit()" style="margin:0;">▶ 重新审计</button>
                </div>
                <div id="replay-audit"></div>
            </div>
        </div>
    </div>
    
    <script>
        // ========== 全局状态 ==========
        let currentMarket = 'CN';  // CN, HK, US, CRYPTO
        let currentMode = 'sim';   // sim, live
        
        // 市场配置
        const MARKET_CONFIG = {
            CN: {
                name: 'A股',
                currency: '¥',
                currencySymbol: '¥',
                icon: '🇨🇳',
                indices: [
                    {symbol: 'sh000001', name: '上证指数', suffix: ''},
                    {symbol: 'sz399001', name: '深证成指', suffix: ''},
                    {symbol: 'sz399006', name: '创业板指', suffix: ''},
                ],
                symbols: ['600000', '000001', '000002', '600519']
            },
            HK: {
                name: '港股',
                currency: 'HK$',
                currencySymbol: 'HK$',
                icon: '🇭🇰',
                indices: [
                    {symbol: 'HSI', name: '恒生指数', suffix: ''},
                    {symbol: '00700', name: '腾讯控股', suffix: ''},
                    {symbol: '09988', name: '阿里巴巴', suffix: ''},
                ],
                symbols: ['00700', '09988', '03690']
            },
            US: {
                name: '美股',
                currency: '$',
                currencySymbol: '$',
                icon: '🇺🇸',
                indices: [
                    {symbol: 'ixic', name: '纳斯达克', suffix: ''},
                    {symbol: 'dji', name: '道琼斯', suffix: ''},
                    {symbol: 'AAPL', name: '苹果', suffix: ''},
                ],
                symbols: ['AAPL', 'TSLA', 'NVDA', 'MSFT']
            },
            CRYPTO: {
                name: '加密货币',
                currency: '₿',
                currencySymbol: '₿',
                icon: '₿',
                indices: [
                    {symbol: 'BTC', name: '比特币', suffix: ''},
                    {symbol: 'ETH', name: '以太坊', suffix: ''},
                    {symbol: 'BNB', name: '币安币', suffix: ''},
                ],
                symbols: ['BTC', 'ETH', 'BNB', 'SOL']
            }
        };
        
        // ========== 市场切换 ==========
        async function switchMarket(market) {
            currentMarket = market;
            
            // 更新Tab样式
            document.querySelectorAll('.market-tab').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + market.toLowerCase()).classList.add('active');
            
            // 更新标题
            const config = MARKET_CONFIG[market];
            document.getElementById('market-title').textContent = `📈 ${config.name}实时行情`;
            
            // 并行加载指数和行情（不再串行等待）
            Promise.all([loadIndexCards(market), loadMarketPrices()]);
        }
        
        // ========== 模式切换 ==========
        function setMode(mode) {
            currentMode = mode;
            document.getElementById('btn-sim').classList.toggle('active', mode === 'sim');
            document.getElementById('btn-live').classList.toggle('active', mode === 'live');
            
            const statusEl = document.getElementById('live-status');
            if (mode === 'live') {
                statusEl.textContent = '实盘';
                statusEl.style.background = '#00c853';
                statusEl.style.color = '#000';
            } else {
                statusEl.textContent = '模拟模式';
                statusEl.style.background = '#555';
                statusEl.style.color = '#fff';
            }
            
            // 调用API切换实盘/模拟模式
            fetch('/api/trading/mode', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mode: mode})
            }).then(res => res.json()).then(data => {
                console.log('模式切换:', data.message || ('已切换到' + (mode === 'live' ? '实盘' : '模拟') + '模式'));
            }).catch(err => {
                console.error('模式切换失败:', err);
            });
        }
        
        // ========== 同步交易表单市场 ==========
        function syncMarketFromTrade() {
            const market = document.getElementById('trade-market').value;
            switchMarket(market);
        }
        
        // 更新交易表单的市场选择
        function updateTradeMarketSelect() {
            const select = document.getElementById('trade-market');
            if (select) {
                select.value = currentMarket;
            }
        }
        
        // ========== 加载指数卡片 ==========
        async function loadIndexCards(market) {
            const config = MARKET_CONFIG[market];
            const container = document.getElementById('index-cards');
            const marketKey = market.toLowerCase() === 'crypto' ? 'CRYPTO' : market;
            
            // 并行获取所有指数数据（替代串行fetch）
            const indexPromises = config.indices.map(idx => {
                const symbol = idx.symbol + idx.suffix;
                return fetch(`/api/price/${marketKey}/${symbol}`)
                    .then(res => res.ok ? res.json() : null)
                    .catch(() => null);
            });
            
            const results = await Promise.all(indexPromises);
            
            let html = '';
            for (let i = 0; i < config.indices.length; i++) {
                const idx = config.indices[i];
                const data = results[i];
                const price = data ? (data.price || '--') : '--';
                const changePct = data ? (data.change_pct || '--') : '--';
                const change = data ? (data.change || '--') : '--';
                const isUp = parseFloat(changePct) >= 0;
                const cls = isUp ? 'price-up' : 'price-down';
                const sign = isUp ? '+' : '';
                
                html += `<div class="index-card">
                    <span class="index-icon">${config.icon}</span>
                    <div class="index-info">
                        <div class="index-name">${idx.name}</div>
                        <div class="index-value">${config.currencySymbol}${price}</div>
                        <div class="index-change ${cls}">${sign}${changePct}%</div>
                    </div>
                </div>`;
            }
            
            container.innerHTML = html || '<div style="color:#666;">暂无数据</div>';
            document.getElementById('market-time').textContent = new Date().toLocaleTimeString();
        }
        
        // 加载所有数据
        async function loadAll() {
            await loadIndexCards(currentMarket);
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
                const mxCard = document.getElementById('stat-menxia');
                if (data.menxia_available) {
                    const mx = data.menxia || {};
                    const levelColors = {'normal': '#00c853', 'caution': '#ff9800', 'warning': '#ff5722', 'locked': '#ff1744'};
                    const levelCls = {'normal': '', 'caution': 'warning', 'warning': 'danger', 'locked': 'danger'};
                    mxEl.textContent = mx.level ? `${mx.level}` : '正常';
                    mxEl.style.background = levelColors[mx.level] || '#00c853';
                    mxEl.style.color = '#000';
                    mxCard.className = 'sys-stat-card ' + (levelCls[mx.level] || 'running');
                } else {
                    mxEl.textContent = '未启用';
                    mxEl.style.background = '#555';
                    mxCard.className = 'sys-stat-card';
                }

                // 尚书省状态
                const ssEl = document.getElementById('shangshu-status');
                const ssCard = document.getElementById('stat-shangshu');
                if (data.shangshu_available && data.live_trading) {
                    ssEl.textContent = data.exchange || '已连接';
                    ssEl.style.background = '#00c853';
                    ssEl.style.color = '#000';
                    ssCard.className = 'sys-stat-card running';
                } else {
                    ssEl.textContent = '离线';
                    ssEl.style.background = '#555';
                    ssCard.className = 'sys-stat-card';
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
                const monCard = document.getElementById('stat-monitor');
                monCard.className = 'sys-stat-card ' + (status === 'running' ? 'running' : 'danger');
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
                const config = MARKET_CONFIG[currentMarket];
                
                // 过滤当前市场的数据
                const marketPrices = prices.filter(p => {
                    if (currentMarket === 'CRYPTO') return p.market === 'CRYPTO';
                    if (currentMarket === 'CN') return p.market === 'CN';
                    if (currentMarket === 'HK') return p.market === 'HK';
                    if (currentMarket === 'US') return p.market === 'US';
                    return true;
                });
                
                if (marketPrices && marketPrices.length) {
                    const currencySymbol = config.currencySymbol;
                    document.getElementById('market-prices').innerHTML = `
                        <table>
                            <tr><th>代码</th><th>名称</th><th>最新价</th><th>涨跌</th><th>24h高</th><th>24h低</th><th>成交量</th></tr>
                            ${marketPrices.map(p => {
                                const chg = p.change_pct || 0;
                                const cls = chg >= 0 ? 'price-up' : 'price-down';
                                const sign = chg >= 0 ? '+' : '';
                                const vol = p.volume_24h || p.volume || 0;
                                const volNum = parseFloat(vol) || 0;
                                const volStr = volNum > 1e8 ? (volNum/1e8).toFixed(2) + '亿' : volNum > 1e4 ? (volNum/1e4).toFixed(2) + '万' : volNum.toFixed(2);
                                return `<tr>
                                    <td><b>${p.symbol}</b></td>
                                    <td>${p.name || '-'}</td>
                                    <td class="${cls}">${currencySymbol}${(p.price||0).toLocaleString()}</td>
                                    <td class="${cls}">${sign}${chg.toFixed(2)}%</td>
                                    <td>${currencySymbol}${(p.high_24h||p.high||0).toLocaleString()}</td>
                                    <td>${currencySymbol}${(p.low_24h||p.low||0).toLocaleString()}</td>
                                    <td>${volStr}</td>
                                </tr>`;
                            }).join('')}
                        </table>`;
                } else {
                    document.getElementById('market-prices').innerHTML = `<p>暂无${config.name}行情数据</p>`;
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
            if (tab === 'replay') loadReplayAll();
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
                // 显示投票权重
                if (data.weights && Object.keys(data.weights).length > 0) {
                    const wParts = Object.entries(data.weights).map(([k, v]) => `${k} ${(v*100).toFixed(0)}%`).join(' + ');
                    const confInfo = data.confidence_enabled
                        ? ` | 信号数：${data.signal_stats.total_signals} | 高置信度🟢${data.signal_stats.high_conf} | 低置信度🔴${data.signal_stats.low_conf}`
                        : '';
                    weightsDiv.style.display = 'block';
                    weightsDiv.innerHTML = `投票权重：${wParts}${confInfo}`;
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

                // 买标记（显示置信度）
                if (data.buy_markers && data.buy_markers.length > 0) {
                    data.buy_markers.forEach(m => {
                        const conf = m.confidence || 0;
                        const color = conf >= 0.7 ? '#00c853' : (conf >= 0.4 ? '#ff9800' : '#ff1744');
                        candleSeries.createPriceLine({
                            price: m.price, color: color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true,
                            title: m.label || '买入'
                        });
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

        // ================================================================
        // P3 交易复盘
        // ================================================================

        async function loadReplayKPI() {
            try {
                const res = await fetch('/api/replay/stats');
                const data = await res.json();
                if (data.error) { document.getElementById('replay-kpi').innerHTML = '<p style="color:#888;">暂无交易数据</p>'; return; }
                const winRate = ((data.win_rate || 0) * 100).toFixed(1);
                const profitFactor = (data.profit_factor || 0).toFixed(2);
                const expectancy = (data.expectancy || 0).toFixed(4);
                const maxDD = ((data.max_drawdown_pct || 0) * 100).toFixed(2);
                const totalTrades = data.total_trades || 0;
                const kpi = [
                    { label: '总交易次数', value: totalTrades, color: '#00d4ff' },
                    { label: '胜率', value: winRate + '%', color: winRate >= 50 ? '#00c853' : '#ff1744' },
                    { label: '盈亏比', value: profitFactor, color: '#ff9800' },
                    { label: '期望值/笔', value: expectancy, color: '#00d4ff' },
                    { label: '最大回撤', value: maxDD + '%', color: maxDD > 10 ? '#ff1744' : '#ff9800' },
                    { label: '总盈利', value: (data.total_pnl || 0).toFixed(2), color: '#00c853' },
                ];
                document.getElementById('replay-kpi').innerHTML = kpi.map(k =>
                    `<div class="sys-stat-card" style="border-left-color:${k.color};">
                        <div class="sys-stat-label">${k.label}</div>
                        <div class="sys-stat-value" style="font-size:22px;color:${k.color};">${k.value}</div>
                    </div>`
                ).join('');
            } catch(e) {
                document.getElementById('replay-kpi').innerHTML = `<p style="color:#ff1744;">加载失败: ${e.message}</p>`;
            }
        }

        async function loadReplayHeatmap() {
            try {
                const res = await fetch('/api/replay/heatmap');
                const data = await res.json();
                if (!data || Object.keys(data).length === 0) {
                    document.getElementById('replay-heatmap').innerHTML = '<p style="color:#888;">暂无热力图数据</p>';
                    return;
                }
                const strategies = [...new Set(Object.values(data).flatMap(v => Object.keys(v)))];
                let html = '<table style="min-width:400px;"><thead><tr><th>策略 \\ 市场</th>';
                for (const s of strategies) html += `<th>${s}</th>`;
                html += '</tr></thead><tbody>';
                for (const [regime, stratMap] of Object.entries(data)) {
                    html += `<tr><td style="font-weight:bold;color:#00d4ff;">${regime}</td>`;
                    for (const s of strategies) {
                        const cell = stratMap[s];
                        if (cell) {
                            const wr = (cell.win_rate * 100).toFixed(0) + '%';
                            const clr = cell.win_rate >= 0.5 ? '#00c853' : (cell.win_rate >= 0.3 ? '#ff9800' : '#ff1744');
                            html += `<td style="text-align:center;">
                                <div style="background:${clr}22;border:1px solid ${clr};border-radius:6px;padding:6px 8px;">
                                    <div style="font-size:16px;font-weight:bold;color:${clr};">${wr}</div>
                                    <div style="font-size:11px;color:#888;">${(cell.total_pnl || 0).toFixed(2)}</div>
                                </div>
                            </td>`;
                        } else {
                            html += `<td style="text-align:center;color:#444;">—</td>`;
                        }
                    }
                    html += '</tr>';
                }
                html += '</tbody></table>';
                html += '<p style="font-size:11px;color:#666;margin-top:8px;">格内数字：胜率 / 总盈亏 | 绿色=胜率≥50% 橙色=30-50% 红色=<30%</p>';
                document.getElementById('replay-heatmap').innerHTML = html;
            } catch(e) {
                document.getElementById('replay-heatmap').innerHTML = `<p style="color:#ff1744;">加载失败: ${e.message}</p>`;
            }
        }

        async function loadReplayExits() {
            try {
                const res = await fetch('/api/replay/exit_analysis');
                const data = await res.json();
                if (!data || Object.keys(data).length === 0) {
                    document.getElementById('replay-exits').innerHTML = '<p style="color:#888;">暂无出场数据</p>';
                    return;
                }
                const labels = { stop_loss: '🔴 止损', take_profit: '🟢 止盈', exit: '⚪ 其他' };
                const colors = { stop_loss: '#ff1744', take_profit: '#00c853', exit: '#888' };
                document.getElementById('replay-exits').innerHTML = Object.entries(data).map(([k, v]) => {
                    const label = labels[k] || k;
                    const color = colors[k] || '#00d4ff';
                    const pnl = (v.total_pnl || 0).toFixed(2);
                    const wr = ((v.win_rate || 0) * 100).toFixed(1);
                    const cls = parseFloat(pnl) >= 0 ? 'profit' : 'loss';
                    return `<div class="sys-stat-card" style="border-left-color:${color};">
                        <div class="sys-stat-label">${label}</div>
                        <div style="font-size:18px;font-weight:bold;color:${color};">${v.count || 0} 笔</div>
                        <div style="font-size:13px;color:#aaa;margin-top:4px;">
                            胜率: ${wr}% | 盈亏: <span class="${cls}">¥${pnl}</span>
                        </div>
                    </div>`;
                }).join('');
            } catch(e) {
                document.getElementById('replay-exits').innerHTML = `<p style="color:#ff1744;">加载失败: ${e.message}</p>`;
            }
        }

        async function loadReplayTrades() {
            const limit = parseInt(document.getElementById('replay-trade-limit').value) || 50;
            try {
                const res = await fetch(`/api/replay/trades?limit=${limit}`);
                const trades = await res.json();
                if (!trades || trades.length === 0) {
                    document.getElementById('replay-trades').innerHTML = '<p style="color:#888;">暂无交易记录</p>';
                    return;
                }
                const trendBadge = { uptrend: '🟢', downtrend: '🔴', ranging: '🟡', unknown: '⚪' };
                const volColor = { high: '#ff9800', medium: '#00d4ff', low: '#888', unknown: '#444' };
                let html = `<table style="min-width:900px;">
                    <thead><tr>
                        <th>时间</th><th>策略</th><th>标的</th><th>方向</th>
                        <th>入场价</th><th>出场价</th><th>持仓时长</th>
                        <th>市场趋势</th><th>波动率</th><th>盈亏</th><th>出场原因</th>
                    </tr></thead><tbody>`;
                for (const t of trades) {
                    const side = t.get('side') || t.get('action') || 'buy';
                    const pnl = t.get('pnl') || 0;
                    const exit = t.get('exit_reason') || 'unknown';
                    const trend = t.get('market_trend') || 'unknown';
                    const vol = t.get('market_volatility') || 'unknown';
                    const duration = t.get('holding_hours');
                    const durationStr = duration != null ? duration.toFixed(1) + 'h' : '—';
                    const cls = pnl >= 0 ? 'profit' : 'loss';
                    const sideLabel = side === 'sell' ? '🔴 做空' : '🟢 做多';
                    const exitLabels = { stop_loss: '🔴止损', take_profit: '🟢止盈', signal_end: '📊信号结束', manual_close: '🔧手动', unknown: '—' };
                    const badge = trendBadge[trend] || '⚪';
                    html += `<tr>
                        <td>${(t.get('opened_at') || t.get('open_time') || '—').toString().slice(0, 19)}</td>
                        <td><b>${t.get('strategy') || '—'}</b></td>
                        <td>${t.get('symbol') || '—'}</td>
                        <td>${sideLabel}</td>
                        <td>¥${(t.get('entry_price') || 0).toFixed(4)}</td>
                        <td>¥${(t.get('exit_price') || 0).toFixed(4)}</td>
                        <td>${durationStr}</td>
                        <td>${badge} ${trend}</td>
                        <td style="color:${volColor[vol] || '#888'};">${vol}</td>
                        <td class="${cls}">¥${pnl.toFixed(2)}</td>
                        <td>${exitLabels[exit] || exit}</td>
                    </tr>`;
                }
                html += '</tbody></table>';
                document.getElementById('replay-trades').innerHTML = html;
            } catch(e) {
                document.getElementById('replay-trades').innerHTML = `<p style="color:#ff1744;">加载失败: ${e.message}</p>`;
            }
        }

        async function loadReplayAudit() {
            try {
                const res = await fetch('/api/replay/audit');
                const data = await res.json();
                if (data.error) {
                    document.getElementById('replay-audit').innerHTML = `<p style="color:#888;">暂无审计数据: ${data.error}</p>`;
                    return;
                }
                let html = `<div style="margin-bottom:15px;font-size:12px;color:#888;">`;
                if (data.period) html += `审计周期: ${data.period.start} ~ ${data.period.end} | `;
                if (data.total_trades != null) html += `总交易: ${data.total_trades} 笔 | `;
                if (data.overall_win_rate != null) html += `整体胜率: ${(data.overall_win_rate * 100).toFixed(1)}%`;
                html += `</div>`;

                // 策略排名
                if (data.strategy_rankings && data.strategy_rankings.length > 0) {
                    html += `<h3 style="color:#00d4ff;margin:10px 0 8px 0;">🏆 策略表现排名</h3>`;
                    html += `<table style="margin-bottom:15px;">
                        <thead><tr><th>#</th><th>策略</th><th>次数</th><th>胜率</th><th>总盈亏</th><th>期望值</th></tr></thead><tbody>`;
                    data.strategy_rankings.forEach((s, i) => {
                        const wr = ((s.win_rate || 0) * 100).toFixed(1) + '%';
                        const pnl = (s.total_pnl || 0).toFixed(2);
                        const cls = parseFloat(pnl) >= 0 ? 'profit' : 'loss';
                        html += `<tr>
                            <td>${i + 1}</td><td><b>${s.strategy}</b></td>
                            <td>${s.count}</td>
                            <td style="color:${s.win_rate >= 0.5 ? '#00c853' : '#ff1744'};">${wr}</td>
                            <td class="${cls}">¥${pnl}</td>
                            <td>${(s.expectancy || 0).toFixed(4)}</td>
                        </tr>`;
                    });
                    html += '</tbody></table>';
                }

                // 洞察
                if (data.insights && data.insights.length > 0) {
                    html += `<h3 style="color:#00d4ff;margin:10px 0 8px 0;">💡 审计洞察</h3>`;
                    const severityColor = { high: '#ff1744', medium: '#ff9800', low: '#00d4ff' };
                    data.insights.forEach(ins => {
                        const color = severityColor[ins.severity] || '#888';
                        html += `<div style="background:#0f3460;border-left:3px solid ${color};border-radius:6px;padding:10px 14px;margin-bottom:8px;">
                            <div style="font-weight:bold;color:${color};margin-bottom:4px;">[${(ins.severity || 'info').toUpperCase()}] ${ins.description || ins}</div>
                            <div style="color:#aaa;font-size:13px;">📌 建议: ${ins.recommendation || '—'}</div>
                        </div>`;
                    });
                } else {
                    html += `<p style="color:#888;">暂无洞察数据</p>`;
                }
                document.getElementById('replay-audit').innerHTML = html;
            } catch(e) {
                document.getElementById('replay-audit').innerHTML = `<p style="color:#ff1744;">加载失败: ${e.message}</p>`;
            }
        }

        async function runReplayAudit() {
            const btn = document.querySelector('[onclick="runReplayAudit()"]');
            if (btn) { btn.disabled = true; btn.textContent = '⏳ 审计中...'; }
            try {
                const res = await fetch('/api/replay/run-audit', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    await loadReplayAudit();
                    await loadReplayKPI();
                } else {
                    alert('审计失败: ' + (data.error || '未知错误'));
                }
            } finally {
                if (btn) { btn.disabled = false; btn.textContent = '▶ 重新审计'; }
            }
        }

        async function loadReplayAll() {
            await Promise.all([loadReplayKPI(), loadReplayHeatmap(), loadReplayExits(), loadReplayTrades(), loadReplayAudit()]);
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

# ============================================================
# 股票交易接口 (A股/港股/美股)
# ============================================================

from stock_trading import StockTrader, SimulatedStockTrader

# 全局模拟交易器
_sim_traders = {}

@app.get("/api/stock/connect")
async def stock_connect(market: str = "us", broker: str = "auto", paper: bool = True):
    """连接股票券商"""
    try:
        trader = StockTrader(market=market, broker=broker, paper=paper)
        return {
            "success": True,
            "market": market,
            "broker": broker,
            "connected": trader.is_connected(),
            "mode": "paper" if paper else "live"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/stock/account/{market}")
async def get_stock_account(market: str):
    """获取股票账户信息"""
    trader = StockTrader.get_trader(market)
    if trader:
        return trader.get_account()
    return {"status": "not_initialized", "cash": 0, "portfolio_value": 0}

@app.get("/api/stock/positions/{market}")
async def get_stock_positions(market: str):
    """获取股票持仓"""
    trader = StockTrader.get_trader(market)
    if trader:
        return trader.get_positions()
    return []

@app.post("/api/stock/order")
async def place_stock_order(
    market: str,
    symbol: str,
    action: str,  # "buy" or "sell"
    quantity: int,
    order_type: str = "market",
    limit_price: float = None
):
    """下单接口"""
    trader = StockTrader.get_trader(market)
    if not trader:
        # 使用模拟交易
        if market not in _sim_traders:
            _sim_traders[market] = SimulatedStockTrader()
        sim = _sim_traders[market]
        if action == "buy":
            result = sim.buy(symbol, quantity, limit_price)
        else:
            result = sim.sell(symbol, quantity, limit_price)
        return {"mode": "simulated", **result}
    
    if action == "buy":
        result = trader.buy(symbol, quantity, order_type, limit_price)
    else:
        result = trader.sell(symbol, quantity, order_type, limit_price)
    return {"mode": "live", **result}

@app.get("/api/stock/order/{market}")
async def get_stock_order_status(market: str, order_id: str):
    """查询订单状态"""
    trader = StockTrader.get_trader(market)
    if trader:
        # 实际实现需要查询券商API
        return {"order_id": order_id, "status": "filled"}
    return {"order_id": order_id, "status": "unknown"}



def run_server(host: str = "0.0.0.0", port: int = 8081):
    """启动 Web 服务"""
    init_db()
    uvicorn.run(app, host=host, port=port, log_level="warning")

if __name__ == "__main__":
    print("启动交易监控系统 Dashboard...")
    run_server()
