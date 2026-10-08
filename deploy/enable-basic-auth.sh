#!/usr/bin/env bash
# Job Copilot —— 为站点启用 / 更新 Basic Auth（Nginx 层门禁）
#
# 用法（服务器上，root）：
#   sudo bash enable-basic-auth.sh                      # 自动生成强密码并启用
#   sudo bash enable-basic-auth.sh -u jobcopilot -p '自己定的密码'
#   sudo bash enable-basic-auth.sh -p '新密码'           # 只轮换密码，用户名沿用现有
#   sudo bash enable-basic-auth.sh --disable            # 关闭门禁
#
# 说明：
#   - 应用本身没有账号体系（代码里没有任何认证），这里是唯一的前端门禁；
#   - 页面、/api、SSE 全部同源，浏览器认证一次后会自己带上凭据，SSE 的 fetch 也不例外；
#   - ⚠️ 当前是 HTTP（无证书），Basic 凭据只是 base64，不是加密。它能挡住扫描器与陌生人，
#     挡不住链路上的嗅探者。备案 + HTTPS 之后才真正安全。
#   - Basic Auth 没有"退出登录"：要换账号得关掉浏览器（或开无痕窗口）。
set -Eeuo pipefail

SITE=/etc/nginx/sites-available/job-copilot
HTPASSWD_FILE=/etc/nginx/.htpasswd
REALM="Job Copilot"
USER_NAME="jobcopilot"
PASSWORD=""
DISABLE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -u|--user)     USER_NAME="${2:?}"; shift 2 ;;
        -p|--password) PASSWORD="${2:?}"; shift 2 ;;
        --disable)     DISABLE=1; shift ;;
        -h|--help)     sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 2 ;;
    esac
done

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[警告] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[失败] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 sudo 运行"
[[ -f "$SITE" ]] || die "找不到站点配置 $SITE"

# ---------------------------------------------------------------- 关闭
if [[ "$DISABLE" -eq 1 ]]; then
    log "关闭 Basic Auth"
    sed -i \
        -e 's|^\([[:space:]]*\)auth_basic "Job Copilot";|\1# auth_basic "Job Copilot";|' \
        -e 's|^\([[:space:]]*\)auth_basic_user_file /etc/nginx/.htpasswd;|\1# auth_basic_user_file /etc/nginx/.htpasswd;|' \
        "$SITE"
    nginx -t && systemctl reload nginx
    log "已关闭（.htpasswd 文件保留在 $HTPASSWD_FILE）"
    exit 0
fi

# ---------------------------------------------------------------- 口令
if [[ -z "$PASSWORD" ]]; then
    command -v openssl >/dev/null || die "缺少 openssl"
    PASSWORD=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | cut -c1-20)
    GENERATED=1
else
    GENERATED=0
fi
[[ ${#PASSWORD} -ge 8 ]] || die "密码至少 8 位"

# ---------------------------------------------------------------- htpasswd
log "1/4 生成/更新口令文件"
command -v htpasswd >/dev/null || { apt-get update -y >/dev/null && apt-get install -y apache2-utils >/dev/null; }
# 用 -i 从标准输入读密码，避免出现在 ps 命令行里；-B 用 bcrypt
if [[ -f "$HTPASSWD_FILE" ]] && command -v htpasswd >/dev/null && grep -q "^${USER_NAME}:" "$HTPASSWD_FILE" 2>/dev/null; then
    printf '%s\n' "$PASSWORD" | htpasswd -iB "$HTPASSWD_FILE" "$USER_NAME"
else
    printf '%s\n' "$PASSWORD" | htpasswd -iBc "$HTPASSWD_FILE" "$USER_NAME"
fi
chown root:www-data "$HTPASSWD_FILE" 2>/dev/null || chown root:root "$HTPASSWD_FILE"
chmod 640 "$HTPASSWD_FILE"
log "已写入 $HTPASSWD_FILE（用户 $USER_NAME，bcrypt）"

# ---------------------------------------------------------------- 站点配置
log "2/4 在站点配置里启用鉴权（幂等）"
sed -i \
    -e 's|^\([[:space:]]*\)# auth_basic "Job Copilot";|\1auth_basic "Job Copilot";|' \
    -e 's|^\([[:space:]]*\)# auth_basic_user_file /etc/nginx/.htpasswd;|\1auth_basic_user_file /etc/nginx/.htpasswd;|' \
    "$SITE"
grep -nE 'auth_basic' "$SITE" | sed 's/^/  /'
grep -qE '^[[:space:]]*auth_basic_user_file' "$SITE" || die "站点配置里没找到 auth_basic_user_file，请手工检查"

# ---------------------------------------------------------------- 生效
log "3/4 校验并重载 Nginx"
nginx -t
systemctl reload nginx

# ---------------------------------------------------------------- 验证
log "4/4 验证（站点监听的每个端口）"
# nginx 是优雅重载：旧 worker 短时间内仍会接新连接，立刻断言会误报"未认证可达"，
# 所以先轮询等 401 稳定出现，再做判断
SERVER_NAME=$(grep -m1 -E '^[[:space:]]*server_name' "$SITE" | awk '{print $2}' | tr -d ';')
# 占位符（bootstrap 未注入成功）或空值：退回用 127.0.0.1 做 Host，保证验证仍可跑
case "$SERVER_NAME" in ""|*__*|"_") SERVER_NAME="127.0.0.1" ;; esac
ACCESS_HOST="$SERVER_NAME"
[[ "$ACCESS_HOST" == "127.0.0.1" ]] && ACCESS_HOST="<公网IP>"
PORTS=$(grep -oE '^[[:space:]]*listen[[:space:]]+([0-9]+)' "$SITE" | grep -oE '[0-9]+$' | sort -u)
[[ -n "$PORTS" ]] || PORTS=80

wait_auth_effective() {
    local base="$1" i code
    for i in $(seq 1 20); do
        code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: ${SERVER_NAME}" --max-time 5 "${base}/" || true)
        [[ "$code" == "401" ]] && return 0
        sleep 0.5
    done
    return 1
}

for port in $PORTS; do
    base="http://127.0.0.1"
    [[ "$port" != "80" ]] && base="http://127.0.0.1:${port}"
    wait_auth_effective "$base" || warn "端口 $port 等待鉴权生效超时（10s）"
    code_noauth=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: ${SERVER_NAME}" --max-time 5 "${base}/" || echo ERR)
    code_auth=$(curl -s -o /dev/null -w '%{http_code}' -u "${USER_NAME}:${PASSWORD}" -H "Host: ${SERVER_NAME}" --max-time 5 "${base}/" || echo ERR)
    health=$(curl -s -u "${USER_NAME}:${PASSWORD}" -H "Host: ${SERVER_NAME}" --max-time 5 "${base}/api/v1/system/health" | head -c 46)
    printf '  端口 %-5s 未认证=%s  已认证=%s  health=%s\n' "$port" "$code_noauth" "$code_auth" "$health"
    [[ "$code_noauth" == "401" ]] || warn "端口 $port 未认证访问未返回 401，请检查"
    [[ "$code_auth" == "200" ]] || die "端口 $port 已认证访问异常（$code_auth）"
done

cat <<EOF

===================== Basic Auth 已启用 =====================
访问地址 : http://${ACCESS_HOST}/$([[ "$ACCESS_HOST" == "<公网IP>" ]] && echo "（本机配置里 server_name 是占位符，请自行替换成实际地址）")
用户名   : ${USER_NAME}
密码     : ${PASSWORD}
$( [[ "$GENERATED" -eq 1 ]] && echo "（密码为本次随机生成，请立即保存到密码管理器；不会再次显示）" )

浏览器行为：首次打开弹原生登录框；认证一次后页面内所有请求（含 SSE 流式）
            会自动带凭据。Basic Auth 没有"退出登录"，换账号请开无痕窗口。
命令行访问：curl -u ${USER_NAME}:'密码' http://${ACCESS_HOST}/api/v1/system/health
轮换密码  ：sudo bash $(readlink -f "$0") -p '新密码'
关闭门禁  ：sudo bash $(readlink -f "$0") --disable
=============================================================
EOF
