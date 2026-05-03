"""
交易监控系统 - 主入口
默认启动 Web Dashboard + 后台行情监控
所有功能集成到 Web 界面
"""
import argparse
import asyncio
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from database import init_db
from portfolio import Portfolio
from monitor import PriceMonitor, quick_price_check
from feishu_alert import feishu_alert
from dashboard import run_server, app, update_monitor_status

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 全局监控器实例
_monitor = None
_monitor_thread = None
_monitor_running = False

def get_monitor_symbols():
    """获取监控品种列表"""
    return [
        {"symbol": "BTC", "market": "CRYPTO"},
        {"symbol": "ETH", "market": "CRYPTO"},
        {"symbol": "AAPL", "market": "US"},
        {"symbol": "TSLA", "market": "US"},
        {"symbol": "00700", "market": "HK"},
        {"symbol": "600000", "market": "CN"},
        {"symbol": "000001", "market": "CN"},
    ]

def monitor_loop_bg():
    """后台监控循环"""
    global _monitor_running
    
    logger.info("后台行情监控启动...")
    init_db()
    
    monitor = PriceMonitor(check_interval=30)
    _monitor_running = True
    
    # 添加飞书告警回调
    def on_alert(data):
        logger.info(f"告警触发: {data.get('symbol')} {data.get('change_pct')}%")
        try:
            feishu_alert.send_alert(
                symbol=data.get("symbol"),
                market=data.get("market"),
                alert_type=data.get("alert_type", "价格波动"),
                price=data.get("price", 0),
                threshold=data.get("threshold", 0),
                message=data.get("message", "")
            )
        except Exception as e:
            logger.error(f"告警发送失败: {e}")
    
    monitor.add_alert_callback(on_alert)
    update_monitor_status("running", f"监控 {len(get_monitor_symbols())} 个品种")
    
    try:
        asyncio.run(monitor.monitor_loop(get_monitor_symbols(), threshold=0.03))
    except Exception as e:
        logger.error(f"监控异常: {e}")
    finally:
        _monitor_running = False
        update_monitor_status("stopped", "监控已停止")

def start_monitor_bg():
    """启动后台监控"""
    global _monitor_thread, _monitor_running
    
    if _monitor_running:
        logger.info("监控已在运行中")
        return {"status": "running", "message": "监控已在运行"}
    
    _monitor_thread = threading.Thread(target=monitor_loop_bg, daemon=True)
    _monitor_thread.start()
    logger.info("后台监控线程已启动")
    return {"status": "started", "message": "监控已启动"}

def stop_monitor_bg():
    """停止后台监控"""
    global _monitor_running
    
    if not _monitor_running:
        return {"status": "stopped", "message": "监控未运行"}
    
    _monitor_running = False
    logger.info("监控停止信号已发送")
    return {"status": "stopping", "message": "监控正在停止"}

def get_monitor_status():
    """获取监控状态"""
    return {
        "running": _monitor_running,
        "status": "running" if _monitor_running else "stopped"
    }

def main():
    parser = argparse.ArgumentParser(description="交易监控系统")
    parser.add_argument("--mode", choices=["server", "monitor-only", "trade", "alert-test"],
                       default="server", help="运行模式")
    parser.add_argument("--port", type=int, default=8081, help="Web端口")
    parser.add_argument("--no-monitor", action="store_true", help="不启动后台监控")
    
    args = parser.parse_args()
    
    # 初始化数据库
    init_db()
    
    if args.mode == "server":
        logger.info("=" * 50)
        logger.info("交易监控系统启动")
        logger.info("=" * 50)
        
        # 启动后台监控（除非指定不启动）
        if not args.no_monitor:
            start_monitor_bg()
        
        # 启动 Web Dashboard
        logger.info(f"Web Dashboard: http://0.0.0.0:{args.port}")
        logger.info(f"Dashboard: http://localhost:{args.port}/dashboard")
        logger.info("按 Ctrl+C 停止服务")
        
        # 注册信号处理
        def signal_handler(sig, frame):
            logger.info("收到停止信号，正在关闭...")
            stop_monitor_bg()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # 启动 Web 服务
        run_server(port=args.port)
        
    elif args.mode == "monitor-only":
        # 仅运行监控（前台阻塞）
        monitor_loop_bg()
        
    elif args.mode == "trade":
        # 交易测试
        logger.info("交易测试...")
        portfolio = Portfolio()
        positions = portfolio.get_positions()
        logger.info(f"当前持仓: {positions}")
        value = portfolio.get_position_value()
        logger.info(f"持仓市值: {value}")
        
    elif args.mode == "alert-test":
        # 告警测试
        logger.info("发送测试告警...")
        feishu_alert.send_alert(
            symbol="BTC",
            market="CRYPTO",
            alert_type="价格突破",
            price=50000,
            threshold=5,
            message="这是测试告警"
        )
        logger.info("告警已发送")

if __name__ == "__main__":
    main()
