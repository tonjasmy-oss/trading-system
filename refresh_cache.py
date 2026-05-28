"""
批量拉取 OHLCV 缓存：指定起始时间，分批拉取到最新
用法: python3 refresh_cache.py [--since 2024-05-01] [--symbols BTC ETH ZEC HYPE]
"""
import sys, os, logging, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from history_cache import init_cache_db, get_ohlcv, get_latest_timestamp, save_ohlcv
import crypto_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TIMEFRAME = "4h"
BATCH_LIMIT = 1000  # Gate.io 单次最大 1000

def parse_since(s: str) -> int:
    """解析日期字符串为毫秒时间戳"""
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def refresh_full(symbol: str, since_ms: int):
    """
    从 since_ms 分批拉取全部数据，写入缓存。
    自动处理：历史回填 + 增量更新。
    """
    init_cache_db()
    short = symbol.split("/")[0].upper()

    # 检查缓存范围
    cache_data = get_ohlcv(symbol, TIMEFRAME, limit=5000)
    # 过滤损坏的时间戳（1970年 = epoch 污染数据）
    MIN_VALID_TS = 1400000000000  # 2014-05-13 以后
    if cache_data:
        valid = [c for c in cache_data if c["timestamp"] > MIN_VALID_TS]
        if len(valid) < len(cache_data):
            logger.warning(f"{symbol} 缓存含 {len(cache_data)-len(valid)} 条损坏数据，已忽略")
        cache_data = valid
    if cache_data:
        cache_first = cache_data[0]["timestamp"]
        cache_last = cache_data[-1]["timestamp"]
        logger.info(f"{symbol} 缓存: {_ts_str(cache_first)} ~ {_ts_str(cache_last)} ({len(cache_data)}条)")
    else:
        cache_first = None
        cache_last = None
        logger.info(f"{symbol} 无有效缓存")

    # Phase 1: 回填历史（如果缓存最早的 > since_ms）
    if cache_first and cache_first > since_ms:
        logger.info(f"  → 回填历史 {_ts_str(since_ms)} ~ {_ts_str(cache_first)}")
        backfill_since = since_ms
        while True:
            candles = crypto_api.get_ohlcv(short, TIMEFRAME, since=backfill_since, limit=BATCH_LIMIT)
            if not candles:
                break
            # 只保存早于 cache_first 的数据
            new_candles = [c for c in candles if c["timestamp"] < cache_first]
            if new_candles:
                save_ohlcv(symbol, TIMEFRAME, new_candles)
                logger.info(f"    回填 {len(new_candles)} 条 → {_ts_str(new_candles[-1]['timestamp'])}")
            if len(candles) < BATCH_LIMIT or candles[-1]["timestamp"] >= cache_first:
                break
            backfill_since = candles[-1]["timestamp"] + 1
            time.sleep(0.3)

    # Phase 2: 拉取增量（从缓存最新到当前）
    fetch_since = (cache_last + 1) if cache_last else since_ms
    logger.info(f"  → 增量拉取 从 {_ts_str(fetch_since)}")

    total_new = 0
    while True:
        candles = crypto_api.get_ohlcv(short, TIMEFRAME, since=fetch_since, limit=BATCH_LIMIT)
        if not candles:
            break

        save_ohlcv(symbol, TIMEFRAME, candles)
        n = len(candles)
        total_new += n
        last_ts = candles[-1]["timestamp"]
        logger.info(f"  {symbol} 拉取 {n} 条 → {_ts_str(last_ts)}")

        if n < BATCH_LIMIT:
            # 已到最新
            break

        # 下一批从最后一条的下一个时间戳开始
        fetch_since = last_ts + 1
        time.sleep(0.3)  # 避免触发限流

    logger.info(f"{symbol} 完成，共新增 {total_new} 条")

def _ts_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

if __name__ == "__main__":
    since_str = "2024-05-01"
    symbols = ["BTC/USDT", "ETH/USDT", "ZEC/USDT", "HYPE/USDT"]

    # 解析参数
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--since" and i + 1 < len(args):
            since_str = args[i + 1]
            i += 2
        elif args[i] == "--symbols":
            symbols = []
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                s = args[i].upper()
                symbols.append(s if "/" in s else f"{s}/USDT")
                i += 1
        else:
            i += 1

    since_ms = parse_since(since_str)
    logger.info(f"起始时间: {since_str} (ts={since_ms})")
    logger.info(f"标的: {symbols}")
    logger.info("=" * 50)

    for s in symbols:
        refresh_full(s, since_ms)

    # 验证
    logger.info("\n===== 缓存验证 =====")
    for s in symbols:
        data = get_ohlcv(s, TIMEFRAME, limit=5000)
        if data:
            first = _ts_str(data[0]["timestamp"])
            last = _ts_str(data[-1]["timestamp"])
            logger.info(f"  {s}: {len(data)} 条, {first} ~ {last}")
        else:
            logger.warning(f"  {s}: 无数据")
