"""按标的找出最优 Swarm 预设"""
import json, glob

files = sorted(glob.glob("backtest_results/swarm/swarm_full_*.json"))
with open(files[-1]) as f:
    data = json.load(f)

symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT", "XAUT/USDT"]

for sym in symbols:
    candidates = [d for d in data if d["symbol"] == sym and d["total_trades"] >= 5]
    if not candidates:
        print(f"\n{sym}: 无有效候选")
        continue
    
    # 按 sharpe 排序
    top = sorted(candidates, key=lambda d: -d["sharpe_ratio"])[:5]
    print(f"\n=== {sym} (最佳 5 个预设) ===")
    for d in top:
        print(f"  {d['preset']:>35s}: ret={d['total_return_pct']:+7.2f}%  "
              f"sharpe={d['sharpe_ratio']:+6.2f}  dd={d['max_drawdown_pct']:+6.2f}%  "
              f"trades={d['total_trades']:>3d}  wr={d['win_rate_pct']:>5.1f}%")
