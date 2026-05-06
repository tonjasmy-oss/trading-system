"""
回测引擎模块 - 向后测试交易策略的历史表现

功能：
  - 从本地 OHLCV 缓存或线上补充数据
  - 按策略执行模拟交易（考虑止损/止盈）
  - 计算核心绩效指标：收益率、夏普比率、最大回撤、交易次数、胜率

依赖：
  - strategies.py: 策略基类 + SMAcrossStrategy + RSIStrategy
  - history_cache.py: OHLCV 缓存读取
  - crypto_api.py: get_ohlcv() 补充线上数据

使用示例：
  python backtest.py BTC/USDT 1h SMAcrossStrategy
  python backtest.py ETH/USDT 4h RSIStrategy --capital-pct 0.5 --stop-loss 0.03
"""
import sys
import os
import json
import logging
import math
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path

# 加载项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies import Strategy, SMAcrossStrategy, RSIStrategy, Signal, StrategyConfig
from tdx_compiler import FormulaStrategy, BUILTIN_FORMULAS
from history_cache import get_ohlcv as cache_get_ohlcv, get_latest_timestamp, save_ohlcv, init_cache_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================
# 回测结果数据类
# ============================================================

@dataclass
class TradeRecord:
    """单笔交易记录"""
    entry_time:    int   # 入场时间戳（毫秒）
    entry_price:   float # 入场价格
    exit_time:     int   # 出场时间戳（毫秒）
    exit_price:    float # 出场价格
    quantity:      float # 成交数量
    pnl_pct:       float # 盈亏比例（%）
    exit_reason:   str   # 出场原因：'signal' | 'stop_loss' | 'take_profit'


@dataclass
class BacktestResult:
    """回测完整结果"""
    strategy_name:     str            # 策略名称
    symbol:            str            # 交易对
    timeframe:         str            # K线周期
    start_date:        str            # 数据开始日期
    end_date:          str            # 数据结束日期
    total_return_pct:  float          # 总收益率（%）
    sharpe_ratio:      float          # 夏普比率（年化）
    max_drawdown_pct: float          # 最大回撤（%）
    max_drawdown_duration_ms: int   # 最大回撤持续时间（毫秒）
    total_trades:      int            # 总交易次数
    winning_trades:   int            # 盈利次数
    losing_trades:    int            # 亏损次数
    win_rate_pct:     float          # 胜率（%）
    avg_holding_ms:   int            # 平均持仓时长（毫秒）
    stop_loss_pct:    float          # 止损比例配置
    take_profit_pct:  float          # 止盈比例配置
    capital_pct:      float          # 资金比例配置
    equity_curve:     List[Tuple[int, float]]  # (timestamp, equity) 时间序列
    trades:           List[TradeRecord] = field(default_factory=list)


# ============================================================
# 核心回测引擎
# ============================================================

class BacktestEngine:
    """
    回测引擎：逐根 K线模拟策略交易行为
    """

    def __init__(self, strategy: Strategy, initial_capital: float = 10000.0):
        """
        Args:
            strategy:        策略实例（须已配置好 config）
            initial_capital: 初始资金（默认 10000 USDT）
        """
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.config = strategy.get_config()

        # 运行状态
        self.candles: List[Dict] = []
        self.entry_signal: List[int] = []
        self.exit_signal:  List[int] = []

        # 持仓状态
        self.in_position: bool = False
        self.entry_price: float = 0.0
        self.entry_time: int   = 0
        self.stop_loss:   float = 0.0
        self.take_profit: float = 0.0

        # 结果收集
        self.trades: List[TradeRecord] = []
        self.equity_curve: List[Tuple[int, float]] = []  # (timestamp, equity)

    # -------------------- 数据加载 --------------------

    def load_data(self) -> bool:
        """
        从本地缓存加载数据，如数据不足（少于 100 条）则从线上补充

        Returns:
            True=成功加载，False=失败
        """
        symbol    = self.config.symbol
        timeframe = self.config.timeframe

        # 尝试从缓存读取（最多 5000 条）
        candles = cache_get_ohlcv(symbol, timeframe, limit=5000)
        logger.info(f"缓存命中 {len(candles)} 条 {symbol} {timeframe} 数据")

        # 缓存不足：从线上补充最近 6 个月数据
        if len(candles) < 100:
            logger.warning(f"缓存数据不足（{len(candles)} 条），从线上补充...")
            try:
                # 尝试补充最近 6 个月（约 180 天 * 24h = 4320 条 1h K线）
                since_ms = int((datetime.now().timestamp() - 180 * 24 * 3600) * 1000)
                import crypto_api
                online_data = crypto_api.get_ohlcv(symbol, timeframe, since=since_ms, limit=5000)
                if online_data:
                    save_ohlcv(symbol, timeframe, online_data)
                    logger.info(f"线上补充 {len(online_data)} 条，已写入缓存")
                    candles = cache_get_ohlcv(symbol, timeframe, limit=5000)
                else:
                    logger.error("线上补充数据失败")
            except Exception as e:
                logger.error(f"线上补充异常: {e}")

        if len(candles) < 10:
            logger.error(f"数据不足，无法回测（仅 {len(candles)} 条）")
            return False

        self.candles = candles
        return True

    # -------------------- 信号计算 --------------------

    def compute_signals(self):
        """调用策略计算入场/出场信号"""
        self.strategy.populate_indicators(self.candles)
        self.entry_signal = self.strategy.populate_entry_trend(self.candles)
        self.exit_signal  = self.strategy.populate_exit_trend(self.candles)
        logger.info("信号计算完成")

    # -------------------- 逐K线回测 --------------------

    def run(self) -> BacktestResult:
        """
        执行回测主循环

        逻辑：
          - 按时间顺序遍历每根 K线
          - 入场信号且空仓 → 开多（用 config.capital_pct 比例资金买入）
          - 出场信号（-1）或触发止损/止盈 → 平多
          - 每根 K线末记录当前权益（equity）
        """
        if not self.candles:
            raise RuntimeError("请先调用 load_data() 加载数据")

        stop_loss_pct  = self.config.stop_loss
        take_profit_pct = self.config.take_profit
        capital_pct    = self.config.capital_pct

        equity = self.initial_capital
        self.equity_curve = []

        for i, candle in enumerate(self.candles):
            ts    = candle["timestamp"]
            close = candle["close"]

            # ----- 记录权益 -----
            self.equity_curve.append((ts, equity))

            # ----- 如有持仓，检测止损/止盈 -----
            if self.in_position:
                pnl_pct = (close - self.entry_price) / self.entry_price

                if pnl_pct <= -stop_loss_pct:
                    # 触发止损
                    self._close_trade(ts, close, "stop_loss", equity, capital_pct)
                    equity = self._calc_equity(equity, capital_pct, pnl_pct, stop_loss=True)
                    self.in_position = False
                    continue

                if pnl_pct >= take_profit_pct:
                    # 触发止盈
                    self._close_trade(ts, close, "take_profit", equity, capital_pct)
                    equity = self._calc_equity(equity, capital_pct, pnl_pct, stop_loss=False)
                    self.in_position = False
                    continue

            # ----- 检测入场信号 -----
            if not self.in_position and self.entry_signal[i] == Signal.BUY:
                self.in_position = True
                self.entry_price = close
                self.entry_time  = ts
                self.stop_loss   = close * (1 - stop_loss_pct)
                self.take_profit  = close * (1 + take_profit_pct)

            # ----- 检测出场信号 -----
            elif self.in_position and self.exit_signal[i] == Signal.SELL:
                self._close_trade(ts, close, "signal", equity, capital_pct)
                pnl_pct = (close - self.entry_price) / self.entry_price
                equity = self._calc_equity(equity, capital_pct, pnl_pct)
                self.in_position = False

        # 最后如仍持仓，以最后收盘价结算
        if self.in_position and self.candles:
            last = self.candles[-1]
            pnl_pct = (last["close"] - self.entry_price) / self.entry_price
            equity = self._calc_equity(equity, capital_pct, pnl_pct)
            self.equity_curve[-1] = (last["timestamp"], equity)
            self.in_position = False

        return self._build_result(equity)

    # -------------------- 私有辅助 --------------------

    def _close_trade(self, exit_time: int, exit_price: float,
                     reason: str, equity: float, capital_pct: float):
        """记录一笔成交"""
        pnl_pct = (exit_price - self.entry_price) / self.entry_price
        qty = (equity * capital_pct) / self.entry_price
        self.trades.append(TradeRecord(
            entry_time   = self.entry_time,
            entry_price  = self.entry_price,
            exit_time    = exit_time,
            exit_price   = exit_price,
            quantity     = qty,
            pnl_pct      = pnl_pct * 100,
            exit_reason  = reason,
        ))

    @staticmethod
    def _calc_equity(equity: float, capital_pct: float,
                     pnl_pct: float, stop_loss: bool = False) -> float:
        """计算平仓后权益"""
        return equity + equity * capital_pct * pnl_pct

    def _build_result(self, final_equity: float) -> BacktestResult:
        """从交易记录计算绩效指标"""
        trades = self.trades
        total  = len(trades)
        wins   = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]

        # 总收益率
        total_return_pct = (final_equity - self.initial_capital) / self.initial_capital * 100

        # 最大回撤（逐点计算equity_curve）
        peak = self.initial_capital
        max_dd = 0.0
        max_dd_duration_ms = 0
        dd_start = 0
        for ts, eq in self.equity_curve:
            if eq > peak:
                peak = eq
                dd_start = ts
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
                max_dd_duration_ms = ts - dd_start

        # 夏普比率（年化，日收益序列）
        daily_returns = self._calc_daily_returns()
        if daily_returns and len(daily_returns) > 1:
            mean_ret = sum(daily_returns) / len(daily_returns)
            std_ret  = self._std(daily_returns)
            if std_ret > 0:
                sharpe = (mean_ret / std_ret) * math.sqrt(252)  # 年化夏普
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # 平均持仓时长
        if trades:
            holding_times = [t.exit_time - t.entry_time for t in trades]
            avg_holding_ms = int(sum(holding_times) / len(holding_times))
        else:
            avg_holding_ms = 0

        start_ts = self.candles[0]["timestamp"]  if self.candles else 0
        end_ts   = self.candles[-1]["timestamp"] if self.candles else 0

        return BacktestResult(
            strategy_name  = self.strategy.__class__.__name__,
            symbol         = self.config.symbol,
            timeframe      = self.config.timeframe,
            start_date     = datetime.fromtimestamp(start_ts / 1000).strftime("%Y-%m-%d"),
            end_date       = datetime.fromtimestamp(end_ts   / 1000).strftime("%Y-%m-%d"),
            total_return_pct   = total_return_pct,
            sharpe_ratio       = round(sharpe, 2),
            max_drawdown_pct   = round(max_dd, 2),
            max_drawdown_duration_ms = max_dd_duration_ms,
            total_trades       = total,
            winning_trades     = len(wins),
            losing_trades      = len(losses),
            win_rate_pct       = round(len(wins) / total * 100, 2) if total > 0 else 0.0,
            avg_holding_ms     = avg_holding_ms,
            stop_loss_pct      = self.config.stop_loss * 100,
            take_profit_pct    = self.config.take_profit * 100,
            capital_pct        = self.config.capital_pct * 100,
            equity_curve       = self.equity_curve,
            trades             = trades,
        )

    def _calc_daily_returns(self) -> List[float]:
        """将 equity_curve 转换为日收益率列表（按 timestamp 分组）"""
        if not self.equity_curve:
            return []
        # 按天聚合
        daily: Dict[str, float] = {}
        for ts, eq in self.equity_curve:
            day = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            daily[day] = eq  # 取每天最后一个 equity

        days = sorted(daily.keys())
        if len(days) < 2:
            return []
        returns = []
        for i in range(1, len(days)):
            prev = daily[days[i - 1]]
            curr = daily[days[i]]
            if prev > 0:
                returns.append((curr - prev) / prev)
        return returns

    @staticmethod
    def _std(values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return math.sqrt(variance)


# ============================================================
# 报告生成
# ============================================================

def generate_report(result: BacktestResult, output_dir: str = "backtest_results") -> str:
    """
    将回测结果输出为 Markdown 格式报告，并保存到 backtest_results/ 目录
    """
    os.makedirs(output_dir, exist_ok=True)
    safe_name = result.symbol.replace("/", "_")
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(output_dir, f"report_{safe_name}_{result.timeframe}_{result.strategy_name}_{timestamp_str}.md")

    # 格式化交易列表
    trade_lines = []
    for i, t in enumerate(result.trades, 1):
        entry_dt  = datetime.fromtimestamp(t.entry_time / 1000).strftime("%Y-%m-%d %H:%M")
        exit_dt   = datetime.fromtimestamp(t.exit_time   / 1000).strftime("%Y-%m-%d %H:%M")
        holding_h = (t.exit_time - t.entry_time) / 3600000
        pnl_emoji = "🟢" if t.pnl_pct > 0 else "🔴"
        trade_lines.append(
            f"| {i} | {entry_dt} | {exit_dt} | "
            f"{t.entry_price:.4f} | {t.exit_price:.4f} | "
            f"{t.quantity:.6f} | {t.pnl_pct:+.2f}% | "
            f"{t.exit_reason} |"
        )

    trades_table = "\n".join(trade_lines) if trade_lines else "| — | 数据不足，无成交 |"

    md = f"""# 回测报告

## 基本信息

| 项目 | 值 |
|------|-----|
| **策略名称** | {result.strategy_name} |
| **交易对** | {result.symbol} |
| **K线周期** | {result.timeframe} |
| **数据区间** | {result.start_date} ~ {result.end_date} |
| **止损比例** | {result.stop_loss_pct:.1f}% |
| **止盈比例** | {result.take_profit_pct:.1f}% |
| **下单资金比例** | {result.capital_pct:.1f}% |

---

## 核心绩效指标

| 指标 | 值 |
|------|-----|
| **总收益率** | {result.total_return_pct:+.2f}% |
| **夏普比率（年化）** | {result.sharpe_ratio:.2f} |
| **最大回撤** | {result.max_drawdown_pct:.2f}% |
| **最大回撤持续** | {result.max_drawdown_duration_ms / 3600000:.1f} 小时 |
| **总交易次数** | {result.total_trades} 次 |
| **盈利次数** | {result.winning_trades} 次 |
| **亏损次数** | {result.losing_trades} 次 |
| **胜率** | {result.win_rate_pct:.2f}% |
| **平均持仓时长** | {result.avg_holding_ms / 3600000:.2f} 小时 |

---

## 交易明细

| # | 入场时间 | 出场时间 | 入场价 | 出场价 | 数量 | 盈亏 | 出场原因 |
|---|---------|---------|-------|-------|-----|------|---------|
{trades_table}

---

*报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} UTC*
"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(md)

    logger.info(f"报告已保存: {filename}")
    return filename


def print_summary(result: BacktestResult):
    """打印回测结果摘要到控制台"""
    print("\n" + "=" * 55)
    print(f"  回测结果摘要  {result.symbol} {result.timeframe}")
    print("=" * 55)
    print(f"  策略          : {result.strategy_name}")
    print(f"  数据区间      : {result.start_date} ~ {result.end_date}")
    print(f"  总收益率      : {result.total_return_pct:+.2f}%")
    print(f"  夏普比率      : {result.sharpe_ratio:.2f}")
    print(f"  最大回撤      : {result.max_drawdown_pct:.2f}%")
    print(f"  总交易次数    : {result.total_trades}")
    print(f"  胜率          : {result.win_rate_pct:.2f}%")
    print("=" * 55)


# ============================================================
# 主入口（支持命令行调用）
# ============================================================

def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description="加密货币策略回测引擎")
    parser.add_argument("symbol",      nargs="?", default="BTC/USDT",  help="交易对，如 BTC/USDT")
    parser.add_argument("timeframe",   nargs="?", default="1h",        help="K线周期：1m/5m/15m/1h/4h/1d")
    parser.add_argument("strategy",    nargs="?", default="SMAcrossStrategy",
                        choices=["SMAcrossStrategy", "RSIStrategy"], help="策略名称")
    parser.add_argument("--capital-pct",  type=float, default=1.0,   help="每次下单资金比例（0~1），默认 1.0")
    parser.add_argument("--stop-loss",     type=float, default=0.05,  help="止损比例，默认 0.05（5%%）")
    parser.add_argument("--take-profit",   type=float, default=0.10,  help="止盈比例，默认 0.10（10%%）")
    parser.add_argument("--fast-period",   type=int,   default=10,    help="SMA 快线周期（仅 SMAcrossStrategy）")
    parser.add_argument("--slow-period",   type=int,   default=30,    help="SMA 慢线周期（仅 SMAcrossStrategy）")
    parser.add_argument("--rsi-period",    type=int,   default=14,     help="RSI 周期（仅 RSIStrategy）")
    parser.add_argument("--rsi-oversold",   type=float, default=30.0,   help="RSI 超卖阈值（仅 RSIStrategy）")
    parser.add_argument("--rsi-overbought",  type=float, default=70.0,  help="RSI 超买阈值（仅 RSIStrategy）")
    parser.add_argument("--initial-capital",type=float, default=10000.0,help="初始资金（USDT）")
    parser.add_argument("--output-dir",     default="backtest_results", help="报告输出目录")
    parser.add_argument("--formula",       type=str, default=None,
                        help="通达信公式字符串（支持内置名如 KDJ/MACD/RSI/BOLL/WR/MA_CROSS）")
    parser.add_argument("--formula-file",  type=str, default=None,
                        help="从文件加载通达信公式")
    return parser.parse_args()


def main():
    args = parse_args()

    # 实例化策略
    strategy: Strategy
    formula_src = None

    if args.formula_file:
        with open(args.formula_file, "r", encoding="utf-8") as f:
            formula_src = f.read()
    elif args.formula:
        # 内置公式名？
        if args.formula.upper() in BUILTIN_FORMULAS:
            formula_src = BUILTIN_FORMULAS[args.formula.upper()]
            logger.info(f"使用内置公式: {args.formula.upper()}")
        else:
            formula_src = args.formula
    else:
        formula_src = None

    if formula_src:
        # 通达信公式策略
        config = StrategyConfig(
            symbol      = args.symbol,
            timeframe   = args.timeframe,
            capital_pct = args.capital_pct,
            stop_loss   = args.stop_loss,
            take_profit = args.take_profit,
        )
        strategy = FormulaStrategy(
            formula    = formula_src,
            symbol     = args.symbol,
            timeframe  = args.timeframe,
            stop_loss  = args.stop_loss,
            take_profit= args.take_profit,
        )
        logger.info(f"策略: FormulaStrategy（通达信公式）")
    else:
        # 构建策略配置
        config = StrategyConfig(
            symbol      = args.symbol,
            timeframe   = args.timeframe,
            capital_pct = args.capital_pct,
            stop_loss   = args.stop_loss,
            take_profit = args.take_profit,
        )
        # 实例化内置策略
        if args.strategy == "SMAcrossStrategy":
            strategy = SMAcrossStrategy(
                config      = config,
                fast_period = args.fast_period,
                slow_period = args.slow_period,
            )
        elif args.strategy == "RSIStrategy":
            strategy = RSIStrategy(
                config    = config,
                rsi_period  = args.rsi_period,
                oversold    = args.rsi_oversold,
                overbought  = args.rsi_overbought,
            )
        else:
            raise ValueError(f"未知策略: {args.strategy}")
        logger.info(f"策略: {args.strategy}")

    # 初始化缓存（确保表存在）
    init_cache_db()

    # 执行回测
    engine = BacktestEngine(strategy, initial_capital=args.initial_capital)
    if not engine.load_data():
        logger.error("数据加载失败，退出")
        sys.exit(1)

    engine.compute_signals()
    result = engine.run()

    # 输出
    print_summary(result)
    report_path = generate_report(result, output_dir=args.output_dir)
    print(f"\n📄 完整报告: {report_path}")

    return result


# ============================================================
# 股票回测入口（支持 A股 / 港股 / 美股）
# ============================================================

def main_stock():
    """股票回测 CLI 入口，调用 vibe_integration"""
    try:
        from vibe_integration import run_stock_backtest, generate_stock_report
    except ImportError:
        print("错误: 请先安装 vibe_integration 模块")
        print("  cd /root/.openclaw/workspace/trading-system")
        print("  pip install akshare yfinance pandas numpy")
        sys.exit(1)

    import argparse
    p = argparse.ArgumentParser(description="股票回测 (A股/港股/美股)")
    p.add_argument("--codes", type=str, required=True,
                   help="逗号分隔代码: 600000.SH,000001.SZ 或 00700.HK 或 AAPL,TSLA")
    p.add_argument("--start", type=str, default="2024-01-01")
    p.add_argument("--end",   type=str, default="2025-01-01")
    p.add_argument("--strategy", type=str, default="ma_cross",
                   choices=["ma_cross", "rsi"])
    p.add_argument("--fast",  type=int, default=20)
    p.add_argument("--slow",  type=int, default=60)
    p.add_argument("--rsi-period",    type=int, default=14)
    p.add_argument("--rsi-oversold",   type=float, default=30.0)
    p.add_argument("--rsi-overbought", type=float, default=70.0)
    p.add_argument("--capital", type=float, default=1000000.0)
    p.add_argument("--engine", type=str, default="auto",
                   choices=["auto", "china_a", "global_equity"])
    p.add_argument("--output", default="backtest_results")
    args = p.parse_args()

    codes = [c.strip() for c in args.codes.split(",")]
    params = {"fast": args.fast, "slow": args.slow} if args.strategy == "ma_cross" else \
             {"period": args.rsi_period, "oversold": args.rsi_oversold, "overbought": args.rsi_overbought}

    result = run_stock_backtest(
        codes=codes, start_date=args.start, end_date=args.end,
        strategy=args.strategy, signal_params=params,
        initial_cash=args.capital, engine=args.engine,
    )

    if "error" in result:
        print(f"错误: {result['error']}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"  股票回测  {', '.join(codes)}")
    print("=" * 60)
    print(f"  策略       : {result.get('strategy')}")
    print(f"  数据区间   : {result.get('start_date')} ~ {result.get('end_date')}")
    print(f"  总收益率   : {result.get('total_return_pct', 0):+.2f}%")
    print(f"  夏普比率   : {result.get('sharpe_ratio', 0):.2f}")
    print(f"  最大回撤   : {result.get('max_drawdown_pct', 0):.2f}%")
    print(f"  交易次数   : {result.get('total_trades', 0)}")
    print(f"  胜率       : {result.get('win_rate_pct', 0):.2f}%")
    print(f"  总手续费   : ¥{result.get('total_commission', 0):.2f}")
    print("=" * 60)

    report_path = generate_stock_report(result, output_dir=args.output)
    print(f"\n📄 完整报告: {report_path}")


if __name__ == "__main__":
    # 根据参数判断是加密货币还是股票回测
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        main()
    elif len(sys.argv) > 1 and ("SH" in sys.argv[1] or "SZ" in sys.argv[1] or
                               "HK" in sys.argv[1] or sys.argv[1] in ("--codes",)):
        # 股票模式
        sys.argv[0] = sys.argv[0].replace("backtest.py", "stock_backtest.py")
        main_stock()
    else:
        main()
