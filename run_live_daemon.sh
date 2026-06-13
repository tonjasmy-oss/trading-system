#!/bin/bash
# Daemon watchdog -- auto-restart on crash (v2: robust PID check)
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
echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') live_trading daemon started (max ${max_restarts} restarts)" | tee -a "$LOG_FILE"
echo "[watchdog] 日志: tail -f $LOG_FILE"

while [ $restart_count -lt $max_restarts ]; do
    # ── 启动前再次确认没有遗留进程 ──
    if [[ -f "$PID_FILE" ]]; then
        OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
        if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
            echo "[watchdog] PID $OLD_PID 仍在运行，跳过重启（可能是 setsid 误报）" | tee -a "$LOG_FILE"
            sleep 30
            continue
        fi
    fi

    echo "[watchdog] #${restart_count} starting @ $(date '+%H:%M:%S')" | tee -a "$LOG_FILE"

    # ── 直接后台启动 + wait（不用 setsid -w，避免误报）──
    python3 -u live_trading.py --daemon >> "$LOG_FILE" 2>&1 &
    CHILD_PID=$!

    # 等待进程写入 PID 文件（初始化 4 个 Agent 需要约 6 秒）
    sleep 6

    # 验证进程是否真的在跑（检查 PID 文件 + 进程存活）
    if [[ -f "$PID_FILE" ]]; then
        DAEMON_PID=$(cat "$PID_FILE")
        if kill -0 "$DAEMON_PID" 2>/dev/null; then
            echo "[watchdog] 进程已启动 PID=$DAEMON_PID，等待..." | tee -a "$LOG_FILE"
        else
            echo "[watchdog] PID 文件存在但进程不存活 (PID=$DAEMON_PID)" | tee -a "$LOG_FILE"
        fi
    else
        echo "[watchdog] ⚠️  进程未生成 PID 文件 (PID=$CHILD_PID)" | tee -a "$LOG_FILE"
    fi

    # 等待子进程真正退出
    wait $CHILD_PID 2>/dev/null
    EXIT_CODE=$?

    restart_count=$((restart_count + 1))
    echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') 进程退出 (code=$EXIT_CODE, restart #${restart_count})" | tee -a "$LOG_FILE"
    echo "[watchdog] $(date) restart #${restart_count} (exit=$EXIT_CODE)" >> "$CRASH_LOG"

    # 清理可能残留的 PID 文件
    if [[ -f "$PID_FILE" ]]; then
        STALE_PID=$(cat "$PID_FILE" 2>/dev/null)
        if [[ -n "$STALE_PID" ]] && ! kill -0 "$STALE_PID" 2>/dev/null; then
            rm -f "$PID_FILE"
        fi
    fi

    sleep 15
done

echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') max restarts (${max_restarts}) reached, stopping" | tee -a "$LOG_FILE"