"""
多市场统一回测引擎
整合 Vibe-Trading 的 ChinaAEngine / GlobalEquityEngine + 数据加载器
支持 A股 / 港股 / 美股 + 加密货币

用法：
  python stock_backtest.py --codes 600000.SH,000001.SZ --start 20240101 --end 20250101
  python stock_backtest.py --codes 00700.HK,09988.HK --market hk --start 20240101 --end 20250101
  python stock_backtest.py --codes AAPL,TSLA --market us --start 20240101 --end 20250101
"""

from __future__ import annotations

import json
import math
import os
import sys
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ─── 数据加载器 ────────────────────────────────────────────

def _detect_market(code: str) -> str:
    """从代码格式推断市场类型"""
    u = code.upper()
    if u.endswith((".SZ", ".SH", ".BJ")):
        return "a_share"
    if u.endswith(".HK"):
        return "hk_equity"
    if u.endswith(".US"):
        return "us_equity"
    if "-USDT" in u or "/USDT" in u:
        return "crypto"
    # 默认A股
    return "a_share"


def _fetch_akshare_a_share(codes: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """通过 akshare 获取 A股 OHLCV"""
    import akshare as ak
    result = {}
    sd = start_date.replace("-", "").replace("/", "")
    ed = end_date.replace("-", "").replace("/", "")
    for code in codes:
        try:
            symbol = code.split(".")[0]
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=sd,
                end_date=ed,
                adjust="qfq",
            )
            if df is None or df.empty:
                continue
            df = df.rename(columns={
                "日期": "trade_date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date").sort_index()
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            result[code] = df[["open", "high", "low", "close", "volume"]].dropna()
        except Exception as e:
            logging.warning("akshare A股 failed for %s: %s", code, e)
    return result


def _fetch_akshare_hk(codes: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """通过 akshare 获取港股 OHLCV"""
    import akshare as ak
    result = {}
    sd = start_date.replace("-", "").replace("/", "")
    ed = end_date.replace("-", "").replace("/", "")
    for code in codes:
        try:
            symbol = code.replace(".HK", "").zfill(5)
            df = ak.stock_hk_hist(
                symbol=symbol,
                period="daily",
                start_date=sd,
                end_date=ed,
                adjust="qfq",
            )
            if df is None or df.empty:
                continue
            df = df.rename(columns={
                "日期": "trade_date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date").sort_index()
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            result[code] = df[["open", "high", "low", "close", "volume"]].dropna()
        except Exception as e:
            logging.warning("akshare HK failed for %s: %s", code, e)
    return result


def _fetch_yfinance_us(codes: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """通过 akshare stock_us_daily 获取美股 OHLCV
    yfinance 有频率限制，改用 akshare 作为主要数据源"""
    return _fetch_akshare_us(codes, start_date, end_date)


def _fetch_akshare_us(codes: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """通过 akshare stock_us_daily 获取美股 OHLCV（yfinance 降级方案）"""
    import akshare as ak
    result = {}
    sd = start_date.replace("-", "").replace("/", "")
    ed = end_date.replace("-", "").replace("/", "")
    for code in codes:
        try:
            symbol = code.replace(".US", "")
            df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
            if df is None or df.empty:
                continue
            df = df.rename(columns={
                "date": "trade_date", "open": "open", "high": "high",
                "low": "low", "close": "close", "volume": "volume",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date").sort_index()
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            # Filter by date range
            start_dt = pd.to_datetime(start_date[:10] if len(start_date) >= 10 else start_date)
            end_dt = pd.to_datetime(end_date[:10] if len(end_date) >= 10 else end_date)
            df = df[(df.index >= start_dt) & (df.index <= end_dt)]
            if not df.empty:
                result[code] = df[["open", "high", "low", "close", "volume"]].dropna()
            logging.debug("akshare US %s: %d rows", code, len(result.get(code, pd.DataFrame())))
        except Exception as e:
            logging.warning("akshare US failed for %s: %s", code, e)
    return result


def _get_cache_path() -> Path:
    cache_dir = Path(__file__).parent.parent / "ohlcv_cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir / "ohlcv_cache.db"


def _read_from_cache(
    codes: List[str], start_date: str, end_date: str
) -> Dict[str, pd.DataFrame]:
    """从 SQLite 缓存读取 K线数据，TTL=1天"""
    import sqlite3
    from datetime import datetime, timedelta

    sd = datetime.strptime(start_date[:10], "%Y-%m-%d")
    ed = datetime.strptime(end_date[:10], "%Y-%m-%d")
    ttl_cutoff = int((datetime.now() - timedelta(days=1)).timestamp())

    cache_path = _get_cache_path()
    if not cache_path.exists():
        return {}

    result = {}
    try:
        conn = sqlite3.connect(str(cache_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        for code in codes:
            cur.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM ohlcv_cache
                WHERE symbol=? AND timeframe='1d' AND timestamp>=? AND timestamp<=?
                AND created_at > ?
                ORDER BY timestamp ASC
                """,
                (code, int(sd.timestamp()), int(ed.timestamp()), ttl_cutoff),
            )
            rows = cur.fetchall()
            if rows:
                df = pd.DataFrame(rows)
                df["date"] = pd.to_datetime(df["timestamp"], unit="s")
                df = df.set_index("date").sort_index()
                result[code] = df[["open", "high", "low", "close", "volume"]]
        conn.close()
    except Exception as e:
        logging.debug("Cache read failed: %s", e)
    return result


def _write_to_cache(code: str, df: pd.DataFrame):
    """写入 K线数据到缓存"""
    import sqlite3

    if df is None or df.empty:
        return
    cache_path = _get_cache_path()
    try:
        conn = sqlite3.connect(str(cache_path))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ohlcv_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, timeframe TEXT DEFAULT '1d',
                timestamp INTEGER, open REAL, high REAL,
                low REAL, close REAL, volume REAL,
                created_at INTEGER DEFAULT (strftime('%s','now'))
            )
            """
        )
        for ts, row in df.iterrows():
            ts_sec = int(pd.Timestamp(ts).timestamp())
            cur.execute(
                """
                INSERT OR REPLACE INTO ohlcv_cache
                (symbol, timeframe, timestamp, open, high, low, close, volume, created_at)
                VALUES (?, '1d', ?, ?, ?, ?, ?, ?, strftime('%s','now'))
                """,
                (code, ts_sec, row["open"], row["high"], row["low"], row["close"], row["volume"]),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.debug("Cache write failed for %s: %s", code, e)


def fetch_stock_data(codes: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """统一数据获取入口，自动路由到正确数据源，带1天TTL缓存"""
    merged = {}

    # 1. 优先从缓存读取
    cached = _read_from_cache(codes, start_date, end_date)
    fetched_codes = set(cached.keys())
    merged.update(cached)

    # 2. 补充未命中缓存的标的（从网络获取并写入缓存）
    remaining = [c for c in codes if c not in fetched_codes]
    if not remaining:
        return merged

    by_market: Dict[str, List[str]] = {}
    for c in remaining:
        m = _detect_market(c)
        by_market.setdefault(m, []).append(c)

    # 转换日期格式: YYYY-MM-DD -> YYYYMMDD
    sd_fmt = start_date.replace("-", "").replace("/", "")
    ed_fmt = end_date.replace("-", "").replace("/", "")

    # 2a. 优先用 stock_data/stock_api.py 的统一接口
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from stock_data.stock_api import get_stock_ohlcv
        for code in remaining:
            try:
                df = get_stock_ohlcv(code, start_date=sd_fmt, end_date=ed_fmt)
                if df is not None and not df.empty:
                    merged[code] = df
                    _write_to_cache(code, df)
            except Exception as e:
                logging.debug("stock_api get_stock_ohlcv failed for %s: %s", code, e)
    except ImportError:
        pass

    # 2b. 回退方案：akshare/yfinance 直接调用
    fetched_codes = set(merged.keys())
    still_missing = [c for c in remaining if c not in fetched_codes]
    if still_missing:
        for_market: Dict[str, List[str]] = {}
        for c in still_missing:
            m = _detect_market(c)
            for_market.setdefault(m, []).append(c)
        if "a_share" in for_market:
            result = _fetch_akshare_a_share(for_market["a_share"], start_date, end_date)
            for code, df in result.items():
                merged[code] = df
                _write_to_cache(code, df)
        if "hk_equity" in for_market:
            result = _fetch_akshare_hk(for_market["hk_equity"], start_date, end_date)
            for code, df in result.items():
                merged[code] = df
                _write_to_cache(code, df)
        if "us_equity" in for_market:
            result = _fetch_yfinance_us(for_market["us_equity"], start_date, end_date)
            for code, df in result.items():
                merged[code] = df
                _write_to_cache(code, df)

    return merged


# ─── 市场引擎 ──────────────────────────────────────────────


@dataclass
class Position:
    symbol: str
    direction: int          # 1=long, -1=short
    entry_price: float
    entry_time: any
    size: float
    leverage: float = 1.0
    entry_bar_idx: int = 0
    entry_commission: float = 0.0


@dataclass
class TradeRecord:
    symbol: str
    direction: int
    entry_price: float
    exit_price: float
    entry_time: any
    exit_time: any
    size: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    holding_bars: int = 0
    commission: float = 0.0


# ── A股引擎 ──────────────────────────────────────────────

class ChinaAEngine:
    """A股回测引擎（T+1, 涨跌停, 佣金, 印花税, 转让费）

    费用规则（默认万一佣+5最低）:
      - 佣金: 0.025% (万一), 最低 5 元
      - 印花税: 0.05% (仅卖出)
      - 过户费: 0.001% (双向)
    """

    def __init__(self, config: dict):
        self.config = config
        self.initial_capital = config.get("initial_cash", 1000000.0)
        self.capital = self.initial_capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[TradeRecord] = []
        self.equity_snapshots: List[Tuple] = []  # (ts, equity)
        self._bar_idx = 0
        self._active_symbol = ""
        # A股费用参数
        self.commission_rate = config.get("commission_rate", 0.00025)
        self.commission_min = config.get("commission_min", 5.0)
        self.stamp_tax = config.get("stamp_tax", 0.0005)
        self.transfer_fee = config.get("transfer_fee", 0.00001)
        self.slippage_rate = config.get("slippage", 0.001)

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        """A股执行规则"""
        # 禁止做空
        if direction == -1:
            return False
        # T+1: 不能卖今天买的
        if direction == 0:
            pos = self.positions.get(symbol)
            if pos is not None:
                bar_date = _bar_date(bar)
                entry_date = pos.entry_time.date() if hasattr(pos.entry_time, "date") else None
                if bar_date is not None and entry_date is not None and bar_date == entry_date:
                    return False
        # 涨跌停检测
        pct_chg = _calc_pct_change(bar)
        if pct_chg is not None:
            limit = _a_share_price_limit(symbol)
            if direction == 1 and pct_chg >= limit - 0.001:
                return False   # 涨停不能买
            if direction == 0 and pct_chg <= -limit + 0.001:
                return False   # 跌停不能卖
        return True

    def round_size(self, raw_size: float, price: float) -> float:
        """向下取整到100股"""
        return max(int(raw_size / 100) * 100, 0)

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        """A股手续费: 佣金 + 过户费(双向) + 印花税(仅卖)"""
        notional = size * price
        comm = max(notional * self.commission_rate, self.commission_min)
        comm += notional * self.transfer_fee  # 过户费双向
        if not is_open:
            comm += notional * self.stamp_tax   # 印花税仅卖
        return comm

    def apply_slippage(self, price: float, direction: int) -> float:
        return price * (1 + direction * self.slippage_rate)

    def on_bar(self, symbol: str, bar: pd.Series, ts: any) -> None:
        pass

    def _calc_pnl(self, symbol: str, direction: int, size: float,
                  entry_price: float, exit_price: float) -> float:
        return direction * size * (exit_price - entry_price)

    def _calc_margin(self, symbol: str, size: float, price: float, leverage: float) -> float:
        return size * price / leverage

    def _safe_price(self, close_df: pd.DataFrame, ts: any, symbol: str, fallback: float) -> float:
        if ts in close_df.index and symbol in close_df.columns:
            val = close_df.at[ts, symbol]
            if pd.notna(val):
                return float(val)
        return fallback

    def _calc_equity(self, close_df: pd.DataFrame, ts: any) -> float:
        equity = self.capital
        for sym, pos in self.positions.items():
            cp = self._safe_price(close_df, ts, sym, pos.entry_price)
            margin = self._calc_margin(sym, pos.size, pos.entry_price, pos.leverage)
            unreal = self._calc_pnl(sym, pos.direction, pos.size, pos.entry_price, cp)
            equity += margin + unreal
        return equity

    def run(
        self,
        data_map: Dict[str, pd.DataFrame],
        signal_map: Dict[str, pd.Series],
        codes: List[str],
        start_date: str,
        end_date: str,
    ) -> dict:
        """执行回测主循环"""
        # 对齐日期索引
        all_dates: set = set()
        for c in codes:
            if c in data_map:
                all_dates.update(data_map[c].index)
        dates = pd.DatetimeIndex(sorted(all_dates))
        dates.name = "trade_date"

        # Close 矩阵
        close_df = pd.DataFrame(index=dates, columns=codes, dtype=float)
        for c in codes:
            if c in data_map:
                close_df[c] = data_map[c]["close"].reindex(dates)
        close_df = close_df.ffill(limit=5)

        # 目标持仓
        pos_df = pd.DataFrame(0.0, index=dates, columns=codes)
        for c in codes:
            if c in signal_map:
                own_dates = data_map[c].index
                raw = signal_map[c].reindex(own_dates).fillna(0.0).clip(-1.0, 1.0)
                shifted = raw.shift(1).fillna(0.0)
                pos_df[c] = shifted.reindex(dates).ffill(limit=5).fillna(0.0)

        ret_df = close_df.pct_change().fillna(0.0)

        # Bar循环
        for i, ts in enumerate(dates):
            self._bar_idx = i
            equity = self._calc_equity(close_df, ts)

            for c in codes:
                if c not in data_map or ts not in data_map[c].index:
                    continue
                self.on_bar(c, data_map[c].loc[ts], ts)
                target_w = float(pos_df.at[ts, c]) if ts in pos_df.index else 0.0
                self._rebalance(c, target_w, data_map.get(c), ts, equity, close_df)

            # 权益快照
            snap_equity = self._calc_equity(close_df, ts)
            self.equity_snapshots.append((ts, snap_equity))

        # 强制平仓
        if len(dates) > 0:
            last_ts = dates[-1]
            for c in list(self.positions.keys()):
                price = self._safe_price(close_df, last_ts, c, self.positions[c].entry_price)
                self._close_position(c, price, last_ts, "end_of_backtest", close_df)

        return self._build_metrics(dates)

    def _rebalance(
        self, symbol: str, target_weight: float,
        df: Optional[pd.DataFrame], ts: any,
        equity: float, close_df: pd.DataFrame,
    ):
        self._active_symbol = symbol
        target_dir = 1 if target_weight > 1e-9 else (-1 if target_weight < -1e-9 else 0)
        current_pos = self.positions.get(symbol)

        if current_pos is None and target_dir == 0:
            return
        if df is None or ts not in df.index:
            return

        bar = df.loc[ts]

        # 平仓
        if current_pos is not None:
            need_close = target_dir == 0 or target_dir != current_pos.direction
            if need_close:
                if self.can_execute(symbol, 0, bar):
                    open_price = float(bar.get("open", bar.get("close", 0)))
                    price = self.apply_slippage(open_price, -current_pos.direction)
                    self._close_position(symbol, price, ts, "signal", close_df)
                else:
                    return

        # 开仓
        if target_dir != 0 and symbol not in self.positions:
            if not self.can_execute(symbol, target_dir, bar):
                return
            open_price = float(bar.get("open", bar.get("close", 0)))
            if open_price <= 0:
                return
            slipped = self.apply_slippage(open_price, target_dir)
            target_notional = abs(target_weight) * equity
            raw_size = target_notional / slipped
            size = self.round_size(raw_size, slipped)
            if size <= 0:
                return
            margin = self._calc_margin(symbol, size, slipped, 1.0)
            comm = self.calc_commission(size, slipped, target_dir, is_open=True)
            if margin + comm > self.capital:
                available = self.capital - comm
                if available <= 0:
                    return
                size = self.round_size(available / slipped, slipped)
                if size <= 0:
                    return
                margin = self._calc_margin(symbol, size, slipped, 1.0)
                comm = self.calc_commission(size, slipped, target_dir, is_open=True)
            self.capital -= (margin + comm)
            self.positions[symbol] = Position(
                symbol=symbol,
                direction=target_dir,
                entry_price=slipped,
                entry_time=ts,
                size=size,
                leverage=1.0,
                entry_bar_idx=self._bar_idx,
                entry_commission=comm,
            )

    def _close_position(self, symbol: str, exit_price: float,
                        exit_time: any, reason: str, close_df: pd.DataFrame):
        self._active_symbol = symbol
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return
        pnl = self._calc_pnl(symbol, pos.direction, pos.size, pos.entry_price, exit_price)
        margin = self._calc_margin(symbol, pos.size, pos.entry_price, pos.leverage)
        pnl_pct = pnl / margin * 100 if margin > 1e-9 else 0.0
        exit_comm = self.calc_commission(pos.size, exit_price, pos.direction, is_open=False)
        self.capital += margin + pnl - exit_comm
        holding_bars = max(self._bar_idx - pos.entry_bar_idx, 0)
        self.trades.append(TradeRecord(
            symbol=symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            size=pos.size,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            holding_bars=holding_bars,
            commission=pos.entry_commission + exit_comm,
        ))

    def _build_metrics(self, dates: pd.DatetimeIndex) -> dict:
        equity_series = pd.Series(
            [e for _, e in self.equity_snapshots],
            index=pd.DatetimeIndex([ts for ts, _ in self.equity_snapshots]),
        )
        if len(equity_series) < 2:
            return {"error": "数据不足"}

        total_return = (equity_series.iloc[-1] - self.initial_capital) / self.initial_capital * 100

        # 最大回撤
        peak = equity_series.cummax()
        dd = (equity_series - peak) / peak * 100
        max_dd = dd.min() if len(dd) > 0 else 0.0

        # 夏普比率
        rets = equity_series.pct_change().dropna()
        if len(rets) > 1 and rets.std() > 0:
            sharpe = rets.mean() / rets.std() * math.sqrt(252)
        else:
            sharpe = 0.0

        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        total_trades = len(self.trades)

        return {
            "strategy": "ChinaAEngine",
            "initial_capital": self.initial_capital,
            "final_equity": float(self.capital),
            "total_return_pct": round(total_return, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(abs(max_dd), 2),
            "total_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": round(len(wins) / total_trades * 100, 2) if total_trades > 0 else 0.0,
            "total_commission": round(sum(t.commission for t in self.trades), 2),
            "equity_curve": [(str(ts.date()) if hasattr(ts, "date") else str(ts), round(eq, 2))
                             for ts, eq in self.equity_snapshots],
            "trades": [
                {
                    "symbol": t.symbol,
                    "entry": str(t.entry_time.date()) if hasattr(t.entry_time, "date") else str(t.entry_time),
                    "exit": str(t.exit_time.date()) if hasattr(t.exit_time, "date") else str(t.exit_time),
                    "direction": "long" if t.direction == 1 else "short",
                    "entry_price": round(t.entry_price, 4),
                    "exit_price": round(t.exit_price, 4),
                    "size": round(t.size, 2),
                    "pnl": round(t.pnl, 2),
                    "pnl_pct": round(t.pnl_pct, 2),
                    "reason": t.exit_reason,
                    "holding_bars": t.holding_bars,
                    "commission": round(t.commission, 2),
                }
                for t in self.trades
            ],
        }


# ── 港股/美股引擎 ────────────────────────────────────────

class GlobalEquityEngine:
    """港股/美股回测引擎

    美股: T+0, 零佣金, 分数股(0.01), 低滑点
    港股: T+0, 印花税0.1%(双向), 整数手(100股)
    """

    def __init__(self, config: dict, market: str = "us"):
        self.config = config
        self.market = market   # "us" or "hk"
        self.initial_capital = config.get("initial_cash", 1000000.0)
        self.capital = self.initial_capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[TradeRecord] = []
        self.equity_snapshots: List[Tuple] = []
        self._bar_idx = 0
        self._active_symbol = ""
        # 费用参数
        self.slippage_us = config.get("slippage_us", 0.0005)
        self.slippage_hk = config.get("slippage_hk", 0.001)
        self.hk_stamp_tax = config.get("hk_stamp_tax", 0.001)
        self.hk_commission = config.get("hk_commission", 0.00015)
        self.hk_levy = config.get("hk_levy", 0.0000565)
        self.hk_settlement = config.get("hk_settlement", 0.00002)

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        return True  # T+0, 做空/做多均可

    def round_size(self, raw_size: float, price: float) -> float:
        if self.market == "hk":
            return max(int(raw_size / 100) * 100, 0)
        return round(max(raw_size, 0.0), 2)  # 美股支持分数

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        if self.market == "hk":
            notional = size * price
            comm = notional * self.hk_commission
            comm += notional * self.hk_stamp_tax  # 双向印花税
            comm += notional * self.hk_levy
            comm += notional * self.hk_settlement
            return comm
        return 0.0  # 美股零佣金

    def apply_slippage(self, price: float, direction: int) -> float:
        rate = self.slippage_hk if self.market == "hk" else self.slippage_us
        return price * (1 + direction * rate)

    def on_bar(self, symbol: str, bar: pd.Series, ts: any) -> None:
        pass

    def _calc_pnl(self, symbol: str, direction: int, size: float,
                  entry_price: float, exit_price: float) -> float:
        return direction * size * (exit_price - entry_price)

    def _calc_margin(self, symbol: str, size: float, price: float, leverage: float) -> float:
        return size * price / leverage

    def _safe_price(self, close_df: pd.DataFrame, ts: any, symbol: str, fallback: float) -> float:
        if ts in close_df.index and symbol in close_df.columns:
            val = close_df.at[ts, symbol]
            if pd.notna(val):
                return float(val)
        return fallback

    def _calc_equity(self, close_df: pd.DataFrame, ts: any) -> float:
        equity = self.capital
        for sym, pos in self.positions.items():
            cp = self._safe_price(close_df, ts, sym, pos.entry_price)
            margin = self._calc_margin(sym, pos.size, pos.entry_price, pos.leverage)
            unreal = self._calc_pnl(sym, pos.direction, pos.size, pos.entry_price, cp)
            equity += margin + unreal
        return equity

    def run(
        self,
        data_map: Dict[str, pd.DataFrame],
        signal_map: Dict[str, pd.Series],
        codes: List[str],
        start_date: str,
        end_date: str,
    ) -> dict:
        """执行回测主循环（与 ChinaAEngine 相同的接口）"""
        all_dates: set = set()
        for c in codes:
            if c in data_map:
                all_dates.update(data_map[c].index)
        dates = pd.DatetimeIndex(sorted(all_dates))
        dates.name = "trade_date"

        close_df = pd.DataFrame(index=dates, columns=codes, dtype=float)
        for c in codes:
            if c in data_map:
                close_df[c] = data_map[c]["close"].reindex(dates)
        close_df = close_df.ffill(limit=5)

        pos_df = pd.DataFrame(0.0, index=dates, columns=codes)
        for c in codes:
            if c in signal_map:
                own_dates = data_map[c].index
                raw = signal_map[c].reindex(own_dates).fillna(0.0).clip(-1.0, 1.0)
                shifted = raw.shift(1).fillna(0.0)
                pos_df[c] = shifted.reindex(dates).ffill(limit=5).fillna(0.0)

        for i, ts in enumerate(dates):
            self._bar_idx = i
            equity = self._calc_equity(close_df, ts)
            for c in codes:
                if c not in data_map or ts not in data_map[c].index:
                    continue
                self.on_bar(c, data_map[c].loc[ts], ts)
                target_w = float(pos_df.at[ts, c]) if ts in pos_df.index else 0.0
                self._rebalance(c, target_w, data_map.get(c), ts, equity, close_df)
            snap_equity = self._calc_equity(close_df, ts)
            self.equity_snapshots.append((ts, snap_equity))

        if len(dates) > 0:
            last_ts = dates[-1]
            for c in list(self.positions.keys()):
                price = self._safe_price(close_df, last_ts, c, self.positions[c].entry_price)
                self._close_position(c, price, last_ts, "end_of_backtest", close_df)

        return self._build_metrics(dates)

    def _rebalance(
        self, symbol: str, target_weight: float,
        df: Optional[pd.DataFrame], ts: any,
        equity: float, close_df: pd.DataFrame,
    ):
        self._active_symbol = symbol
        target_dir = 1 if target_weight > 1e-9 else (-1 if target_weight < -1e-9 else 0)
        current_pos = self.positions.get(symbol)

        if current_pos is None and target_dir == 0:
            return
        if df is None or ts not in df.index:
            return

        bar = df.loc[ts]

        if current_pos is not None:
            need_close = target_dir == 0 or target_dir != current_pos.direction
            if need_close:
                if self.can_execute(symbol, 0, bar):
                    open_price = float(bar.get("open", bar.get("close", 0)))
                    price = self.apply_slippage(open_price, -current_pos.direction)
                    self._close_position(symbol, price, ts, "signal", close_df)
                else:
                    return

        if target_dir != 0 and symbol not in self.positions:
            if not self.can_execute(symbol, target_dir, bar):
                return
            open_price = float(bar.get("open", bar.get("close", 0)))
            if open_price <= 0:
                return
            slipped = self.apply_slippage(open_price, target_dir)
            target_notional = abs(target_weight) * equity
            raw_size = target_notional / slipped
            size = self.round_size(raw_size, slipped)
            if size <= 0:
                return
            margin = self._calc_margin(symbol, size, slipped, 1.0)
            comm = self.calc_commission(size, slipped, target_dir, is_open=True)
            if margin + comm > self.capital:
                available = self.capital - comm
                if available <= 0:
                    return
                size = self.round_size(available / slipped, slipped)
                if size <= 0:
                    return
                margin = self._calc_margin(symbol, size, slipped, 1.0)
                comm = self.calc_commission(size, slipped, target_dir, is_open=True)
            self.capital -= (margin + comm)
            self.positions[symbol] = Position(
                symbol=symbol,
                direction=target_dir,
                entry_price=slipped,
                entry_time=ts,
                size=size,
                leverage=1.0,
                entry_bar_idx=self._bar_idx,
                entry_commission=comm,
            )

    def _close_position(self, symbol: str, exit_price: float,
                        exit_time: any, reason: str, close_df: pd.DataFrame):
        self._active_symbol = symbol
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return
        pnl = self._calc_pnl(symbol, pos.direction, pos.size, pos.entry_price, exit_price)
        margin = self._calc_margin(symbol, pos.size, pos.entry_price, pos.leverage)
        pnl_pct = pnl / margin * 100 if margin > 1e-9 else 0.0
        exit_comm = self.calc_commission(pos.size, exit_price, pos.direction, is_open=False)
        self.capital += margin + pnl - exit_comm
        holding_bars = max(self._bar_idx - pos.entry_bar_idx, 0)
        self.trades.append(TradeRecord(
            symbol=symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            size=pos.size,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            holding_bars=holding_bars,
            commission=pos.entry_commission + exit_comm,
        ))

    def _build_metrics(self, dates: pd.DatetimeIndex) -> dict:
        equity_series = pd.Series(
            [e for _, e in self.equity_snapshots],
            index=pd.DatetimeIndex([ts for ts, _ in self.equity_snapshots]),
        )
        if len(equity_series) < 2:
            return {"error": "数据不足"}

        total_return = (equity_series.iloc[-1] - self.initial_capital) / self.initial_capital * 100
        peak = equity_series.cummax()
        dd = (equity_series - peak) / peak * 100
        max_dd = dd.min() if len(dd) > 0 else 0.0
        rets = equity_series.pct_change().dropna()
        sharpe = (rets.mean() / rets.std() * math.sqrt(252)) if len(rets) > 1 and rets.std() > 0 else 0.0
        wins = [t for t in self.trades if t.pnl > 0]
        total_trades = len(self.trades)

        return {
            "strategy": f"GlobalEquityEngine({self.market.upper()})",
            "initial_capital": self.initial_capital,
            "final_equity": float(self.capital),
            "total_return_pct": round(total_return, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(abs(max_dd), 2),
            "total_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(self.trades) - len(wins),
            "win_rate_pct": round(len(wins) / total_trades * 100, 2) if total_trades > 0 else 0.0,
            "total_commission": round(sum(t.commission for t in self.trades), 2),
            "equity_curve": [(str(ts.date()) if hasattr(ts, "date") else str(ts), round(eq, 2))
                             for ts, eq in self.equity_snapshots],
            "trades": [
                {
                    "symbol": t.symbol,
                    "entry": str(t.entry_time.date()) if hasattr(t.entry_time, "date") else str(t.entry_time),
                    "exit": str(t.exit_time.date()) if hasattr(t.exit_time, "date") else str(t.exit_time),
                    "direction": "long" if t.direction == 1 else "short",
                    "entry_price": round(t.entry_price, 4),
                    "exit_price": round(t.exit_price, 4),
                    "size": round(t.size, 2),
                    "pnl": round(t.pnl, 2),
                    "pnl_pct": round(t.pnl_pct, 2),
                    "reason": t.exit_reason,
                    "holding_bars": t.holding_bars,
                    "commission": round(t.commission, 2),
                }
                for t in self.trades
            ],
        }


# ─── 辅助函数 ────────────────────────────────────────────

def _bar_date(bar: pd.Series):
    for col in ("trade_date", "date"):
        if col in bar.index:
            val = bar[col]
            if hasattr(val, "date"):
                return val.date()
            try:
                return pd.Timestamp(val).date()
            except Exception:
                pass
    if hasattr(bar, "name") and hasattr(bar.name, "date"):
        return bar.name.date()
    return None


def _calc_pct_change(bar: pd.Series) -> Optional[float]:
    if "pct_chg" in bar.index:
        val = bar["pct_chg"]
        if pd.notna(val):
            return float(val) / 100.0
    close = bar.get("close")
    pre_close = bar.get("pre_close")
    if close is not None and pre_close is not None and pre_close > 0:
        return (float(close) - float(pre_close)) / float(pre_close)
    return None


def _a_share_price_limit(symbol: str) -> float:
    """A股涨跌停幅度"""
    code = symbol.split(".")[0] if "." in symbol else symbol
    if code.startswith("300") or code.startswith("688"):
        return 0.20   # 科创板/创业板 ±20%
    if code.startswith("8") and len(code) == 6:
        return 0.30   # 北交所 ±30%
    return 0.10      # 主板 ±10%


# ─── 内置信号生成器 ────────────────────────────────────

class SimpleMASignal:
    """简单均线交叉信号（可替换为 FormulaStrategy）"""

    def __init__(self, fast: int = 20, slow: int = 60):
        self.fast = fast
        self.slow = slow

    def generate(self, data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
        result = {}
        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            ma_fast = df["close"].rolling(self.fast).mean()
            ma_slow = df["close"].rolling(self.slow).mean()
            signal = pd.Series(0, index=df.index)
            prev_fast = ma_fast.shift(1)
            prev_slow = ma_slow.shift(1)
            # 金叉买入，死叉卖出
            buy = (prev_fast <= prev_slow) & (ma_fast > ma_slow)
            sell = (prev_fast >= prev_slow) & (ma_fast < ma_slow)
            signal[buy] = 1
            signal[sell] = -1
            result[code] = signal
        return result


class RSISignal:
    """RSI 信号"""

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate(self, data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
        result = {}
        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            delta = df["close"].diff()
            gain = delta.clip(lower=0).rolling(self.period).mean()
            loss = (-delta.clip(upper=0)).rolling(self.period).mean()
            rs = gain / loss.replace(0, 1e-10)
            rsi = 100 - (100 / (1 + rs))
            signal = pd.Series(0, index=df.index)
            signal[rsi < self.oversold] = 1
            signal[rsi > self.overbought] = -1
            result[code] = signal
        return result


# ─── 统一回测入口 ─────────────────────────────────────

def run_stock_backtest(
    codes: List[str],
    start_date: str,
    end_date: str,
    strategy: str = "ma_cross",
    signal_params: dict = None,
    initial_cash: float = 1000000.0,
    engine: str = "auto",
) -> dict:
    """多市场统一回测入口

    Args:
        codes: 股票代码列表，如 ["600000.SH", "000001.SZ"]
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        strategy: 信号策略 "ma_cross" | "rsi"
        signal_params: 信号参数
        initial_cash: 初始资金
        engine: 引擎选择 "auto" | "china_a" | "global_equity"
    Returns:
        回测结果字典
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger(__name__)

    # 1. 加载数据
    logger.info(f"加载数据: {codes} ({start_date} ~ {end_date})")
    data_map = fetch_stock_data(codes, start_date, end_date)
    if not data_map:
        return {"error": "数据获取失败，请检查网络和代码格式"}

    valid_codes = [c for c in codes if c in data_map]
    if not valid_codes:
        return {"error": "无有效数据"}
    logger.info(f"成功加载 {len(valid_codes)} 个标的: {valid_codes}")

    # 2. 生成信号
    params = signal_params or {}
    if strategy == "rsi":
        sig_gen = RSISignal(
            period=params.get("period", 14),
            oversold=params.get("oversold", 30.0),
            overbought=params.get("overbought", 70.0),
        )
    else:
        sig_gen = SimpleMASignal(
            fast=params.get("fast", 20),
            slow=params.get("slow", 60),
        )
    signal_map = sig_gen.generate(data_map)

    # 3. 选择引擎
    if engine == "auto":
        first_code = valid_codes[0]
        market = _detect_market(first_code)
        if market == "a_share":
            engine = "china_a"
            market_type = None
        else:
            engine = "global_equity"
            market_type = "hk" if market == "hk_equity" else "us"
    else:
        market_type = "hk" if engine == "global_equity" and _detect_market(valid_codes[0]) == "hk_equity" else "us"

    config = {"initial_cash": initial_cash}

    if engine == "china_a":
        logger.info("使用 ChinaAEngine (A股 T+1)")
        be = ChinaAEngine(config)
    else:
        logger.info(f"使用 GlobalEquityEngine ({market_type.upper()})")
        be = GlobalEquityEngine(config, market=market_type)

    # 4. 执行回测
    result = be.run(data_map, signal_map, valid_codes, start_date, end_date)
    result["codes"] = valid_codes
    result["start_date"] = start_date
    result["end_date"] = end_date
    result["strategy"] = strategy
    result["signal_params"] = params

    logger.info(f"回测完成: 收益率 {result.get('total_return_pct', 0):+.2f}%, "
                f"夏普 {result.get('sharpe_ratio', 0):.2f}, "
                f"最大回撤 {result.get('max_drawdown_pct', 0):.2f}%, "
                f"交易次数 {result.get('total_trades', 0)}")

    return result


def generate_stock_report(result: dict, output_dir: str = "backtest_results") -> str:
    """生成 Markdown 回测报告"""
    os.makedirs(output_dir, exist_ok=True)
    safe_name = "_".join(result.get("codes", ["unknown"]))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(output_dir, f"stock_report_{safe_name}_{ts}.md")

    trades_lines = []
    for i, t in enumerate(result.get("trades", []), 1):
        pnl_emoji = "🟢" if t["pnl"] > 0 else "🔴"
        trades_lines.append(
            f"| {i} | {t['entry']} | {t['exit']} | "
            f"{t['entry_price']:.4f} | {t['exit_price']:.4f} | "
            f"{t['size']:.2f} | {t['pnl_pct']:+.2f}% | "
            f"{t['reason']} |"
        )

    trades_table = "\n".join(trades_lines) if trades_lines else "| — | 无成交 |"

    codes_str = ", ".join(result.get("codes", []))
    equity_curve = result.get("equity_curve", [])
    eq_preview = ""
    if equity_curve:
        eq_preview = "\n".join(
            f"- {d}: ¥{v:,.2f}" for d, v in equity_curve[-5:]
        )

    md = f"""# 股票回测报告

## 基本信息

| 项目 | 值 |
|------|-----|
| **标的** | {codes_str} |
| **策略** | {result.get('strategy', 'N/A')} |
| **数据区间** | {result.get('start_date', 'N/A')} ~ {result.get('end_date', 'N/A')} |
| **初始资金** | ¥{result.get('initial_capital', 0):,.2f} |
| **最终权益** | ¥{result.get('final_equity', 0):,.2f} |
| **引擎** | {result.get('strategy', 'N/A').split('(')[0] if result.get('strategy') else 'N/A'} |

---

## 核心绩效指标

| 指标 | 值 |
|------|-----|
| **总收益率** | {result.get('total_return_pct', 0):+.2f}% |
| **夏普比率** | {result.get('sharpe_ratio', 0):.2f} |
| **最大回撤** | {result.get('max_drawdown_pct', 0):.2f}% |
| **总交易次数** | {result.get('total_trades', 0)} |
| **盈利次数** | {result.get('winning_trades', 0)} |
| **亏损次数** | {result.get('losing_trades', 0)} |
| **胜率** | {result.get('win_rate_pct', 0):.2f}% |
| **总手续费** | ¥{result.get('total_commission', 0):.2f} |

---

## 交易明细

| # | 入场日 | 出场日 | 入场价 | 出场价 | 数量 | 盈亏% | 出场原因 |
|---|--------|--------|--------|--------|------|-------|---------|
{trades_table}

---

## 最近权益快照

{eq_preview}

---

*报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} UTC*
"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(md)
    return filename


# ─── CLI 入口 ──────────────────────────────────────────

def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="多市场股票回测 (A股/港股/美股)")
    p.add_argument("--codes", type=str, required=True,
                   help="逗号分隔的股票代码，如 600000.SH,000001.SZ 或 00700.HK 或 AAPL,TSLA")
    p.add_argument("--start", type=str, default="2024-01-01", help="开始日期 YYYY-MM-DD")
    p.add_argument("--end",   type=str, default="2025-01-01", help="结束日期 YYYY-MM-DD")
    p.add_argument("--strategy", type=str, default="ma_cross",
                   choices=["ma_cross", "rsi"], help="信号策略")
    p.add_argument("--fast",  type=int, default=20,  help="均线快线周期 (ma_cross)")
    p.add_argument("--slow",  type=int, default=60,  help="均线慢线周期 (ma_cross)")
    p.add_argument("--rsi-period",    type=int, default=14,   help="RSI 周期 (rsi)")
    p.add_argument("--rsi-oversold",   type=float, default=30.0, help="RSI 超卖阈值")
    p.add_argument("--rsi-overbought", type=float, default=70.0, help="RSI 超买阈值")
    p.add_argument("--capital", type=float, default=1000000.0, help="初始资金")
    p.add_argument("--engine", type=str, default="auto",
                   choices=["auto", "china_a", "global_equity"], help="回测引擎")
    p.add_argument("--output", type=str, default="backtest_results", help="报告输出目录")
    return p.parse_args()


def main():
    args = _parse_args()
    codes = [c.strip() for c in args.codes.split(",")]

    params = {}
    if args.strategy == "rsi":
        params = {"period": args.rsi_period, "oversold": args.rsi_oversold, "overbought": args.rsi_overbought}
    else:
        params = {"fast": args.fast, "slow": args.slow}

    result = run_stock_backtest(
        codes=codes,
        start_date=args.start,
        end_date=args.end,
        strategy=args.strategy,
        signal_params=params,
        initial_cash=args.capital,
        engine=args.engine,
    )

    if "error" in result:
        print(f"错误: {result['error']}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"  股票回测结果  {', '.join(codes)}")
    print("=" * 60)
    print(f"  策略          : {result.get('strategy')}")
    print(f"  数据区间      : {result.get('start_date')} ~ {result.get('end_date')}")
    print(f"  总收益率      : {result.get('total_return_pct', 0):+.2f}%")
    print(f"  夏普比率      : {result.get('sharpe_ratio', 0):.2f}")
    print(f"  最大回撤      : {result.get('max_drawdown_pct', 0):.2f}%")
    print(f"  总交易次数    : {result.get('total_trades', 0)}")
    print(f"  胜率          : {result.get('win_rate_pct', 0):.2f}%")
    print(f"  总手续费      : ¥{result.get('total_commission', 0):.2f}")
    print("=" * 60)

    report_path = generate_stock_report(result, output_dir=args.output)
    print(f"\n📄 完整报告: {report_path}")

    return result


if __name__ == "__main__":
    main()
