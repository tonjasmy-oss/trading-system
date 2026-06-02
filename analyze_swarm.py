"""解析最新 swarm 回测结果 JSON"""
import json, os, glob
from collections import defaultdict

RESULT_DIR = "backtest_results/swarm"
files = sorted(glob.glob(f"{RESULT_DIR}/swarm_full_*.json"))
if not files:
    print("未找到结果文件")
    exit(1)

latest = files[-1]
print(f"读取: {latest}\n")

with open(latest) as f:
    data = json.load(f)

active = [d for d in data if d["total_trades"] >= 3]
zero = [d for d in data if d["total_trades"] == 0]

print(f"总结果: {len(data)}")
print(f"有效(>=3trades): {len(active)}")
print(f"零交易: {len(zero)}")

presets_active = set(d["preset"] for d in active)
presets_zero = set(d["preset"] for d in zero) - presets_active
print(f"有效预设: {len(presets_active)}")
print(f"零交易预设: {len(presets_zero)}")

if presets_zero:
    print("\n=== 零交易预设 ===")
    for p in sorted(presets_zero):
        print(f"  {p}")

print("\n=== 有效预设 (按夏普最高排名) ===")
best = {}
for d in active:
    key = d["preset"]
    if key not in best or d["sharpe_ratio"] > best[key]["sharpe_ratio"]:
        best[key] = d

for pn, d in sorted(best.items(), key=lambda x: -x[1]["sharpe_ratio"]):
    print(f'{pn:>35s}: {d["symbol"]:<10s} {d["timeframe"]}  '
          f'ret={d["total_return_pct"]:+7.2f}%  '
          f'sharpe={d["sharpe_ratio"]:+6.2f}  '
          f'dd={d["max_drawdown_pct"]:+6.2f}%  '
          f'trades={d["total_trades"]:>3d}  '
          f'wr={d["win_rate_pct"]:>5.1f}%')
