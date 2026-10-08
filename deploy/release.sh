#!/usr/bin/env bash
# Job Copilot —— 服务器端发布脚本（原地更新 + 备份 + 构建 + 重启 + 健康检查）
#
# 用法（在服务器上，root）：
#   sudo bash /opt/job-copilot/deploy/release.sh /tmp/job-copilot-20261007-120000.tar.gz
#   sudo bash /opt/job-copilot/deploy/release.sh                 # 自动取 /tmp 下最新发布包
#   sudo bash /opt/job-copilot/deploy/release.sh <包> --skip-frontend   # 用包里自带的 dist
#   sudo bash /opt/job-copilot/deploy/release.sh <包> --no-pip          # 只更代码，不装依赖
#
# 关键设计：没有用 releases/<时间戳> + current 软链——应用的运行态数据（上传文件、
# FAISS 索引、解析文本）写在 backend/resources 下，而 config.py 用 Path.resolve()
# 推导路径会穿透软链，切版本时数据目录会跟着变。原地更新 + 发布前备份更稳。
set -Eeuo pipefail

APP_ROOT=/opt/job-copilot
WEB_ROOT=/var/www/job-copilot
BACKUP_DIR=/var/backups/job-copilot
SVC_NAME=job-copilot
SVC_USER=jobcopilot
HEALTH_URL=http://127.0.0.1:8000/api/v1/system/health

ARCHIVE=""
SKIP_FRONTEND=0
DO_PIP=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-frontend) SKIP_FRONTEND=1; shift ;;
        --no-pip)        DO_PIP=0; shift ;;
        -h|--help)       sed -n '2,11p' "$0"; exit 0 ;;
        -*)              echo "未知参数：$1" >&2; exit 2 ;;
        *)               ARCHIVE="$1"; shift ;;
    esac
done

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[警告] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[失败] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 sudo 运行"
[[ -d "$APP_ROOT" ]] || die "找不到 $APP_ROOT，请先执行 deploy/bootstrap.sh"

if [[ -z "$ARCHIVE" ]]; then
    ARCHIVE=$(ls -1t /tmp/job-copilot-*.tar.gz 2>/dev/null | head -1 || true)
fi
[[ -n "$ARCHIVE" && -f "$ARCHIVE" ]] || die "找不到发布包：请先在本机执行 deploy/push.ps1"
log "发布包：$ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

TS=$(date +%Y%m%d-%H%M%S)
INCOMING="$APP_ROOT/.incoming"
rm -rf "$INCOMING"
mkdir -p "$INCOMING"

# ---------------------------------------------------------------- 1. 解包与校验
log "1/8 解包"
tar xzf "$ARCHIVE" -C "$INCOMING"
[[ -f "$INCOMING/backend/requirements.txt" ]] || die "发布包结构不对：缺 backend/requirements.txt"

# Windows 上传的 shell 脚本可能带 CRLF，会让 bash 报 $'\r': command not found
find "$INCOMING/deploy" -name '*.sh' -type f -exec sed -i 's/\r$//' {} + 2>/dev/null || true

# ---------------------------------------------------------------- 2. 备份旧代码
log "2/8 备份当前代码到 $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
CODE_BACKUP="$BACKUP_DIR/code-$TS.tar.gz"
tar czf "$CODE_BACKUP" -C "$APP_ROOT" \
    --exclude='.venv' --exclude='node_modules' --exclude='*.log' \
    backend/app backend/scripts backend/requirements.txt frontend/src frontend/package.json \
    frontend/package-lock.json frontend/vite.config.js frontend/index.html deploy 2>/dev/null || true
log "回滚包：$CODE_BACKUP"

PREV_COMMIT=$(cat "$APP_ROOT/.release-commit" 2>/dev/null || echo "unknown")
log "上一版本：$PREV_COMMIT"

# ---------------------------------------------------------------- 3. 同步代码
log "3/8 同步代码（保留 .env 与运行态数据）"
rsync -a --delete \
    --exclude='.env' \
    --exclude='.git/' \
    --exclude='.incoming/' \
    --exclude='backend/.venv/' \
    --exclude='backend/resources/uploads/' \
    --exclude='backend/resources/parsed_docs/' \
    --exclude='backend/resources/faiss_index_uploads/' \
    --exclude='backend/resources/faiss_index_knowledge/' \
    --exclude='backend/resources/generated_docs/' \
    --exclude='backend/resources/chat_history.json' \
    --exclude='frontend/node_modules/' \
    --exclude='frontend/dist/' \
    --exclude='deploy/_artifacts/' \
    --exclude='*.log' \
    --exclude='__pycache__/' \
    "$INCOMING/" "$APP_ROOT/"

if [[ -f "$INCOMING/.release-manifest.txt" ]]; then
    cp "$INCOMING/.release-manifest.txt" "$APP_ROOT/.release-commit"
fi

# ---------------------------------------------------------------- 4. Python 依赖
if [[ "$DO_PIP" -eq 1 ]]; then
    log "4/8 同步 Python 依赖"
    PY_BIN=$(command -v python3.12 || command -v python3)
    VENV="$APP_ROOT/backend/.venv"
    if [[ ! -x "$VENV/bin/python" ]]; then
        log "创建虚拟环境（$PY_BIN）"
        "$PY_BIN" -m venv "$VENV"
    fi
    REQ_SUM=$(md5sum "$APP_ROOT/backend/requirements.txt" | awk '{print $1}')
    STAMP="$VENV/.requirements.md5"
    if [[ ! -f "$STAMP" || "$(cat "$STAMP" 2>/dev/null)" != "$REQ_SUM" ]]; then
        "$VENV/bin/python" -m pip install --upgrade pip --quiet
        "$VENV/bin/python" -m pip install --no-cache-dir -r "$APP_ROOT/backend/requirements.txt"
        echo "$REQ_SUM" > "$STAMP"
    else
        log "依赖未变化，跳过 pip install"
    fi
    # MySQL 8 默认 caching_sha2_password：PyMySQL 走全量认证时需要 cryptography，
    # 否则首次连接（认证缓存为空）会报 "cryptography is required for sha256_password"。
    if ! "$VENV/bin/python" -c 'import cryptography' >/dev/null 2>&1; then
        log "补装 cryptography（MySQL 8 认证需要）"
        "$VENV/bin/python" -m pip install --no-cache-dir cryptography
    fi
else
    log "4/8 --no-pip：跳过依赖安装"
fi

# ---------------------------------------------------------------- 5. 前端构建
log "5/8 前端"
WEB_PORT=$(grep -oP '^\s*listen\s+\K[0-9]+' /etc/nginx/sites-available/job-copilot 2>/dev/null | head -1 || echo 8080)
if [[ "$SKIP_FRONTEND" -eq 1 ]]; then
    [[ -d "$INCOMING/frontend/dist" ]] || die "--skip-frontend 但发布包里没有 frontend/dist"
    rm -rf "$WEB_ROOT"/*
    cp -r "$INCOMING/frontend/dist/." "$WEB_ROOT/"
    log "已发布包内预构建产物：$(find "$WEB_ROOT" -type f | wc -l) 个文件"
else
    cd "$APP_ROOT/frontend"
    command -v npm >/dev/null 2>&1 || die "服务器上没有 npm（是否用了 bootstrap.sh --no-node？）
   请改用：开发机 pwsh -File deploy\\push.ps1 -Server <host> -Build   （本地构建并随包上传）
   服务器：sudo bash $APP_ROOT/deploy/release.sh <包> --skip-frontend"
    npm ci --no-audit --no-fund
    MEM_MB=$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)
    if [[ "$MEM_MB" -lt 4096 ]]; then
        NODE_OPTIONS=--max-old-space-size=1024 npm run build
    else
        npm run build
    fi
    [[ -d dist ]] || die "前端构建失败：没有 dist 目录"
    rm -rf "$WEB_ROOT"/*
    cp -r dist/. "$WEB_ROOT/"
    log "静态产物已发布到 $WEB_ROOT"
fi

# ---------------------------------------------------------------- 6. 权限
log "6/8 校正权限"
chown -R "$SVC_USER":"$SVC_USER" "$APP_ROOT/backend/resources"
find "$APP_ROOT/backend/resources" -type d -exec chmod 755 {} +

# 发布包是 Windows 上打的 tar，目录/文件模式是 777/666；rsync -a 会原样带过来，
# 不收紧的话代码树是全用户可写。这里统一成 755/644（resources 与 .venv 单独处理）。
find "$APP_ROOT" \
    \( -path "$APP_ROOT/backend/resources" -o -path "$APP_ROOT/backend/.venv" -o -path "$APP_ROOT/.incoming" \) -prune -o \
    -type d -print0 2>/dev/null | xargs -0 -r chmod 755
find "$APP_ROOT" \
    \( -path "$APP_ROOT/backend/resources" -o -path "$APP_ROOT/backend/.venv" -o -path "$APP_ROOT/.incoming" \) -prune -o \
    -type f -print0 2>/dev/null | xargs -0 -r chmod 644

chown root:root "$APP_ROOT" 2>/dev/null || true
chmod 755 "$APP_ROOT" "$WEB_ROOT"

# Windows 打的 tar 不带执行位，脚本会是 644；而 cron 里是直接执行 backup.sh，
# 少了执行位就会 Permission denied（本次线上实测：每日备份其实一次没成功过）
if compgen -G "$APP_ROOT/deploy/*.sh" >/dev/null; then
    chmod 755 "$APP_ROOT"/deploy/*.sh
fi

if [[ -f "$APP_ROOT/.env" ]]; then
    chown root:"$SVC_USER" "$APP_ROOT/.env"
    chmod 640 "$APP_ROOT/.env"
fi

# ---------------------------------------------------------------- 7. Nginx
log "7/8 重载 Nginx"
if nginx -t; then
    systemctl reload nginx
else
    die "nginx -t 未通过，已停在重启前的状态（代码与静态产物已更新）"
fi

# ---------------------------------------------------------------- 8. 重启与健康检查
log "8/8 重启后端并做健康检查"
systemctl restart "$SVC_NAME"
for i in $(seq 1 30); do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
        break
    fi
    if [[ "$i" -eq 30 ]]; then
        echo "--- journalctl -u $SVC_NAME -n 50 ---" >&2
        journalctl -u "$SVC_NAME" -n 50 --no-pager >&2 || true
        die "健康检查失败。回滚代码：tar xzf $CODE_BACKUP -C $APP_ROOT && systemctl restart $SVC_NAME"
    fi
    sleep 2
done

PUBLIC_IP=$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
cat <<EOF

===================== 发布成功 =====================
发布包   : $ARCHIVE
上一版本 : $PREV_COMMIT
回滚包   : $CODE_BACKUP
健康检查 : OK  ($HEALTH_URL)
访问地址 : http://${PUBLIC_IP}:${WEB_PORT}/
日志     : sudo journalctl -u $SVC_NAME -f
===================================================
EOF
