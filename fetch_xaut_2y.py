#!/usr/bin/env python3
"""
拉取 XAUT/USDT 近2年 4h K线数据（Gate.io）
XAUT = Tether Gold，1:1追踪黄金价格
"""
import requests, json, time

pair = "XAUT_USDT"
interval = "4h"
# 约2年数据
end_ts = int(time.time())
start_ts = end_ts - (365 * 2 * 24 * 3600)

all_data = []
current_from = start_ts
batch_num = 0

print(f"目标: 2年 4h K线 (XAUT/USDT)")
while current_from < end_ts:
    batch_num += 1
    params = {"currency_pair": pair, "interval": interval, "limit": 1000, "from": current_from}
    r = requests.get("https://api.gateio.ws/api/v4/spot/candlesticks", params=params, timeout=15)
    raw = r.json()
    if not isinstance(raw, list) or len(raw) == 0:
        print(f"第{batch_num}批: 空，停止")
        break
    for item in raw:
        if not isinstance(item, list) or len(item) < 6:
            continue
        try:
            all_data.append({
                "timestamp": int(item[0]) * 1000,
                "open": float(item[5]),
                "high": float(item[3]),
                "low": float(item[4]),
                "close": float(item[2]),
                "volume": float(item[1]),
            })
        except (ValueError, IndexError):
            continue
    last_ts = int(raw[-1][0])
    first_dt = time.strftime("%Y-%m-%d", time.localtime(int(raw[-1][0])))
    last_dt = time.strftime("%Y-%m-%d", time.localtime(int(raw[0][0])))
    print(f"第{batch_num}批: {first_dt}~{last_dt} ({len(raw)}条)，累计 {len(all_data)} 条")
    if len(raw) < 1000:
        print("到头")
        break
    current_from = last_ts + 14400  # 4h

# 去重合并
seen = {}
for d in all_data:
    seen[d["timestamp"]] = d
merged = sorted(seen.values(), key=lambda x: x["timestamp"])

first_ts = merged[0]["timestamp"] // 1000
last_ts = merged[-1]["timestamp"] // 1000
print(f"\n合计: {len(merged)} 条 4h K线")
print(f"范围: {time.strftime('%Y-%m-%d', time.localtime(first_ts))} ~ {time.strftime('%Y-%m-%d', time.localtime(last_ts))}")
print(f"起始 ${merged[0]['close']:.2f} → 最新 ${merged[-1]['close']:.2f} (变化 {(merged[-1]['close']/merged[0]['close']-1)*100:.1f}%)")

with open("xaut_2y_4h.json", "w") as f:
    json.dump(merged, f)
print("已保存: xaut_2y_4h.json")
