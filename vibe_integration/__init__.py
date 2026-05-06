"""
Vibe-Trading 集成模块
从 HKUDS/Vibe-Trading (https://github.com/HKUDS/Vibe-Trading) 适配

适配说明：
  - Vibe-Trading 需要 Python >= 3.11，此处直接适配其核心引擎到 Python 3.10
  - 数据源：akshare（免费）+ tushare Pro（免费）+ yfinance（免费）
  - 支持：A股 / 港股 / 美股 / 加密货币

核心组件：
  - ChinaAEngine: A股回测（T+1, 涨跌停, 佣金, 印花税）
  - GlobalEquityEngine: 港股/美股回测（T+0, 分数股, 港股印花税）
  - unified_backtest: 统一多市场回测入口
"""

from .stock_backtest import (
    ChinaAEngine,
    GlobalEquityEngine,
    run_stock_backtest,
    generate_stock_report,
    SimpleMASignal,
    RSISignal,
    fetch_stock_data,
)

# 别名兼容
StockBacktestEngine = None  # 已整合到 ChinaAEngine / GlobalEquityEngine

__all__ = [
    "ChinaAEngine",
    "GlobalEquityEngine",
    "run_stock_backtest",
    "generate_stock_report",
    "SimpleMASignal",
    "RSISignal",
    "fetch_stock_data",
    "StockBacktestEngine",
]
