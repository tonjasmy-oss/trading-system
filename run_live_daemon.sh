#!/bin/bash
# Daemon watchdog -- auto-restart on crash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/.live_daemon.pid"

# ── 清理残留 PID 文件（进程已不在时）──
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [[ -n "$OLD_PID" ]] && ! kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[watchdog] 清理残留 PID 文件 (PID $OLD_PID 已不存在)"
        rm -f "$PID_FILE"
    elif [[ -n "$OLD_PID" ]]; then
        echo "[watchdog] 发现旧进程 PID $OLD_PID，先终止..."
        kill "$OLD_PID" 2>/dev/null
        sleep 2
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "[watchdog] 强制终止 PID $OLD_PID"
            kill -9 "$OLD_PID" 2>/dev/null
        fi
        rm -f "$PID_FILE"
    fi
fi

restart_count=0
max_restarts=20
echo "[watchdog] live_trading daemon started (max ${max_restarts} restarts)"
while [ $restart_count -lt $max_restarts ]; do
    echo "[watchdog] #${restart_count} starting @ $(date '+%H:%M:%S')"
    setsid -w python3 -u live_trading.py --daemon
    restart_count=$((restart_count + 1))
    echo "[watchdog] exited, restart in 5s..."
    echo "[watchdog] $(date) restart #${restart_count}" >> /tmp/live_daemon_crash.log
    sleep 5
done
echo "[watchdog] max restarts reached, stopping"
