# Trading System Docker 镜像
# 借鉴 QuantDinger Dockerfile 结构，Python 3.12 slim-bookworm

FROM python:3.12-slim-bookworm

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data /app/backtest_results /app/ohlcv_cache

# 暴露端口
# 8081: Dashboard (FastAPI)
# 7800: MCP Server HTTP (可选)
EXPOSE 8081 7800

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8081/api/health || exit 1

# 默认启动：Dashboard + Live Trading 守护
CMD ["sh", "-c", "\
    echo '[trading-system] Starting Dashboard on :8081'; \
    python3 -m uvicorn dashboard:app --host 0.0.0.0 --port 8081 --log-level info & \
    sleep 3 && \
    echo '[trading-system] Starting Live Trading daemon'; \
    python3 -u live_trading.py --daemon & \
    wait"]
