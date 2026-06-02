#!/bin/bash
# =============================================================================
# 交易系统安全重启脚本
# =============================================================================
# 用法: bash restart.sh
#
# 安全流程:
#   1. 优雅停止 Live Daemon（SIGTERM → 等待 5s → SIGKILL）
#   2. 优雅停止 Dashboard（SIGTERM → 等待 3s → SIGKILL）
#   3. 清理残留 PID 文件和端口
#   4. 启动 Dashboard → 等待就绪（最多 15s）
#   5. 启动 Live Daemon → 验证进程存活
#   6. 输出最终状态报告
#
# 容错设计:
#   - 任意步骤失败不阻塞后续步骤
#   - 已停止的组件不会报错
#   - 最终验证全覆盖
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_DASH="$SCRIPT_DIR/.dashboard.pid"
PID_LIVE="$SCRIPT_DIR/.live_daemon.pid"
PORT="${PORT:-8081}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[restart]${NC} $*"; }
warn() { echo -e "${YELLOW}[restart]${NC} $*"; }
err()  { echo -e "${RED}[restart]${NC} $*"; }

# ─── 函数：安全停止进程 ─────────────────────────────────────
stop_process() {
    local name="$1"
    local pid_file="$2"
    local force_wait="${3:-3}"

    if [[ ! -f "$pid_file" ]]; then
        warn "$name PID 文件不存在，跳过停止"
        return 0
    fi

    local pid=$(cat "$pid_file" 2>/dev/null)
    if [[ -z "$pid" ]]; then
        warn "$name PID 文件为空，清理"
        rm -f "$pid_file"
        return 0
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
        warn "$name (PID $pid) 已不在运行，清理 PID 文件"
        rm -f "$pid_file"
        return 0
    fi

    log "停止 $name (PID $pid)..."
    kill "$pid" 2>/dev/null || true

    # 等待优雅退出
    for i in $(seq 1 $force_wait); do
        sleep 1
        if ! kill -0 "$pid" 2>/dev/null; then
            log "$name 已停止"
            rm -f "$pid_file"
            return 0
        fi
    done

    # 强制终止
    warn "$name 未响应 SIGTERM，发送 SIGKILL..."
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        err "$name (PID $pid) 无法强制终止！请手动检查"
        return 1
    fi
    log "$name 已强制终止"
    rm -f "$pid_file"
    return 0
}

# ─── 函数：等待 HTTP 就绪 ───────────────────────────────────
wait_http() {
    local url="$1"
    local max_wait="${2:-15}"
    for i in $(seq 1 $max_wait); do
        if curl -sf "$url" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# ══════════════════════════════════════════════════════════════
# 1. 停止所有服务
# ══════════════════════════════════════════════════════════════

echo ""
log "══════════════════════════════════════════════════"
log "  交易系统安全重启"
log "══════════════════════════════════════════════════"
echo ""

# 1a. 停止 Live Daemon（先停，避免在 Dashboard 不可用时继续轮询）
stop_process "Live Daemon" "$PID_LIVE" 5 || true

# 1b. 停止 Dashboard
stop_process "Dashboard" "$PID_DASH" 3 || true

# 1c. 清理可能的端口残留进程
DASH_RESIDUAL=$(lsof -ti :$PORT 2>/dev/null || true)
if [[ -n "$DASH_RESIDUAL" ]]; then
    warn "端口 $PORT 仍有残留进程: $DASH_RESIDUAL"
    kill -9 $DASH_RESIDUAL 2>/dev/null || true
    sleep 1
fi

log "所有服务已停止"
echo ""

# ══════════════════════════════════════════════════════════════
# 2. 启动 Dashboard
# ══════════════════════════════════════════════════════════════

log "── 启动 Dashboard ──"
bash "$SCRIPT_DIR/run_dashboard.sh" &
DASH_PID=$!
wait $DASH_PID 2>/dev/null || true

# 额外等待（run_dashboard.sh 内部已经等了 12s，这里再补 5s 兜底）
if ! wait_http "http://localhost:$PORT/api/system/status" 10; then
    err "Dashboard 启动失败或超时！"
    err "日志: tail -50 $SCRIPT_DIR/nohup_$(date +%Y%m%d).out"
    # 不阻止后续启动
else
    DASH_STATUS=$(curl -sf "http://localhost:$PORT/api/system/status" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['monitor']['status'])" 2>/dev/null || echo "unknown")
    log "Dashboard 就绪 (端口 $PORT, 监控: $DASH_STATUS)"
fi
echo ""

# ══════════════════════════════════════════════════════════════
# 3. 启动 Live Daemon
# ══════════════════════════════════════════════════════════════

log "── 启动 Live Daemon ──"
setsid bash "$SCRIPT_DIR/run_live_daemon.sh" > /dev/null 2>&1 &
LIVE_PID=$!

# 等待 daemon 启动
sleep 5

if [[ -f "$PID_LIVE" ]]; then
    DAEMON_PID=$(cat "$PID_LIVE")
    if kill -0 "$DAEMON_PID" 2>/dev/null; then
        log "Live Daemon 已启动 (PID $DAEMON_PID)"
    else
        err "Live Daemon PID 存在但进程不存活 (PID $DAEMON_PID)"
    fi
else
    warn "Live Daemon PID 文件未生成，检查日志: tail -20 live_loop.log"
fi
echo ""

# ══════════════════════════════════════════════════════════════
# 4. 最终验证
# ══════════════════════════════════════════════════════════════

log "══════════════════════════════════════════════════"
log "  最终状态"
log "══════════════════════════════════════════════════"

# Dashboard
if wait_http "http://localhost:$PORT/api/system/status" 3; then
    UPTIME=$(curl -sf "http://localhost:$PORT/api/system/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('uptime','?'))" 2>/dev/null || echo "?")
    echo -e "  Dashboard:  ${GREEN}✅ 运行中${NC}  (端口 $PORT, uptime ${UPTIME}s)"
else
    echo -e "  Dashboard:  ${RED}❌ 不可达${NC}"
fi

# Live Daemon
LIVE_PID=$(cat "$PID_LIVE" 2>/dev/null || echo "")
if [[ -n "$LIVE_PID" ]] && kill -0 "$LIVE_PID" 2>/dev/null; then
    AGENT_COUNT=$(grep -c "Agent 初始化" "$SCRIPT_DIR/live_loop.log" 2>/dev/null | tail -1 || echo "?")
    echo -e "  Live Daemon: ${GREEN}✅ 运行中${NC}  (PID $LIVE_PID)"
    # 最近一行日志
    LAST_LOG=$(tail -1 "$SCRIPT_DIR/live_loop.log" 2>/dev/null | cut -c1-100 || echo "无日志")
    echo "  最近日志: $LAST_LOG"
else
    echo -e "  Live Daemon: ${RED}❌ 未运行${NC}"
fi

# 因子库
if python3 -c "import sys; sys.path.insert(0,'$SCRIPT_DIR/..'); from factor_bridge import _FACTOR_AVAILABLE; exit(0 if _FACTOR_AVAILABLE else 1)" 2>/dev/null; then
    echo -e "  Factor Bridge: ${GREEN}✅ 可用${NC}"
else
    echo -e "  Factor Bridge: ${YELLOW}⚠️ 不可用${NC}"
fi

echo ""
log "重启完成。监控日志:"
echo "  Dashboard:  tail -f $SCRIPT_DIR/nohup_$(date +%Y%m%d).out"
echo "  Live Daemon: tail -f $SCRIPT_DIR/live_loop.log"
echo ""
