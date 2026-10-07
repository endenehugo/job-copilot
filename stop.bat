@echo off
chcp 65001 >nul
REM 停止 Job Copilot 前后端（按监听端口查找进程）
set BACKEND_PORT=8018
set FRONTEND_PORT=5174

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%BACKEND_PORT% .*LISTENING"') do (
  echo 停止后端进程 PID=%%p
  taskkill /F /PID %%p >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%FRONTEND_PORT% .*LISTENING"') do (
  echo 停止前端进程 PID=%%p
  taskkill /F /PID %%p >nul 2>&1
)
echo 完成。
pause
