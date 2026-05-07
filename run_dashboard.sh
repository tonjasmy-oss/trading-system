#!/bin/bash
# =============================================================================
# 交易系统启动脚本
# 用法: bash run_dashboard.sh [options]
#   --live          启用实盘交易（默认模拟）
#   --check         仅检查状态，不启动
#   --reload        向现有进程发送 HUP 信号热重载
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─── 加载 .env 文件（如果存在）──────────────────────────────
if [[ -f ".env" ]]; then
    set -a
    source .env
    set +a
fi

# ─── 生成/验证 AGENT_TOKEN（必须）───────────────────────────
if [[ -z "${AGENT_TOKEN:-}" ]]; then
    if [[ -f ".agent_token" ]]; then
        AGENT_TOKEN="$(cat .agent_token)"
    else
        AGENT_TOKEN="$(openssl rand -hex 32)"
        echo "$AGENT_TOKEN" > .agent_token
        chmod 600 .agent_token
        echo "[启动脚本] 生成新 AGENT_TOKEN，已保存到 .agent_token"
    fi
    export AGENT_TOKEN
fi

echo "[交易系统] AGENT_TOKEN 已配置 (${AGENT_TOKEN:0:8}...)"

# ─── 常用配置（可通过环境变量覆盖）─────────────────────────
export LIVE_TRADING_ENABLED="${LIVE_TRADING_ENABLED:-false}"
export LIVE_TESTNET="${LIVE_TESTNET:-true}"
export CRYPTO_EXCHANGE="${CRYPTO_EXCHANGE:-gateio}"
export PORT="${PORT:-8081}"
export HOST="${HOST:-0.0.0.0}"

# ─── 模式 ───────────────────────────────────────────────────
CHECK_ONLY=false
RELOAD=false
for arg in "$@"; do
    case $arg in
        --check)  CHECK_ONLY=true; shift ;;
        --reload)  RELOAD=true; shift ;;
        --live)   export LIVE_TRADING_ENABLED=true; export LIVE_TESTNET=false; shift ;;
    esac
done

# ─── 检查现有进程 ───────────────────────────────────────────
PID_FILE="$SCRIPT_DIR/.dashboard.pid"
if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE")"
    if kill -0 "$PID" 2>/dev/null; then
        if [[ "$RELOAD" == "true" ]]; then
            echo "[交易系统] 发送 HUP 到 PID $PID (热重载)..."
            kill -HUP "$PID"
            echo "[交易系统] 热重载已触发"
            exit 0
        else
            echo "[警告] Dashboard 已在 PID $PID 运行 (端口 $PORT)"
            echo "使用 --reload 热重载 或 --check 查看状态"
            exit 1
        fi
    else
        echo "[启动脚本] 旧 PID 文件残留，清理..."
        rm -f "$PID_FILE"
    fi
fi

# ─── 检查模式 ───────────────────────────────────────────────
if [[ "$CHECK_ONLY" == "true" ]]; then
    echo "=== 交易系统状态检查 ==="
    if curl -sf "http://localhost:$PORT/api/system/status" > /dev/null 2>&1; then
        echo "✅ Dashboard 运行中 (端口 $PORT)"
        curl -s "http://localhost:$PORT/api/system/status" | python3 -m json.tool 2>/dev/null || true
    else
        echo "❌ Dashboard 未运行"
    fi
    echo ""
    echo "=== Git 状态 ==="
    if [[ -d .git ]]; then
        echo "分支: $(git branch --show-current)"
        echo "未提交: $(git status --short | wc -l) 项"
        echo "最近提交: $(git log --oneline -1)"
    fi
    echo ""
    echo "=== Agent Token ==="
    echo "AGENT_TOKEN: ${AGENT_TOKEN:0:8}... (已设置)"
    exit 0
fi

# ─── 启动 Dashboard ─────────────────────────────────────────
echo "[交易系统] 启动 Dashboard..."
echo "  模式: $([[ "$LIVE_TRADING_ENABLED" == "true" ]] && echo "实盘" || echo "模拟")"
echo "  交易所: $CRYPTO_EXCHANGE"
echo "  监听: $HOST:$PORT"

# 启动（nohup 方式，由启动它的 shell 管理生命周期）
python3 -m uvicorn dashboard:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload \
    > nohup.out 2>&1 &

NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"
echo "$AGENT_TOKEN" > "$SCRIPT_DIR/.agent_token"
chmod 600 "$SCRIPT_DIR/.agent_token"

echo "[交易系统] Dashboard 已启动 PID=$NEW_PID"
echo "[交易系统] PID 已保存到 $PID_FILE"

# 等待就绪
sleep 3
if curl -sf "http://localhost:$PORT/api/system/status" > /dev/null 2>&1; then
    echo "✅ Dashboard 就绪: http://localhost:$PORT"
else
    echo "⚠️  Dashboard 可能仍在启动，查看日志: tail -f nohup.out"
fi
