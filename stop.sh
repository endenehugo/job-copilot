#!/usr/bin/env bash
# 停止 Job Copilot 前后端（按监听端口查找进程）
BACKEND_PORT="${BACKEND_PORT:-8018}"
FRONTEND_PORT="${FRONTEND_PORT:-5174}"

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  pids=$(netstat -ano 2>/dev/null | grep ":$port" | grep LISTENING | awk '{print $5}' | sort -u)
  for pid in $pids; do
    echo "停止端口 $port 的进程 PID=$pid"
    taskkill //F //PID "$pid" > /dev/null 2>&1 || kill -9 "$pid" 2>/dev/null || true
  done
done
echo "完成。"
