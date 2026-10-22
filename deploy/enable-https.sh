#!/usr/bin/env bash
# Job Copilot —— 启用域名 + HTTPS
#
#   sudo bash deploy/enable-https.sh --domain displaydemo.top
#   sudo bash deploy/enable-https.sh --domain displaydemo.top \
#        --pem /etc/nginx/ssl/displaydemo.top.pem --key /etc/nginx/ssl/displaydemo.top.key
#
# 做什么：
#   1. 安装共享片段 /etc/nginx/snippets/job-copilot-server.conf
#   2. 按模板生成 /etc/nginx/sites-available/job-copilot-https（域名 80→443 跳转 + 443 站点）
#   3. 建 sites-enabled 软链，nginx -t 通过才 reload；失败则恢复旧文件并退出非 0
#
# 幂等：可重复执行（换证书后重跑一次即可）。证书续期流程见 deploy/部署指南 附录 B。
# 注意：本脚本只加 HTTPS，不动 /etc/nginx/sites-available/job-copilot（裸 IP 的 HTTP 站点）。

set -euo pipefail

DOMAIN=""
CERT_PEM=""
CERT_KEY=""
SNIPPET_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nginx-snippet-server.conf"
CONF_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nginx-https.conf"
SNIPPET_DST="/etc/nginx/snippets/job-copilot-server.conf"
SITE_AVAIL="/etc/nginx/sites-available/job-copilot-https"
SITE_ENABLED="/etc/nginx/sites-enabled/job-copilot-https"
BACKUP_DIR="/var/backups/job-copilot"

log()  { printf '\033[32m[HTTPS]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[HTTPS]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[HTTPS]\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) DOMAIN="${2:-}"; shift 2 ;;
        --pem)    CERT_PEM="${2:-}"; shift 2 ;;
        --key)    CERT_KEY="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
done

[[ -n "$DOMAIN" ]] || die "必须指定 --domain，例如：--domain example.com"
[[ $EUID -eq 0 ]] || die "请用 root 执行（sudo bash deploy/enable-https.sh ...）"

# ---------------------------------------------------------------- 1. 证书
[[ -n "$CERT_PEM" ]] || CERT_PEM="/etc/nginx/ssl/${DOMAIN}.pem"
[[ -n "$CERT_KEY" ]] || CERT_KEY="/etc/nginx/ssl/${DOMAIN}.key"
[[ -f "$CERT_PEM" ]] || die "找不到证书：$CERT_PEM"
[[ -f "$CERT_KEY" ]] || die "找不到私钥：$CERT_KEY"
command -v openssl >/dev/null || die "缺少 openssl"

log "校验证书：$CERT_PEM"
openssl x509 -in "$CERT_PEM" -noout >/dev/null 2>&1 || die "证书文件无法解析（是不是把 .key 当 .pem 传了？）"

# SAN 必须覆盖根域；www 有则顺带生效，没有也不影响根域访问
SAN=$(openssl x509 -in "$CERT_PEM" -noout -text | awk '/Subject Alternative Name/{getline; print}')
echo "$SAN" | grep -q "DNS:${DOMAIN}" \
    || die "证书 SAN 不含 ${DOMAIN}，实际为：$(echo "$SAN" | tr -d ' ')。换一张证书，或把 --domain 改成证书里的名字"
echo "$SAN" | grep -q "DNS:www.${DOMAIN}" \
    || warn "证书 SAN 不含 www.${DOMAIN}：www 访问会证书告警（根域不受影响）"

CERT_MOD=$(openssl x509 -in "$CERT_PEM" -noout -modulus 2>/dev/null | openssl md5)
KEY_MOD=$(openssl rsa -in "$CERT_KEY" -noout -modulus 2>/dev/null | openssl md5)
[[ "$CERT_MOD" == "$KEY_MOD" ]] || die "证书与私钥不匹配（不是同一对文件）"

NOT_AFTER=$(openssl x509 -in "$CERT_PEM" -noout -enddate | cut -d= -f2)
log "证书有效期至：${NOT_AFTER}（到期前需手工换证并重跑本脚本）"

openssl x509 -in "$CERT_PEM" -noout -dates | grep -q "notAfter" || true
if ! openssl x509 -in "$CERT_PEM" -noout -checkend 2592000 >/dev/null 2>&1; then
    warn "证书剩余有效期不足 30 天，建议尽快续期"
fi

# 私钥收紧权限：nginx master 以 root 读，worker 不需要
chown root:root "$CERT_PEM" "$CERT_KEY" 2>/dev/null || true
chmod 644 "$CERT_PEM"
chmod 600 "$CERT_KEY"

# ---------------------------------------------------------------- 2. 片段
[[ -f "$SNIPPET_SRC" ]] || die "缺少 $SNIPPET_SRC（完整代码没上传？）"
mkdir -p /etc/nginx/snippets
install -m 644 "$SNIPPET_SRC" "$SNIPPET_DST"
log "已安装共享片段：$SNIPPET_DST"

if [[ ! -f /etc/nginx/snippets/ielts-daily-location.conf ]]; then
    printf '# 占位空片段：保证 job-copilot-server.conf 里的 include 合法。\n# 部署 ielts-daily 项目后由该项目覆盖本文件。\n' \
        > /etc/nginx/snippets/ielts-daily-location.conf
    log "已创建 ielts-daily-location.conf 占位（本机没有 ielts-daily 项目）"
fi

# ---------------------------------------------------------------- 3. 站点
[[ -f "$CONF_SRC" ]] || die "缺少 $CONF_SRC"
CERT_BASENAME=$(basename "$CERT_PEM" .pem)

mkdir -p "$BACKUP_DIR"
if [[ -f "$SITE_AVAIL" ]]; then
    cp -a "$SITE_AVAIL" "$BACKUP_DIR/nginx-job-copilot-https-$(date +%Y%m%d-%H%M%S).bak"
fi
cp -a "$SITE_AVAIL" /tmp/job-copilot-https.rollback 2>/dev/null || rm -f /tmp/job-copilot-https.rollback

sed -e "s/__DOMAIN__/${DOMAIN}/g" \
    -e "s/__CERT_BASENAME__/${CERT_BASENAME}/g" \
    "$CONF_SRC" > "$SITE_AVAIL"
chmod 644 "$SITE_AVAIL"
log "已生成站点配置：$SITE_AVAIL（域名 ${DOMAIN} / 证书 ${CERT_BASENAME}）"

ln -sf "$SITE_AVAIL" "$SITE_ENABLED"

# ---------------------------------------------------------------- 4. 生效
if nginx -t 2>&1; then
    systemctl reload nginx || systemctl restart nginx
    log "HTTPS 已启用：https://${DOMAIN}/"
    log "自检：curl -sS -o /dev/null -w '%{http_code}\\n' https://${DOMAIN}/"
else
    warn "nginx -t 未通过，正在回滚站点配置"
    if [[ -f /tmp/job-copilot-https.rollback ]]; then
        cp -a /tmp/job-copilot-https.rollback "$SITE_AVAIL"
    else
        rm -f "$SITE_AVAIL"
        rm -f "$SITE_ENABLED"
    fi
    die "已回滚，nginx 保持原状。请按上面的报错修配置后重试"
fi

rm -f /tmp/job-copilot-https.rollback
