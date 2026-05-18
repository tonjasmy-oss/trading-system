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
_start_time = time.time()  # 模块导入时初始化，FastAPI startup 事件中覆盖


@app.on_event("startup")
async def _on_startup():
    """FastAPI 启动事件：记录启动时间 + 启动后台行情监控"""
    global _start_time
    _start_time = time.time()
    print(f"[Dashboard] 启动完成 @ {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_start_time))}")

    # 启动后台行情监控（独立线程）
    import threading
    from database import init_db
    from monitor import PriceMonitor
    from feishu_alert import feishu_alert as _fa

    def _monitor_worker():
        init_db()
        mon = PriceMonitor(check_interval=30)
        symbols = [
            {"symbol": "BTC", "market": "CRYPTO"},
            {"symbol": "ETH", "market": "CRYPTO"},
        ]
        update_monitor_status("running", f"监控 {len(symbols)} 个品种")

        def _on_alert(data):
            try:
                _fa.send_alert(
                    symbol=data.get("symbol"),
                    market=data.get("market"),
                    alert_type=data.get("alert_type", "价格波动"),
                    price=data.get("price", 0),
                    threshold=data.get("threshold", 0),
                    message=data.get("message", ""),
                )
            except Exception:
                pass

        mon.add_alert_callback(_on_alert)
        try:
            import asyncio as _asyncio
            _asyncio.run(mon.monitor_loop(symbols, threshold=0.03))
        except Exception:
            pass
        finally:
            update_monitor_status("stopped", "监控已停止")

    t = threading.Thread(target=_monitor_worker, daemon=True)
    t.start()
    print("[Dashboard] 后台行情监控线程已启动")

    # 交易所连通性自检
    try:
        from config import CRYPTO_EXCHANGE
        from crypto_api import get_crypto_price
        result = get_crypto_price("BTC")
        if result and result.get("price"):
            print(f"[Dashboard] 连通性检查通过: {CRYPTO_EXCHANGE} BTC=${result['price']:,.2f}")
        else:
            print(f"[Dashboard] ⚠️ 连通性检查: {CRYPTO_EXCHANGE} 返回异常")
    except Exception as e:
        print(f"[Dashboard] ⚠️ 连通性检查失败: {e}")

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
        "uptime": int(time.time() - _start_time) if _start_time else 0,
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_start_time)) if _start_time else None
    }

@app.get("/api/sansheng/status")
async def get_sansheng_status():
    """获取三省六部架构状态"""
    from config import LIVE_TRADING_ENABLED, LIVE_EXCHANGE, LIVE_TESTNET
    from menxia_sheng import MenxiaSheng, RiskLevel
    from shangshu_sheng import ShangshuSheng

    menxia_ok = MenxiaSheng is not None
    shangshu_ok = ShangshuSheng is not None

    # 尝试获取门下省状态（跨进程：先从 orchestrator 读，回退到 DB）
    menxia_info = {}
    if menxia_ok:
        try:
            from live_trading import orchestrator as _orch_ss
            if _orch_ss and _orch_ss.menxia:
                ms = _orch_ss.menxia.get_status()
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
    # 如果 orchestrator 不可用（独立进程），从数据库构造基本状态
    if not menxia_info:
        try:
            import sqlite3
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_trading.db")
            conn = sqlite3.connect(db_path)
            open_count = conn.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
            today_count = conn.execute("SELECT COUNT(*) FROM trades WHERE created_at > date('now')").fetchone()[0]
            conn.close()
            menxia_info = {
                "level": "normal",
                "daily_loss_pct": 0.0,
                "exposure_pct": 0.0,
                "open_positions": open_count,
                "daily_trades": today_count,
                "can_open": True,
            }
        except Exception:
            menxia_info = {"level": "normal"}

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
    """获取持仓 — 从 live_trading.db 读取实盘持仓"""
    import sqlite3, os
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_trading.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT symbol, entry_price, quantity, side, exchange, created_at
            FROM positions WHERE status='open'
            ORDER BY created_at DESC
        """).fetchall()
        conn.close()
        # 构建 symbol → agent_id 映射
        from config import AGENT_SYMBOLS
        sym_to_agent = {}
        for i, cfg in enumerate(AGENT_SYMBOLS.split(",")):
            s = cfg.strip().split(":")[0].strip()
            sym_to_agent[s] = f"agent_{i+1}"
        positions = []
        for r in rows:
            sym = r["symbol"]
            current_price = r["entry_price"]  # fallback
            aid = sym_to_agent.get(sym)
            if aid:
                try:
                    conn2 = sqlite3.connect(db_path)
                    conn2.row_factory = sqlite3.Row
                    eq = conn2.execute(
                        "SELECT price FROM equity_log WHERE agent_id=? ORDER BY id DESC LIMIT 1",
                        (aid,)
                    ).fetchone()
                    conn2.close()
                    if eq and eq["price"] and eq["price"] > 0:
                        current_price = eq["price"]
                except Exception:
                    pass
            cp = current_price
            qty = r["quantity"]
            entry = r["entry_price"]
            side_label = (r["side"] or "long").upper()
            if side_label == "SHORT":
                pnl = qty * (entry - cp)
                pnl_pct = (entry - cp) / entry * 100 if entry else 0
            else:
                pnl = qty * (cp - entry)
                pnl_pct = (cp - entry) / entry * 100 if entry else 0
            positions.append({
                "symbol": sym,
                "market": "CRYPTO",
                "quantity": round(qty, 6),
                "avg_price": round(entry, 4),
                "current_price": round(cp, 4),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "side": side_label,
                "exchange": r["exchange"] or "",
            })
        return positions
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/portfolio/value")
async def get_portfolio_value():
    """获取持仓市值和盈亏 — 从 live_trading.db 读取"""
    import sqlite3, os
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_trading.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT symbol, entry_price, quantity, side
            FROM positions WHERE status='open'
        """).fetchall()
        conn.close()
        from config import AGENT_SYMBOLS
        sym_to_agent = {}
        for i, cfg in enumerate(AGENT_SYMBOLS.split(",")):
            s = cfg.strip().split(":")[0].strip()
            sym_to_agent[s] = f"agent_{i+1}"
        total_cost = 0.0
        total_value = 0.0
        positions_list = []
        for r in rows:
            sym = r["symbol"]
            entry = r["entry_price"] or 0
            qty = r["quantity"] or 0
            cp = entry
            aid = sym_to_agent.get(sym)
            if aid:
                try:
                    conn2 = sqlite3.connect(db_path)
                    conn2.row_factory = sqlite3.Row
                    eq = conn2.execute(
                        "SELECT price FROM equity_log WHERE agent_id=? ORDER BY id DESC LIMIT 1",
                        (aid,)
                    ).fetchone()
                    conn2.close()
                    if eq and eq["price"] and eq["price"] > 0:
                        cp = eq["price"]
                except Exception:
                    pass
            cost = qty * entry
            value_now = qty * cp
            total_cost += cost
            total_value += value_now
            side_val = (r["side"] or "long").upper()
            pnl = qty * (entry - cp) if side_val == "SHORT" else qty * (cp - entry)
            positions_list.append({
                "symbol": sym, "quantity": qty, "entry_price": entry,
                "current_price": cp, "cost": cost, "value": value_now, "pnl": pnl,
            })
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_value - total_cost) / total_cost * 100 if total_cost > 0 else 0.0
        return {
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "positions": positions_list,
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/trades")
async def get_trades_api(limit: int = 50):
    """获取交易历史 — 从 live_trading.db 读取"""
    import sqlite3, os
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_trading.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, symbol, entry_price, exit_price, quantity, pnl_pct, pnl_abs, exit_reason, created_at
            FROM trades ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [{
            "id": r["id"],
            "symbol": r["symbol"],
            "entry_price": round(r["entry_price"], 4) if r["entry_price"] else 0,
            "exit_price": round(r["exit_price"], 4) if r["exit_price"] else 0,
            "quantity": round(r["quantity"], 6) if r["quantity"] else 0,
            "pnl_pct": round(r["pnl_pct"], 2) if r["pnl_pct"] else 0,
            "pnl_abs": round(r["pnl_abs"], 2) if r["pnl_abs"] else 0,
            "exit_reason": r["exit_reason"] or "",
            "created_at": r["created_at"] or "",
        } for r in rows]
    except Exception as e:
        return []

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

@app.get("/api/backtest/chart/{strategy_name}")
async def get_backtest_chart_data(
    strategy_name: str,
    symbol: str = "SUI/USDT",
    timeframe: str = "4h",
    ema_period: int = 20,
    atr_period: int = 14,
    atr_multiplier: float = 2.0,
    rsi_period: int = 14,
    oversold: float = 30.0,
    overbought: float = 65.0,
    stop_loss: float = 0.012,
    take_profit: float = 0.025,
):
    """
    获取回测图表数据：Equity Curve + 买卖点标注（真实回测引擎）
    strategy_name: "KDJ" | "MACD" | "MA_CROSS" | "CCI" | "RSI" | "BOLL" | "WR" | "MultiVote"
                 | "ATRStopStrategy" | "RSIStrategy" | "BollingerBandsStrategy" | "SMAcrossStrategy"
    """
    import random, math
    from backtest import BacktestEngine
    from multi_strategy_vote import MultiStrategyVote
    from strategies import (RSIStrategy, SMAcrossStrategy, BollingerBandsStrategy,
                            KDJStrategy, MACDStrategy, ATRStopStrategy, StrategyConfig)
    from tdx_compiler import FormulaStrategy, BUILTIN_FORMULAS, TdxCompiler

    # 构建策略
    strategy_map = {
        "RSIStrategy":            RSIStrategy,
        "BollingerBandsStrategy": BollingerBandsStrategy,
        "SMAcrossStrategy":       SMAcrossStrategy,
        "KDJStrategy":            KDJStrategy,
        "MACDStrategy":           MACDStrategy,
        "ATRStopStrategy":       ATRStopStrategy,
        # 别名
        "RSI":                   RSIStrategy,
        "BOLL":                  BollingerBandsStrategy,
        "MA_CROSS":              SMAcrossStrategy,
    }
    # 多策略投票阈值（0.0 表示过半即触发，适合分散信号的多策略组合）
    VOTE_THRESHOLD = float(os.getenv("LIVE_VOTE_THRESHOLD", "0.0"))
    if strategy_name == "MultiVote":
        rsi_s  = RSIStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit), rsi_period=rsi_period, oversold=oversold, overbought=overbought)
        sma_s  = SMAcrossStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit))
        macd_s = FormulaStrategy(formula=BUILTIN_FORMULAS["MACD"], symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit)
        vote   = MultiStrategyVote([(rsi_s, 0.4), (sma_s, 0.3), (macd_s, 0.3)], threshold=VOTE_THRESHOLD, name="RSI40%+SMA30%+MACD30%")
        strategy = vote
        use_confidence = True
    elif strategy_name in BUILTIN_FORMULAS:
        strategy = FormulaStrategy(formula=BUILTIN_FORMULAS[strategy_name], symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit)
        use_confidence = False
    elif strategy_name in strategy_map:
        cls = strategy_map[strategy_name]
        if cls == ATRStopStrategy:
            strategy = ATRStopStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit, capital_pct=0.05, commission_pct=0.001, slippage_pct=0.0005), ema_period=ema_period, atr_period=atr_period, atr_multiplier=atr_multiplier)
        elif cls == RSIStrategy:
            strategy = RSIStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit, capital_pct=0.05, commission_pct=0.001, slippage_pct=0.0005), rsi_period=rsi_period, oversold=oversold, overbought=overbought)
        elif cls == BollingerBandsStrategy:
            strategy = BollingerBandsStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit, capital_pct=0.05, commission_pct=0.001, slippage_pct=0.0005))
        elif cls == SMAcrossStrategy:
            strategy = SMAcrossStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit, capital_pct=0.05, commission_pct=0.001, slippage_pct=0.0005))
        elif cls == KDJStrategy:
            strategy = KDJStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit, capital_pct=0.05, commission_pct=0.001, slippage_pct=0.0005))
        elif cls == MACDStrategy:
            strategy = MACDStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit, capital_pct=0.05, commission_pct=0.001, slippage_pct=0.0005))
        elif cls == WRIStrategy:
            strategy = WRIStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit, capital_pct=0.05, commission_pct=0.001, slippage_pct=0.0005))
        elif cls == CCIOScillatorStrategy:
            strategy = CCIOScillatorStrategy(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit, capital_pct=0.05, commission_pct=0.001, slippage_pct=0.0005))
        else:
            strategy = cls(StrategyConfig(symbol=symbol, timeframe=timeframe, stop_loss=stop_loss, take_profit=take_profit))
        use_confidence = False
    else:
        raise HTTPException(status_code=404, detail=f"未知策略: {strategy_name}")

    # ── 真实回测引擎 ──────────────────────────────────────
    engine = BacktestEngine(strategy=strategy, initial_capital=10000)
    if not engine.load_data():
        raise HTTPException(status_code=500, detail="K线数据加载失败")
    result = engine.run()

    # ── 转换 equity_curve 为前端格式 ──────────────────────
    equity_curve = [{"t": ts, "v": round(eq, 2)} for ts, eq in result.equity_curve]

    # ── 买卖点 ───────────────────────────────────────────
    buy_markers  = []
    sell_markers = []
    for t in result.trades:
        if t.side == "long":
            buy_markers.append({"t": t.entry_time, "price": t.entry_price,
                                 "pnl_pct": round(t.pnl_pct * 100, 2),
                                 "exit_reason": t.exit_reason})
            sell_markers.append({"t": t.exit_time, "price": t.exit_price,
                                  "pnl_pct": round(t.pnl_pct * 100, 2),
                                  "exit_reason": t.exit_reason})
        else:
            sell_markers.append({"t": t.entry_time, "price": t.entry_price,
                                  "pnl_pct": round(t.pnl_pct * 100, 2),
                                  "exit_reason": t.exit_reason})
            buy_markers.append({"t": t.exit_time, "price": t.exit_price,
                                 "pnl_pct": round(t.pnl_pct * 100, 2),
                                 "exit_reason": t.exit_reason})

    # ── 指标线（最后 250 根 K线）────────────────────────
    n_show = 250
    candles_all = engine.candles
    n = len(candles_all)
    start = max(0, n - n_show)
    candles_show = candles_all[start:]
    try:
        indicators = strategy.populate_indicators(candles_all)
        indicators_out = {}
        for k, v in indicators.items():
            if len(v) <= start:
                continue
            indicators_out[k] = [{"t": candles_all[min(start + i, n - 1)]["timestamp"], "v": round(val, 4)}
                                  for i, val in enumerate(v[start:]) if val != 0]
    except Exception:
        indicators_out = {}

    # ── K线数据 ─────────────────────────────────────────
    ohlc = [{"t": c["timestamp"], "o": c["open"], "h": c["high"],
             "l": c["low"], "c": c["close"]} for c in candles_show]

    # 策略投票权重
    strategy_weights = {}
    if strategy_name == "MultiVote":
        for s, w in vote.strategies:
            strategy_weights[s.__class__.__name__] = w

    return {
        "strategy":       strategy_name,
        "symbol":         symbol,
        "timeframe":      timeframe,
        "ohlc":           ohlc,
        "equity_curve":   equity_curve,
        "buy_markers":    buy_markers,
        "sell_markers":   sell_markers,
        "indicators":     indicators_out,
        "weights":        strategy_weights,
        "confidence_enabled": use_confidence,
        "performance": {
            "total_return_pct":   round(result.total_return_pct, 2),
            "sharpe_ratio":       round(result.sharpe_ratio, 2),
            "max_drawdown_pct":   round(result.max_drawdown_pct, 2),
            "win_rate_pct":       round(result.win_rate_pct, 2),
            "total_trades":       result.total_trades,
            "start_date":         result.start_date,
            "end_date":           result.end_date,
        },
    }


@app.get("/api/backtest/strategies")
async def list_backtest_strategies():
    """列出所有可回测的策略"""
    return {
        "single": list(BUILTIN_FORMULAS.keys()),
        "multi": ["MultiVote"],
        "registered": list(STRATEGY_REGISTRY.keys()) if "STRATEGY_REGISTRY" in dir() else [],
    }


# ================================================================
# J: 实时 PnL / 权益曲线 API
# ================================================================

@app.get("/api/pnl/summary")
async def get_pnl_summary():
    """获取所有 Agent 的实时盈亏摘要（从数据库读取）"""
    try:
        import sqlite3, os
        # 尝试从 live_trading 模块导入 orchestrator（同一进程）
        # 若不可用，从数据库读取最近状态
        try:
            from live_trading import orchestrator as _orch
        except Exception:
            _orch = None

        if _orch:
            status_list = _orch.get_all_status()
        else:
            # 从数据库构造最近状态（直接从 signal_log / positions 取真实 symbol）
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_trading.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            agents = conn.execute("""
                SELECT DISTINCT agent_id FROM equity_log
                WHERE created_at > datetime('now', '-1 day')
                ORDER BY agent_id
            """).fetchall()
            status_list = []
            from config import LIVE_INITIAL_CAPITAL
            # 重新加载 dotenv 获取最新 AGENT_SYMBOLS（uvicorn reload 不监听 .env 变化）
            import os as _os
            try:
                from dotenv import load_dotenv as _ld
                _ld(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)
            except Exception:
                pass
            # 绕过 config 缓存：直接用 os.getenv 获取最新 AGENT_SYMBOLS
            _agent_symbols = os.getenv("AGENT_SYMBOLS",
                "BTC/USDT:VOTE:weex,ETH/USDT:VOTE:weex,SOL/USDT:RSI:weex,SUI/USDT:AUTO:weex,ZEC/USDT:RSI:weex")
            # 构建 agent → (symbol, strategy) 映射
            _agent_info = {}
            for i, cfg in enumerate(_agent_symbols.split(",")):
                parts = cfg.strip().split(":")
                sym = parts[0].strip() if parts else cfg.strip()
                strat = parts[1].strip().upper() if len(parts) > 1 else "RSI"
                _agent_info[f"agent_{i+1}"] = (sym, strat)
            for a in agents:
                aid = a["agent_id"]
                latest = conn.execute("""
                    SELECT agent_id, equity, in_position FROM equity_log
                    WHERE agent_id=? ORDER BY id DESC LIMIT 1
                """, (aid,)).fetchone()
                if latest:
                    init_cap = LIVE_INITIAL_CAPITAL
                    equity = latest["equity"]
                    info = _agent_info.get(aid, (aid, "?"))
                    sym, strat = info[0], info[1]
                    status_list.append({
                        "agent_id": aid,
                        "symbol": sym,
                        "strategy": strat,
                        "equity": round(equity, 2),
                        "return_pct": round((equity - init_cap) / init_cap * 100, 2),
                        "in_position": bool(latest["in_position"]),
                        "risk_level": "normal",
                    })
            conn.close()

        if status_list:
            total_equity = sum(s["equity"] for s in status_list)
            init_total = LIVE_INITIAL_CAPITAL * len(status_list)
            total_return_pct = round((total_equity - init_total) / init_total * 100, 2) if init_total > 0 else 0
            return {
                "agents": [
                    {
                        "agent_id": s.get("agent_id", ""),
                        "symbol": s.get("symbol", s.get("agent_id", "")),
                        "strategy": s.get("strategy", "VOTE"),
                        "equity": round(s["equity"], 2),
                        "return_pct": round(s.get("return_pct", s.get("total_return_pct", 0)), 2),
                        "in_position": s.get("in_position", s.get("position") is not None if isinstance(s.get("position"), dict) else False),
                        "risk_level": s.get("risk_level", "normal"),
                    }
                    for s in status_list
                ],
                "total_equity": round(total_equity, 2),
                "total_return_pct": round(total_return_pct, 2),
                "agent_count": len(status_list),
            }
        return {"agents": [], "total_equity": 0, "total_return_pct": 0}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/pnl/daily-trades")
async def get_daily_trades():
    """获取当日交易数（从 live_trading.db）"""
    import sqlite3, os
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_trading.db")
    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
        conn.close()
        return {"count": count}
    except Exception:
        return {"count": 0}


@app.get("/api/pnl/equity")
async def get_equity_curve(agent_id: str = None, limit: int = 100):
    """获取权益曲线数据（从 equity_log 表读取）"""
    try:
        import sqlite3
        from live_trading import DB_PATH as _DB_PATH_LIVE
        conn = sqlite3.connect(_DB_PATH_LIVE)
        conn.row_factory = sqlite3.Row
        if agent_id:
            rows = conn.execute(
                "SELECT agent_id, timestamp, equity, in_position FROM equity_log "
                "WHERE agent_id=? ORDER BY timestamp DESC LIMIT ?",
                (agent_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT agent_id, timestamp, equity, in_position FROM equity_log "
                "ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        conn.close()
        curves = {}
        for r in rows:
            aid = r["agent_id"]
            curves.setdefault(aid, []).append({
                "t": r["timestamp"] // 1000,
                "v": round(r["equity"], 2),
                "in_position": bool(r["in_position"]),
            })
        for k in curves:
            curves[k].reverse()
        return {"curves": curves}
    except Exception as e:
        return {"error": str(e)}


# ================================================================
# 在线参数优化 API
# ================================================================

@app.get("/api/optimizer/history")
async def get_optimizer_history(symbol: str = None, limit: int = 30):
    """查询参数优化变更历史"""
    try:
        import sqlite3
        db = "live_trading.db"
        conn = sqlite3.connect(db)
        params = []
        sql = "SELECT symbol, param_name, old_value, new_value, reason, trade_count, win_rate, created_at FROM param_history"
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [
            {"symbol": r[0], "param": r[1], "old": r[2], "new": r[3],
             "reason": r[4], "trades": r[5], "win_rate": r[6], "time": r[7]}
            for r in rows
        ]
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/backtest/compare")
async def compare_strategies(symbol: str = "ETH/USDT", timeframe: str = "4h",
                             direction: str = "long"):
    """运行全部6个策略对比回测并返回排行"""
    try:
        from backtest import BacktestEngine
        from strategies import (RSIStrategy, SMAcrossStrategy, MACDStrategy,
                                BollingerBandsStrategy, KDJStrategy, ATRStopStrategy,
                                StrategyConfig)
        from history_cache import get_ohlcv, init_cache_db
        init_cache_db()

        cfg = StrategyConfig(symbol=symbol, timeframe=timeframe,
                            stop_loss=0.02, take_profit=0.04,
                            trade_direction=direction)
        strats = {
            "RSI": RSIStrategy(cfg, rsi_period=14, oversold=28, overbought=65),
            "SMA": SMAcrossStrategy(cfg),
            "MACD": MACDStrategy(cfg),
            "BOLLINGER": BollingerBandsStrategy(cfg),
            "KDJ": KDJStrategy(cfg),
            "ATRSTOP": ATRStopStrategy(cfg),
        }
        candles = get_ohlcv(symbol, timeframe, limit=5000)
        if len(candles) < 100:
            return {"error": f"数据不足（{len(candles)} 条）"}

        rankings = []
        for name, s in strats.items():
            try:
                engine = BacktestEngine(s, initial_capital=10000, trade_direction=direction)
                engine.candles = candles
                engine.compute_signals()
                r = engine.run()
                rankings.append({
                    "strategy": name, "return": round(r.total_return_pct, 2),
                    "sharpe": r.sharpe_ratio, "drawdown": round(r.max_drawdown_pct, 2),
                    "win_rate": round(r.win_rate_pct, 1), "trades": r.total_trades,
                })
            except Exception as e:
                rankings.append({"strategy": name, "error": str(e)})
        rankings.sort(key=lambda x: x.get("return", -999), reverse=True)
        return {"symbol": symbol, "timeframe": timeframe, "direction": direction,
                "rankings": rankings}
    except Exception as e:
        return {"error": str(e)}

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
                    <div class="sys-stat-value"><span id="menxia-status" class="status-badge" style="background:#00c853;color:#000;">就绪</span></div>
                </div>
                <div class="sys-stat-card" id="stat-shangshu">
                    <div class="sys-stat-label">⚙️ 尚书省</div>
                    <div class="sys-stat-value"><span id="shangshu-status" class="status-badge" style="background:#00c853;color:#000;">就绪</span></div>
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
            <button class="tab" onclick="switchTab('live-equity')">📈 实时权益</button>
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

        <!-- 实时权益曲线 -->
        <div id="tab-live-equity" class="tab-content">
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;">
                    <h2 style="margin:0;">📈 实时权益曲线</h2>
                    <div style="display:flex;gap:8px;">
                        <select id="eq-agent-filter" onchange="loadLiveEquity()" style="width:auto;margin:0;">
                            <option value="">全部 Agent</option>
                        </select>
                        <button class="btn btn-primary" onclick="loadLiveEquity()" style="margin:0;">🔄 刷新</button>
                    </div>
                </div>
                <div id="equity-summary" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-bottom:15px;"></div>
                <div class="card" style="background:#0d1b2a;">
                    <div id="live-equity-chart" style="height:350px;"></div>
                </div>
                <p class="refresh-info">自动刷新间隔: 30秒</p>
            </div>

            <!-- 策略对比排名 -->
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;flex-wrap:wrap;gap:10px;">
                    <h2 style="margin:0;">🏆 策略对比排名</h2>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        <select id="compare-symbol" onchange="loadCompareRanking()" style="width:120px;margin:0;">
                            <option value="ETH/USDT" selected>ETH/USDT</option>
                            <option value="BTC/USDT">BTC/USDT</option>
                            <option value="SOL/USDT">SOL/USDT</option>
                            <option value="SUI/USDT">SUI/USDT</option>
                        </select>
                        <select id="compare-tf" onchange="loadCompareRanking()" style="width:70px;margin:0;">
                            <option value="4h" selected>4h</option>
                            <option value="1h">1h</option>
                            <option value="1d">1d</option>
                        </select>
                        <select id="compare-dir" onchange="loadCompareRanking()" style="width:80px;margin:0;">
                            <option value="long" selected>做多</option>
                            <option value="short">做空</option>
                            <option value="both">多空</option>
                        </select>
                        <button class="btn btn-primary" onclick="loadCompareRanking()" style="margin:0;">▶ 开始对比</button>
                    </div>
                </div>
                <div id="compare-table"></div>
                <div id="compare-verdict" style="margin-top:10px;font-size:13px;color:#aaa;"></div>
            </div>

            <!-- 投票参数配置 -->
            <div class="card">
                <h2>🎛 多策略投票配置</h2>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:10px;">
                    <div><label style="font-size:12px;color:#888;">RSI 权重</label>
                        <input type="range" id="vw-rsi" min="0" max="100" value="40" oninput="updateVoteWeights()"><span id="vw-rsi-v">40%</span></div>
                    <div><label style="font-size:12px;color:#888;">MACD 权重</label>
                        <input type="range" id="vw-macd" min="0" max="100" value="30" oninput="updateVoteWeights()"><span id="vw-macd-v">30%</span></div>
                    <div><label style="font-size:12px;color:#888;">BOLL 权重</label>
                        <input type="range" id="vw-boll" min="0" max="100" value="30" oninput="updateVoteWeights()"><span id="vw-boll-v">30%</span></div>
                    <div><label style="font-size:12px;color:#888;">投票阈值</label>
                        <input type="range" id="vw-th" min="0" max="50" value="30" step="5" oninput="updateVoteThreshold()"><span id="vw-th-v">0.30</span></div>
                </div>
                <div id="vote-config-display" style="font-size:13px;color:#aaa;"></div>
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
        
        // 更新顶部统计：运行时间、今日交易、持仓数
        async function loadHeaderStats() {
            try {
                const stRes = await fetch('/api/system/status');
                const stData = await stRes.json();
                const uptime = stData.uptime || 0;
                const h = Math.floor(uptime / 3600);
                const m = Math.floor((uptime % 3600) / 60);
                const s = uptime % 60;
                document.getElementById('uptime-display').textContent = 
                    `⏱ 运行时间: ${h}h ${m}m ${s}s`;
            } catch(e) {}
            try {
                const pnlRes = await fetch('/api/pnl/summary');
                const pnlData = await pnlRes.json();
                if (pnlData.agents) {
                    const inPos = pnlData.agents.filter(a => a.in_position).length;
                    document.getElementById('position-count').textContent = inPos;
                }
            } catch(e) {}
            try {
                // 从 live_trading.db 获取当日交易数
                const trRes = await fetch('/api/pnl/daily-trades');
                const trData = await trRes.json();
                document.getElementById('daily-trades-count').textContent = trData.count || 0;
            } catch(e) {}
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
                            <tr><th>代码</th><th>市场</th><th>方向</th><th>数量</th><th>成本价</th><th>当前价</th><th>盈亏</th><th>盈亏率</th></tr>
                            ${positions.map(p => {
                                const pnl = p.pnl || 0;
                                const pnl_pct = p.pnl_pct || 0;
                                const cls = pnl >= 0 ? 'profit' : 'loss';
                                const sideBadge = p.side === 'SHORT' ? '🔴空' : '🟢多';
                                return `<tr>
                                    <td><b>${p.symbol}</b><br><small>${p.exchange||''}</small></td>
                                    <td>${p.market}</td>
                                    <td>${sideBadge}</td>
                                    <td>${p.quantity}</td>
                                    <td>$${p.avg_price?.toFixed(4) || 0}</td>
                                    <td>$${p.current_price?.toFixed(4) || '-'}</td>
                                    <td class="${cls}">$${pnl.toFixed(2)}</td>
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
                            <td>$${(value.total_cost||0).toFixed(2)}</td>
                            <td>$${(value.total_value||0).toFixed(2)}</td>
                            <td class="${cls}">$${pnl.toFixed(2)}</td>
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
            const targetTab = document.querySelector(`.tab[onclick="switchTab('${tab}')"]`);
            if (targetTab) targetTab.classList.add('active');
            const targetContent = document.getElementById('tab-' + tab);
            if (targetContent) targetContent.classList.add('active');
            if (tab === 'backtest') loadBacktestChart();
            if (tab === 'live-equity') { loadLiveEquity(); loadCompareRanking(); }
            if (tab === 'replay') loadReplayAll();
        }

        // ================================================================
        // 实时权益曲线
        // ================================================================

        let liveEquityChart = null;
        const AGENT_COLORS = ['#00d4ff', '#ff9800', '#00c853', '#e040fb', '#ff1744', '#ffeb3b', '#00bcd4', '#8bc34a'];

        async function loadLiveEquity() {
            const filter = document.getElementById('eq-agent-filter').value;
            try {
                const url = filter ? `/api/pnl/equity?agent_id=${encodeURIComponent(filter)}&limit=200` : '/api/pnl/equity?limit=200';
                const res = await fetch(url);
                const data = await res.json();
                const curves = data.curves || {};

                // 更新 Agent 过滤器
                const agentIds = Object.keys(curves);
                const sel = document.getElementById('eq-agent-filter');
                sel.innerHTML = '<option value="">全部 Agent</option>' + agentIds.map(a => `<option value="${a}" ${a===filter?'selected':''}>${a}</option>`).join('');

                // 加载 PnL 概要
                try {
                    const sumRes = await fetch('/api/pnl/summary');
                    const sumData = await sumRes.json();
                    const summaryDiv = document.getElementById('equity-summary');
                    if (sumData.agents && sumData.agents.length > 0) {
                        summaryDiv.innerHTML = sumData.agents.map(a => {
                            const cls = a.return_pct >= 0 ? 'profit' : 'loss';
                            const posTag = a.in_position ? '🟢 持仓' : '⚪ 空仓';
                            const riskColors = {normal:'#00c853',caution:'#ff9800',warning:'#ff5722',locked:'#ff1744'};
                            const riskColor = riskColors[a.risk_level] || '#888';
                            return `<div class="sys-stat-card" style="border-left-color:${a.return_pct>=0?'#00c853':'#ff1744'};">
                                <div class="sys-stat-label">${a.agent_id} · ${a.symbol}</div>
                                <div class="sys-stat-value" style="font-size:18px;">$${a.equity.toLocaleString()}</div>
                                <div style="font-size:13px;margin-top:4px;">
                                    <span class="${cls}">${a.return_pct>=0?'+':''}${a.return_pct}%</span>
                                    <span style="margin-left:8px;color:${riskColor};">${posTag}</span>
                                    <span style="margin-left:8px;font-size:11px;color:#888;">${a.strategy||'VOTE'}</span>
                                </div>
                            </div>`;
                        }).join('');
                    } else {
                        summaryDiv.innerHTML = '<div style="color:#888;padding:10px;">暂无运行中的 Agent</div>';
                    }
                } catch(e) {}

                // 渲染权益曲线（清除旧序列）
                const chartDiv = document.getElementById('live-equity-chart');
                if (!liveEquityChart) {
                    liveEquityChart = LightweightCharts.createChart(chartDiv, {
                        width: chartDiv.clientWidth || 800,
                        height: 350,
                        layout: { backgroundColor: '#0d1b2a', textColor: '#aaa' },
                        grid: { vertLines: { color: '#1a2a3a' }, horzLines: { color: '#1a2a3a' } },
                        rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } },
                    });
                    liveEquityChart._seriesRefs = [];
                }

                // 清除旧序列
                // Lightweight Charts v4: no .series() method, track refs
                if (liveEquityChart._seriesRefs) {
                    liveEquityChart._seriesRefs.forEach(s => liveEquityChart.removeSeries(s));
                }
                liveEquityChart._seriesRefs = [];

                let ci = 0;
                for (const [agentId, points] of Object.entries(curves)) {
                    if (!points || points.length < 2) continue;
                    const color = AGENT_COLORS[ci % AGENT_COLORS.length]; ci++;
                    const series = liveEquityChart.addLineSeries({ color, lineWidth: 2, title: agentId });
                    liveEquityChart._seriesRefs.push(series);
                    series.setData(points.map(p => ({ time: p.t, value: p.v })));
                }
                liveEquityChart.timeScale().fitContent();

            } catch(e) {
                document.getElementById('equity-summary').innerHTML = `<div style="color:#ff1744;">加载失败: ${e.message}</div>`;
            }
        }

        // ================================================================
        // 策略对比排名
        // ================================================================

        async function loadCompareRanking() {
            const symbol = document.getElementById('compare-symbol').value;
            const timeframe = document.getElementById('compare-tf').value;
            const direction = document.getElementById('compare-dir').value;
            const tableDiv = document.getElementById('compare-table');
            const verdictDiv = document.getElementById('compare-verdict');

            tableDiv.innerHTML = '<div style="color:#888;padding:10px;">⏳ 回测中...</div>';
            verdictDiv.innerHTML = '';

            try {
                const res = await fetch(`/api/backtest/compare?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&direction=${direction}`);
                const data = await res.json();
                if (data.error) { tableDiv.innerHTML = `<div style="color:#ff1744;">${data.error}</div>`; return; }

                const rankings = data.rankings || [];
                let html = `<table><thead><tr>
                    <th>#</th><th>策略</th><th>收益率</th><th>夏普</th><th>最大回撤</th><th>胜率</th><th>交易数</th>
                </tr></thead><tbody>`;
                rankings.forEach((r, i) => {
                    const retCls = (r.return||0) >= 0 ? 'profit' : 'loss';
                    const srCls = (r.sharpe||0) >= 1 ? 'profit' : ((r.sharpe||0) >= 0 ? '' : 'loss');
                    const ddCls = (r.drawdown||0) <= 10 ? 'profit' : ((r.drawdown||0) <= 20 ? '' : 'loss');
                    html += `<tr>
                        <td><b>${i+1}</b></td>
                        <td style="font-weight:bold;color:#00d4ff;">${r.strategy}</td>
                        <td class="${retCls}">${(r.return||0)>=0?'+':''}${(r.return||0).toFixed(2)}%</td>
                        <td class="${srCls}">${(r.sharpe||0).toFixed(2)}</td>
                        <td class="${ddCls}">${(r.drawdown||0).toFixed(2)}%</td>
                        <td>${(r.win_rate||0).toFixed(1)}%</td>
                        <td>${r.trades||0}</td>
                    </tr>`;
                });
                html += '</tbody></table>';
                tableDiv.innerHTML = html;

                const best = rankings[0];
                if (best && !best.error) {
                    verdictDiv.innerHTML = `🏆 最优策略: <b style="color:#00d4ff;">${best.strategy}</b> | 收益率: <b class="profit">${best.return>=0?'+':''}${best.return}%</b> | 夏普: ${best.sharpe} | 交易: ${best.trades}笔 | ${symbol} ${timeframe} (${direction})`;
                }
            } catch(e) {
                tableDiv.innerHTML = `<div style="color:#ff1744;">加载失败: ${e.message}</div>`;
            }
        }

        // ================================================================
        // 投票参数配置
        // ================================================================

        function updateVoteWeights() {
            const rsi = parseInt(document.getElementById('vw-rsi').value);
            const macd = parseInt(document.getElementById('vw-macd').value);
            const boll = parseInt(document.getElementById('vw-boll').value);
            const total = rsi + macd + boll || 1;
            document.getElementById('vw-rsi-v').textContent = Math.round(rsi/total*100) + '%';
            document.getElementById('vw-macd-v').textContent = Math.round(macd/total*100) + '%';
            document.getElementById('vw-boll-v').textContent = Math.round(boll/total*100) + '%';
            displayVoteConfig();
        }

        function updateVoteThreshold() {
            const th = parseInt(document.getElementById('vw-th').value) / 100;
            document.getElementById('vw-th-v').textContent = th.toFixed(2);
            displayVoteConfig();
        }

        function displayVoteConfig() {
            const rsi = parseInt(document.getElementById('vw-rsi').value);
            const macd = parseInt(document.getElementById('vw-macd').value);
            const boll = parseInt(document.getElementById('vw-boll').value);
            const total = rsi + macd + boll || 1;
            const th = parseInt(document.getElementById('vw-th').value) / 100;

            const rsiPct = Math.round(rsi/total*100);
            const macdPct = Math.round(macd/total*100);
            const bollPct = Math.round(boll/total*100);

            const div = document.getElementById('vote-config-display');
            div.innerHTML = `当前配置: <b>RSI ${rsiPct}% + MACD ${macdPct}% + BOLL ${bollPct}%</b> | 阈值: <b>${th.toFixed(2)}</b> | ` +
                (th >= 0.35 ? '🛡 高阈值-严格过滤' : th >= 0.2 ? '⚖ 中阈值-平衡模式' : '🎯 低阈值-信号密集');
        }

        // 初始化投票配置显示
        displayVoteConfig();

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
        loadHeaderStats();
        setInterval(loadAll, 30000);  // 每30秒刷新
        // 权益曲线也定时刷新
        setInterval(() => {
            if (document.getElementById('tab-live-equity').classList.contains('active')) {
                loadLiveEquity();
            }
        }, 30000);
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
