#!/bin/bash
# Daemon watchdog -- auto-restart on crash
# 使用方式: bash run_live_daemon.sh
# 日志输出: live_loop.log (保留最近 5000 行自动截断)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/.live_daemon.pid"
LOG_FILE="$SCRIPT_DIR/live_loop.log"
CRASH_LOG="/tmp/live_daemon_crash.log"

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

# ── 日志轮转（保留最近 5000 行，防止磁盘占满）──
if [[ -f "$LOG_FILE" ]] && [[ $(wc -l < "$LOG_FILE") -gt 5000 ]]; then
    tail -3000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
    echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') --- log rotated (kept last 3000 lines) ---" >> "$LOG_FILE"
fi

restart_count=0
max_restarts=20
echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') live_trading daemon started (max ${max_restarts} restarts)"
echo "[watchdog] 日志: tail -f $LOG_FILE"
while [ $restart_count -lt $max_restarts ]; do
    echo "[watchdog] #${restart_count} starting @ $(date '+%H:%M:%S')"
    # 使用 setsid 脱离会话 + 重定向 stderr(stdout) 到 live_loop.log
    setsid -w python3 -u live_trading.py --daemon >> "$LOG_FILE" 2>&1
    restart_count=$((restart_count + 1))
    echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') exited (restart #${restart_count}), waiting 5s..."
    echo "[watchdog] $(date) restart #${restart_count}" >> "$CRASH_LOG"
    sleep 5
done
echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') max restarts (${max_restarts}) reached, stopping" | tee -a "$LOG_FILE"
