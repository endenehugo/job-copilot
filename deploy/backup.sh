#!/usr/bin/env bash
# Job Copilot —— 每日备份（MySQL 全库 + resources 运行产物）
#
# 用法：
#   sudo bash /opt/job-copilot/deploy/backup.sh            # 库 + resources
#   sudo bash /opt/job-copilot/deploy/backup.sh --db-only   # 只备份库（体积小，可每小时跑）
#
# 定时任务（crontab -e，root）：
#   0 3 * * * /opt/job-copilot/deploy/backup.sh >> /var/log/job-copilot-backup.log 2>&1
#
# 注意：备份在本机磁盘上，机器整体损坏就一起没了。重要阶段请把 $BACKUP_DIR
# 再同步到 OSS（ossutil cp -r）或另一台机器。
set -Eeuo pipefail

APP_ROOT=/opt/job-copilot
BACKUP_DIR=/var/backups/job-copilot
ENV_FILE="$APP_ROOT/.env"
RETENTION_DAYS=14
DB_ONLY=0

[[ "${1:-}" == "--db-only" ]] && DB_ONLY=1

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

[[ ${EUID} -eq 0 ]] || { echo "请用 sudo 运行" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "找不到 $ENV_FILE" >&2; exit 1; }

get_env() {
    local key="$1" default="${2:-}" val
    val=$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' || true)
    printf '%s' "${val:-$default}"
}

DB_HOST=$(get_env MYSQL_HOST 127.0.0.1)
DB_PORT=$(get_env MYSQL_PORT 3306)
DB_USER=$(get_env MYSQL_USER jobcopilot)
DB_PASS=$(get_env MYSQL_PASSWORD)
DB_NAME=$(get_env MYSQL_DATABASE job_copilot)
export MYSQL_PWD="$DB_PASS"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
TS=$(date +%Y%m%d-%H%M%S)

log "开始备份 $DB_NAME"
DB_FILE="$BACKUP_DIR/db-$TS.sql.gz"
mysqldump --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" \
    --single-transaction --quick --no-tablespaces --set-gtid-purged=OFF \
    --default-character-set=utf8mb4 "$DB_NAME" | gzip -9 > "$DB_FILE"
[[ -s "$DB_FILE" ]] || { echo "备份文件为空，备份失败" >&2; exit 1; }
log "数据库 -> $DB_FILE ($(du -h "$DB_FILE" | cut -f1))"

if [[ "$DB_ONLY" -eq 0 ]]; then
    RES_FILE="$BACKUP_DIR/resources-$TS.tar.gz"
    tar czf "$RES_FILE" -C "$APP_ROOT/backend" resources
    log "运行产物 -> $RES_FILE ($(du -h "$RES_FILE" | cut -f1))"
fi

log "清理 ${RETENTION_DAYS} 天前的备份"
find "$BACKUP_DIR" -maxdepth 1 -name 'db-*.sql.gz' -mtime "+$RETENTION_DAYS" -print -delete
find "$BACKUP_DIR" -maxdepth 1 -name 'resources-*.tar.gz' -mtime "+$RETENTION_DAYS" -print -delete
log "完成。当前备份："
ls -lh "$BACKUP_DIR" | tail -n +2
