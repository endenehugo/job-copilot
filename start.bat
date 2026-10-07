@echo off
REM ============================================================
REM  Job Copilot 一键启动（Windows）
REM  双击运行：分别在新窗口启动后端与前端，并打开浏览器
REM ============================================================
setlocal
chcp 65001 >nul

REM ---- 可按需修改的配置 ----
set PYTHON=D:\conda\python.exe
set BACKEND_PORT=8018
set FRONTEND_PORT=5174
set REPO=%~dp0

if not exist "%REPO%.env" (
  echo [错误] 未找到 .env，请先复制 .env.example 为 .env 并填入 DASHSCOPE_API_KEY 与 MySQL 配置。
  pause
  exit /b 1
)

if not exist "%PYTHON%" (
  echo [警告] 未找到 %PYTHON%，改用 PATH 中的 python
  set PYTHON=python
)

echo [1/3] 启动后端 FastAPI（端口 %BACKEND_PORT%）...
start "JobCopilot-Backend" cmd /k "cd /d %REPO%backend && %PYTHON% -m uvicorn app.main:app --port %BACKEND_PORT%"

echo [2/3] 启动前端 Vite（端口 %FRONTEND_PORT%）...
if not exist "%REPO%frontend\node_modules" (
  echo        首次运行，先安装前端依赖（约 1 分钟）...
  pushd "%REPO%frontend"
  call npm install --no-audit --no-fund
  popd
)
start "JobCopilot-Frontend" cmd /k "cd /d %REPO%frontend && npm run dev"

echo [3/3] 等待服务就绪并打开浏览器...
timeout /t 12 /nobreak >nul
start "" http://localhost:%FRONTEND_PORT%/

echo.
echo 启动完成。
echo   前端:       http://localhost:%FRONTEND_PORT%/
echo   接口文档:   http://localhost:%BACKEND_PORT%/docs
echo   停止服务:   运行 stop.bat，或直接关闭上面两个命令行窗口
pause
