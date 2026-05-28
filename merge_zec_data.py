import json, time, requests

# 拉取 2024-10 之后的完整数据
pair = "ZEC_USDT"
interval = "1d"
all_data = []
current_from = 1727740800  # 2024-10-01 00:00 UTC
end_ts = int(time.time())

while current_from < end_ts:
    params = {"currency_pair": pair, "interval": interval, "limit": 1000, "from": current_from}
    r = requests.get("https://api.gateio.ws/api/v4/spot/candlesticks", params=params, timeout=8)
    raw = r.json()
    if not raw:
        break
    for item in raw:
        all_data.append({
            "timestamp": int(item[0]) * 1000,
            "open": float(item[5]),
            "high": float(item[3]),
            "low": float(item[4]),
            "close": float(item[2]),
            "volume": float(item[1]),
        })
    last_ts = int(raw[-1][0])
    first_dt = time.strftime("%Y-%m-%d", time.localtime(int(raw[-1][0])))
    last_dt = time.strftime("%Y-%m-%d", time.localtime(int(raw[0][0])))
    print(f"拉取: {first_dt} ~ {last_dt} ({len(raw)}条)")
    if len(raw) < 1000:
        break
    current_from = last_ts + 86400

# 读取之前的可靠数据
with open("zec_3y_gate.json") as f:
    old_data = json.load(f)

# 合并去重
seen = {}
for d in old_data + all_data:
    ts = d["timestamp"]
    if ts not in seen:
        seen[ts] = d

merged = sorted(seen.values(), key=lambda x: x["timestamp"])

first_ts = merged[0]["timestamp"] // 1000
last_ts = merged[-1]["timestamp"] // 1000
first_close = merged[0]["close"]
last_close = merged[-1]["close"]

print(f"\n合并后: {len(merged)} 条")
print(f"范围: {time.strftime('%Y-%m-%d', time.localtime(first_ts))} ~ {time.strftime('%Y-%m-%d', time.localtime(last_ts))}")
print(f"起始 ${first_close:.2f} 最新 ${last_close:.2f}")
print(f"总涨幅: {(last_close/first_close-1)*100:.1f}%")

with open("zec_3y_full.json", "w") as f:
    json.dump(merged, f, indent=2)
print("已保存: zec_3y_full.json")