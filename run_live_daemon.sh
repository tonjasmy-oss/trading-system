#!/bin/bash
# Daemon watchdog -- auto-restart on crash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
restart_count=0
max_restarts=20
echo "[watchdog] live_trading daemon started (max ${max_restarts} restarts)"
while [ $restart_count -lt $max_restarts ]; do
    echo "[watchdog] #${restart_count} starting @ $(date '+%H:%M:%S')"
    python3 -u live_trading.py --daemon
    restart_count=$((restart_count + 1))
    echo "[watchdog] exited, restart in 5s..."
    echo "[watchdog] $(date) restart #${restart_count}" >> /tmp/live_daemon_crash.log
    sleep 5
done
echo "[watchdog] max restarts reached, stopping"
