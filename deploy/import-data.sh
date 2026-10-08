#!/usr/bin/env bash
# Job Copilot —— 把开发机的运行产物（resources）搬到服务器，并改写库里的绝对路径
#
# 本项目有两种情形，按实际选一种：
#
# A) 数据库本来就在服务器上（当前情形：ECS 上已有 llmrag 库与全部表数据）
#    只需搬 resources + 改写路径：
#      sudo bash /opt/job-copilot/deploy/import-data.sh --paths-only /tmp/resources.tar.gz
#
# B) 数据库也要从开发机搬过去（换机器/换库名时）
#      sudo bash /opt/job-copilot/deploy/import-data.sh /tmp/job_copilot.sql /tmp/resources.tar.gz
#
# 可加 --yes 跳过确认。
#
# 路径改写这一步不能省：conversation_documents.stored_path/parsed_text_path 存的是
# 开发机绝对路径（F:\code\...\resources\...），不改写会导致旧会话检索失效，且再传
# 新文档时旧文档会被 rebuild_conversation_index 静默踢出索引。
set -Eeuo pipefail

APP_ROOT=/opt/job-copilot
BACKUP_DIR=/var/backups/job-copilot
SVC_NAME=job-copilot
SVC_USER=jobcopilot
ENV_FILE="$APP_ROOT/.env"
RESOURCES="$APP_ROOT/backend/resources"
VENV_PY="$APP_ROOT/backend/.venv/bin/python"

DUMP=""
RES_TAR=""
ASSUME_YES=0
PATHS_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)     ASSUME_YES=1; shift ;;
        --paths-only) PATHS_ONLY=1; shift ;;
        -h|--help)    sed -n '2,18p' "$0"; exit 0 ;;
        -*)           echo "未知参数：$1" >&2; exit 2 ;;
        *) if [[ -z "$DUMP" ]]; then DUMP="$1"; elif [[ -z "$RES_TAR" ]]; then RES_TAR="$1"; else echo "多余参数：$1" >&2; exit 2; fi; shift ;;
    esac
done

# --paths-only 只给一个位置参数时，那个参数显然是 resources 包
if [[ "$PATHS_ONLY" -eq 1 && -z "$RES_TAR" && "$DUMP" == *.tar.gz ]]; then
    RES_TAR="$DUMP"
    DUMP=""
fi

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[警告] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[失败] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 sudo 运行"
if [[ "$PATHS_ONLY" -eq 0 ]]; then
    [[ -n "$DUMP" && -f "$DUMP" ]] || die "用法：import-data.sh <job_copilot.sql> [resources.tar.gz]
   或：import-data.sh --paths-only <resources.tar.gz>"
else
    [[ -n "$RES_TAR" && -f "$RES_TAR" ]] || die "用法：import-data.sh --paths-only <resources.tar.gz>"
fi
[[ -f "$ENV_FILE" ]] || die "找不到 $ENV_FILE，请先按部署指南第四章写配置"

# .env 里是 KEY=value 明文，用 grep 取值而不是 source，避免密码里的特殊字符被执行
get_env() {
    local key="$1" default="${2:-}"
    local val
    val=$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' || true)
    printf '%s' "${val:-$default}"
}

DB_HOST=$(get_env MYSQL_HOST 127.0.0.1)
DB_PORT=$(get_env MYSQL_PORT 3306)
DB_USER=$(get_env MYSQL_USER jobcopilot)
DB_PASS=$(get_env MYSQL_PASSWORD)
DB_NAME=$(get_env MYSQL_DATABASE job_copilot)
[[ -n "$DB_PASS" ]] || die "$ENV_FILE 里 MYSQL_PASSWORD 为空"

MY=(mysql --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" --default-character-set=utf8mb4)
DUMPER=(mysqldump --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" --default-character-set=utf8mb4)
export MYSQL_PWD="$DB_PASS"

# ---------------------------------------------------------------- 0. 连通性
log "0/6 检查数据库连通性"
SRV_VERSION=$("${MY[@]}" -N -B -e "SELECT VERSION()") || die "连不上 MySQL，请检查 $ENV_FILE 与账号授权"
log "MySQL 版本：$SRV_VERSION（库：$DB_NAME）"

mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d-%H%M%S)

# ---------------------------------------------------------------- 1. 兼容性处理
if [[ -n "$DUMP" ]]; then
    log "1/6 检查 dump 与服务端版本兼容性"
    case "$SRV_VERSION" in
        5.*|10.*|11.*)
            warn "$SRV_VERSION 不支持 utf8mb4_0900_ai_ci（8.0 默认排序规则），自动降级为 utf8mb4_general_ci"
            sed -i 's/utf8mb4_0900_ai_ci/utf8mb4_general_ci/g' "$DUMP"
            ;;
    esac
    # 目标库不是从库、也不开 GTID 时，这些语句没有 SUPER 权限会直接报错
    sed -i -e '/SET @@GLOBAL.GTID_PURGED/d' -e '/SET @@SESSION.SQL_LOG_BIN/d' "$DUMP" || true
else
    log "1/6 --paths-only：数据库留在原处，跳过 dump 兼容性处理"
fi

# ---------------------------------------------------------------- 2. 导入前备份
EXISTING=$("${MY[@]}" -N -B -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME'" 2>/dev/null || echo 0)
if [[ "${EXISTING:-0}" -gt 0 ]]; then
    log "2/6 现网库已有 ${EXISTING} 张表，先整库备份"
    MYSQL_PWD="$DB_PASS" "${DUMPER[@]}" --single-transaction --quick --no-tablespaces \
        --set-gtid-purged=OFF "$DB_NAME" | gzip -9 > "$BACKUP_DIR/pre-import-$TS.sql.gz"
    log "备份：$BACKUP_DIR/pre-import-$TS.sql.gz ($(du -h "$BACKUP_DIR/pre-import-$TS.sql.gz" | cut -f1))"
else
    log "2/6 现网库为空，跳过备份"
fi

if [[ "$ASSUME_YES" -eq 0 ]]; then
    if [[ -n "$DUMP" ]]; then
        read -r -p "将用 $DUMP 覆盖 $DB_NAME 的同名表，继续？[y/N] " reply
    else
        read -r -p "将改写 $DB_NAME.conversation_documents 中的绝对路径并铺开 resources，继续？[y/N] " reply
    fi
    [[ "$reply" =~ ^[Yy]$ ]] || die "已取消"
fi

# ---------------------------------------------------------------- 3. 导入
if [[ -n "$DUMP" ]]; then
    log "3/6 导入数据"
    "${MY[@]}" "$DB_NAME" < "$DUMP"
    "${MY[@]}" -N -B -e "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema='$DB_NAME' ORDER BY table_name"
else
    log "3/6 --paths-only：跳过数据导入"
fi

# ---------------------------------------------------------------- 4. resources
log "4/6 铺开 resources 运行产物"
mkdir -p "$RESOURCES"
if [[ -n "$RES_TAR" && -f "$RES_TAR" ]]; then
    tar xzf "$RES_TAR" -C "$APP_ROOT"
    log "已解包 $RES_TAR 到 $APP_ROOT/backend/resources"
else
    warn "未提供 resources.tar.gz，跳过（旧会话的上传文件/FAISS 索引将不可用）"
fi
chown -R "$SVC_USER":"$SVC_USER" "$RESOURCES"
find "$RESOURCES" -type d -exec chmod 755 {} +

# ---------------------------------------------------------------- 5. 路径改写
log "5/6 改写库里记录的绝对路径（开发机 → 服务器）"
if [[ ! -x "$VENV_PY" ]]; then
    log "首次运行：创建虚拟环境并安装后端依赖"
    PY_BIN=$(command -v python3.12 || command -v python3)
    [[ -f "$APP_ROOT/backend/requirements.txt" ]] || die "找不到 backend/requirements.txt，请先上传代码（deploy/push.ps1 -FirstTime）"
    "$PY_BIN" -m venv "$APP_ROOT/backend/.venv"
    "$VENV_PY" -m pip install --upgrade pip --quiet
    "$VENV_PY" -m pip install --no-cache-dir -r "$APP_ROOT/backend/requirements.txt"
    "$VENV_PY" -c 'import cryptography' >/dev/null 2>&1 || "$VENV_PY" -m pip install --no-cache-dir cryptography
fi
MYSQL_HOST="$DB_HOST" MYSQL_PORT="$DB_PORT" MYSQL_USER="$DB_USER" \
MYSQL_PASSWORD="$DB_PASS" MYSQL_DATABASE="$DB_NAME" \
    "$VENV_PY" "$APP_ROOT/deploy/fix_resource_paths.py" --resources-root "$RESOURCES" --report-missing

# ---------------------------------------------------------------- 6. 重启与校验
log "6/6 重启后端并校验"
systemctl restart "$SVC_NAME"
for i in $(seq 1 30); do
    curl -fsS --max-time 3 http://127.0.0.1:8000/api/v1/system/health >/dev/null 2>&1 && break
    [[ "$i" -eq 30 ]] && { journalctl -u "$SVC_NAME" -n 40 --no-pager || true; die "后端健康检查失败"; }
    sleep 2
done

log "数据核对（会话 / 消息 / 文档 / 简历版本 / 面试会话 / JD 分析 / 知识条目）"
"${MY[@]}" -t -e "
SELECT 'conversations' AS t, COUNT(*) AS rows_ FROM \`$DB_NAME\`.conversations
UNION ALL SELECT 'conversation_messages', COUNT(*) FROM \`$DB_NAME\`.conversation_messages
UNION ALL SELECT 'conversation_documents', COUNT(*) FROM \`$DB_NAME\`.conversation_documents
UNION ALL SELECT 'resume_versions', COUNT(*) FROM \`$DB_NAME\`.resume_versions
UNION ALL SELECT 'interview_sessions', COUNT(*) FROM \`$DB_NAME\`.interview_sessions
UNION ALL SELECT 'job_analysis', COUNT(*) FROM \`$DB_NAME\`.job_analysis
UNION ALL SELECT 'knowledge_entries', COUNT(*) FROM \`$DB_NAME\`.knowledge_entries;"

cat <<EOF

===================== 数据迁移完成 =====================
数据库备份（导入前）：$BACKUP_DIR/pre-import-$TS.sql.gz
请到浏览器里打开一个旧会话，确认历史消息与文档可正常检索。
=======================================================
EOF
