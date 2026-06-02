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

# 使用 setsid 彻底脱离会话，避免父 shell 退出时连带杀死子进程
# Dashboard 初始化需要 8~12 秒（因子库加载等），等待时间相应延长
LOG_FILE="nohup_$(date +%Y%m%d).out"
setsid python3 -c "
import uvicorn
uvicorn.run('dashboard:app', host='$HOST', port=$PORT)
" > "$LOG_FILE" 2>&1 &
NEW_PID=$!

# 写入 PID 文件（注：uvicorn 可能 fork worker，此处记录的是主进程 PID）
echo "$NEW_PID" > "$PID_FILE"
echo "$AGENT_TOKEN" > "$SCRIPT_DIR/.agent_token"
chmod 600 "$SCRIPT_DIR/.agent_token"

echo "[交易系统] Dashboard 已启动 PID=$NEW_PID"
echo "[交易系统] PID 已保存到 $PID_FILE"

# 清理 7 天前的旧日志
find "$SCRIPT_DIR" -name "nohup_*.out" -mtime +7 -delete 2>/dev/null || true

# 等待就绪（Dashboard 初始化因子注册表需要更多时间）
for i in $(seq 1 6); do
    sleep 2
    if curl -sf "http://localhost:$PORT/api/system/status" > /dev/null 2>&1; then
        echo "✅ Dashboard 就绪: http://localhost:$PORT (耗时 ${i}0s)"
        exit 0
    fi
done
echo "⚠️  Dashboard 启动超时 (12s)，查看日志: tail -f $LOG_FILE"
