#!/usr/bin/env bash
# Job Copilot 一键启动（Git Bash / Linux）
# 用法：./start.sh   停止：./stop.sh
set -e
cd "$(dirname "$0")"

# ---- 可按需修改的配置 ----
PYTHON="${PYTHON:-D:/conda/python.exe}"   # Windows Git Bash 默认；Linux 上 export PYTHON=python3
BACKEND_PORT="${BACKEND_PORT:-8018}"
FRONTEND_PORT="${FRONTEND_PORT:-5174}"

if [ ! -f ".env" ]; then
  echo "[错误] 未找到 .env，请先复制 .env.example 为 .env 并填入 DASHSCOPE_API_KEY 与 MySQL 配置。"
  exit 1
fi

command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python

echo "[1/2] 启动后端 FastAPI（端口 $BACKEND_PORT，日志 backend.log）..."
( cd backend && "$PYTHON" -m uvicorn app.main:app --port "$BACKEND_PORT" > backend.log 2>&1 & )

echo "[2/2] 启动前端 Vite（端口 $FRONTEND_PORT，日志 frontend.log）..."
(
  cd frontend
  if [ ! -d node_modules ]; then
    echo "      首次运行，先安装前端依赖..."
    npm install --no-audit --no-fund
  fi
  npm run dev > frontend.log 2>&1 &
)

sleep 12

# 就绪检查
curl -s --max-time 3 "http://127.0.0.1:$BACKEND_PORT/api/v1/system/health" > /dev/null \
  && echo "后端就绪: http://localhost:$BACKEND_PORT/docs" \
  || echo "[警告] 后端未就绪，请查看 backend/backend.log"

# 打开浏览器（Git Bash 用 explorer，Linux 用 xdg-open）
explorer.exe "http://localhost:$FRONTEND_PORT/" 2>/dev/null || xdg-open "http://localhost:$FRONTEND_PORT/" 2>/dev/null || true

echo "启动完成。停止服务：./stop.sh"
