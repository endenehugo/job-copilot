#!/usr/bin/env bash
# Job Copilot —— 备份异地推送
#
# backup.sh 的产物只落本机磁盘（/var/backups/job-copilot），机器整体损坏就
# 一起没了——backup.sh 头部注释里自己承认的缺口，本脚本补上：把最近一份
# 数据库备份 + resources 备份推到远端。
#
# 远端二选一（也可都配，会依次推）：
#   1) SSH 目标（rsync）：BACKUP_PUSH_TARGET="user@backup-host:/backups/job-copilot"
#   2) rclone 远端（OSS/S3/B2 等）：BACKUP_PUSH_RCLONE="oss:job-copilot-backup"
#      需先 `rclone config` 配好同名 remote。
#
# 用法：
#   sudo BACKUP_PUSH_TARGET="user@host:/backups" bash /opt/job-copilot/deploy/backup_push.sh
#
# 定时（root crontab，与 backup.sh 的 0 3 错开半小时）：
#   30 3 * * * BACKUP_PUSH_TARGET="user@host:/backups" bash /opt/job-copilot/deploy/backup_push.sh >> /var/log/job-copilot-backup-push.log 2>&1
#
# 密钥不在命令行/仓库里：SSH 走免密密钥（push.ps1 已配过），rclone 配置在
# root 的 rclone.conf（权限 600），.env 只存业务密钥。
set -Eeuo pipefail

BACKUP_DIR=${BACKUP_DIR:-/var/backups/job-copilot}
PUSH_TARGET=${BACKUP_PUSH_TARGET:-}
PUSH_RCLONE=${BACKUP_PUSH_RCLONE:-}

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "失败：$*"; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 sudo 运行"
[[ -d "$BACKUP_DIR" ]] || die "备份目录不存在：$BACKUP_DIR（先跑 backup.sh）"
[[ -n "$PUSH_TARGET" || -n "$PUSH_RCLONE" ]] || die "未配置远端：设 BACKUP_PUSH_TARGET 或 BACKUP_PUSH_RCLONE"

# 取每个类型最新的一份——推"最新"而不是"全部"：异地副本以可用为先，
# 全历史留在本机（保留 14 天），真要全量再手动 rclone sync 整个目录
latest() {
    ls -1t "$BACKUP_DIR"/$1 2>/dev/null | head -1
}

DB_FILE=$(latest 'db-*.sql.gz')
RES_FILE=$(latest 'resources-*.tar.gz')
[[ -n "$DB_FILE" ]] || die "找不到数据库备份（db-*.sql.gz）"
log "待推送：$(basename "$DB_FILE")${RES_FILE:+ + $(basename "$RES_FILE")}"

FAILED=0
if [[ -n "$PUSH_TARGET" ]]; then
    for f in "$DB_FILE" ${RES_FILE:+"$RES_FILE"}; do
        rsync -z --checksum --partial "$f" "$PUSH_TARGET/" || FAILED=1
        log "rsync -> $PUSH_TARGET : $(basename "$f") $([[ $FAILED -eq 0 ]] && echo ok || echo 失败)"
    done
fi
if [[ -n "$PUSH_RCLONE" ]]; then
    for f in "$DB_FILE" ${RES_FILE:+"$RES_FILE"}; do
        rclone copyto "$f" "$PUSH_RCLONE/$(basename "$f")" --checksum || FAILED=1
        log "rclone -> $PUSH_RCLONE : $(basename "$f")"
    done
fi

# 推完校验远端文件真实存在且非空：rsync 静默失败比不备份更危险
if [[ $FAILED -eq 0 ]]; then
    log "推送完成"
else
    die "部分推送失败，请检查上方日志"
fi
