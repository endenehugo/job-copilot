#!/usr/bin/env bash
# Job Copilot —— 阿里云 ECS（Ubuntu 22.04 / 24.04）一次性初始化
#
# 用法（在服务器上、代码已上传之后运行）：
#   sudo bash /opt/job-copilot/deploy/bootstrap.sh
#   sudo bash /opt/job-copilot/deploy/bootstrap.sh --domain demo.example.com  # 有域名时用域名做 server_name
#   sudo bash /opt/job-copilot/deploy/bootstrap.sh --cn-mirror      # 国内机器：pip/npm 走国内源
#   sudo bash /opt/job-copilot/deploy/bootstrap.sh --web-port 8080  # 额外监听端口（默认 80+8080）
#   sudo bash /opt/job-copilot/deploy/bootstrap.sh --no-node        # 不在服务器构建前端
#
# 幂等：可重复执行。做四件事：装依赖 → 建用户与目录 → 装 Nginx 站点 → 装 systemd 单元。
set -Eeuo pipefail

APP_ROOT=/opt/job-copilot
WEB_ROOT=/var/www/job-copilot
BACKUP_DIR=/var/backups/job-copilot
SVC_USER=jobcopilot
WEB_PORT=8080
CN_MIRROR=0
INSTALL_NODE=1
DOMAIN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cn-mirror) CN_MIRROR=1; shift ;;
        --no-node)   INSTALL_NODE=0; shift ;;
        --web-port)  WEB_PORT="${2:?--web-port 需要一个端口}"; shift 2 ;;
        --domain)    DOMAIN="${2:?--domain 需要一个域名}"; shift 2 ;;
        -h|--help)   sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 2 ;;
    esac
done

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[警告] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[失败] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 sudo 运行"
command -v apt-get >/dev/null || die "本脚本面向 Debian/Ubuntu；CentOS/Alibaba Cloud Linux 请参考文档附录"

# shellcheck disable=SC1091
. /etc/os-release
log "系统：${PRETTY_NAME:-unknown}"

# ---------------------------------------------------------------- 1. 系统依赖
log "1/5 安装系统依赖（nginx / mysql-client / rsync / htpasswd / libgomp1）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg rsync nginx mysql-client apache2-utils libgomp1

# Python：优先 3.12（与开发环境一致）；22.04 需要 deadsnakes PPA，装不上就回退系统 python3。
PY_BIN=""
if command -v python3.12 >/dev/null 2>&1; then
    PY_BIN=python3.12
    log "1b 已存在 Python 3.12，跳过安装"
else
    log "1b 安装 Python 3.12"
    apt-get install -y --no-install-recommends software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1 || warn "添加 deadsnakes PPA 失败"
    apt-get update -y || true
    if apt-get install -y --no-install-recommends python3.12 python3.12-venv; then
        PY_BIN=python3.12
    else
        warn "python3.12 安装失败（PPA 不可达？）——回退到系统 python3"
        warn "项目依赖均为预编译 wheel，Python 3.10/3.11 同样可用"
        apt-get install -y --no-install-recommends python3 python3-venv
        PY_BIN=python3
    fi
fi
"$PY_BIN" -V

# ---------------------------------------------------------------- 2. 账号与目录
log "2/5 创建服务账号与目录"
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$APP_ROOT" --shell /usr/sbin/nologin "$SVC_USER"
fi
mkdir -p "$APP_ROOT" "$WEB_ROOT" "$BACKUP_DIR"
chown root:"$SVC_USER" "$APP_ROOT"
chmod 755 "$APP_ROOT" "$WEB_ROOT"
chmod 700 "$BACKUP_DIR"

# 代码是从 Windows 打的 tar 解出来的，不带执行位；cron 直接执行 backup.sh 需要它
if compgen -G "$APP_ROOT/deploy/*.sh" >/dev/null; then
    chmod 755 "$APP_ROOT"/deploy/*.sh
fi

# 小内存机器给 2G swap：pip 安装与 vite 构建是最容易 OOM 的两步
MEM_MB=$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)
if [[ "$MEM_MB" -lt 2048 ]] && ! swapon --show | grep -q .; then
    log "2b 内存 ${MEM_MB}MB 且无 swap，创建 /swapfile（2G）"
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 时区：应用用 datetime.now() 写 created_at，服务器与本地不一致会让历史数据时间错乱
if [[ "$(timedatectl show -p Timezone --value 2>/dev/null || true)" != "Asia/Shanghai" ]]; then
    log "2c 设置时区 Asia/Shanghai"
    timedatectl set-timezone Asia/Shanghai || warn "时区设置失败，请手动检查"
fi

# ---------------------------------------------------------------- 3. Nginx 站点
log "3/5 安装 Nginx 站点"
NGINX_SRC="$APP_ROOT/deploy/nginx-host.conf"
if [[ -f "$NGINX_SRC" ]]; then
    install -m 644 "$NGINX_SRC" /etc/nginx/sites-available/job-copilot
    if [[ "$WEB_PORT" != "8080" ]]; then
        sed -i "s/listen 8080;/listen ${WEB_PORT};/; s/listen \[::\]:8080;/listen [::]:${WEB_PORT};/" \
            /etc/nginx/sites-available/job-copilot
    fi

    # 注入 server_name：配置里是 __SERVER_NAME__ 占位符，避免把真实 IP 写进仓库
    SERVER_NAME="$DOMAIN"
    if [[ -z "$SERVER_NAME" ]]; then
        SERVER_NAME=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null \
            || curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null \
            || hostname -I 2>/dev/null | awk '{print $1}' || true)
    fi
    SERVER_NAME=${SERVER_NAME//[[:space:]]/}
    if [[ -z "$SERVER_NAME" ]]; then
        warn "取不到公网 IP：站点配置里仍是 __SERVER_NAME__ 占位符，请手工替换后再 reload"
    else
        sed -i "s/__SERVER_NAME__/${SERVER_NAME}/" /etc/nginx/sites-available/job-copilot
        log "server_name 注入为：${SERVER_NAME}（如需域名：--domain your.domain.com）"
    fi

    ln -sf /etc/nginx/sites-available/job-copilot /etc/nginx/sites-enabled/job-copilot

    if nginx -t; then
        systemctl enable nginx >/dev/null 2>&1 || true
        systemctl reload nginx || systemctl restart nginx
        # nginx 的 include 是 sites-enabled/*，重名/带后缀的站点都会一起加载：
        # 同名 server_name 会静默由"先加载的那个"接管（线上踩过一次，表现为 502）
        if nginx -t 2>&1 | grep -qi conflicting; then
            warn "存在 conflicting server name：sites-enabled/ 下有站点与本站点重名，"
            warn "请移出那个站点（带 .disabled 后缀也会被 * 匹配）后重载"
        fi
        log "Nginx 站点已启用：http://${SERVER_NAME:-<公网IP>}/"
    else
        warn "nginx -t 未通过，站点已就位但未生效，请检查上面的报错"
    fi
else
    warn "未找到 $NGINX_SRC（代码还没上传？）——上传后重新执行本脚本即可"
fi

# ---------------------------------------------------------------- 4. systemd 单元
log "4/5 安装 systemd 单元"
SVC_SRC="$APP_ROOT/deploy/job-copilot.service"
if [[ -f "$SVC_SRC" ]]; then
    install -m 644 "$SVC_SRC" /etc/systemd/system/job-copilot.service
    systemctl daemon-reload
    systemctl enable job-copilot >/dev/null 2>&1 || true
    log "已注册 job-copilot.service（.env 就绪后：sudo systemctl start job-copilot）"
else
    warn "未找到 $SVC_SRC（代码还没上传？）"
fi

# ---------------------------------------------------------------- 5. Node 与镜像
log "5/5 检查 Node.js 与镜像源"
if [[ "$INSTALL_NODE" -eq 1 ]]; then
    NODE_MAJOR=0
    if command -v node >/dev/null 2>&1; then
        NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
    fi
    if [[ "${NODE_MAJOR:-0}" -lt 18 ]]; then
        log "安装 Node.js 22（Vite 6 要求 >= 18；依赖树里的 @vueuse 要求 >= 22）"
        if ! curl -fsSL https://deb.nodesource.com/setup_22.x | bash -; then
            warn "NodeSource 源不可达，回退到发行版自带的 nodejs"
        fi
        apt-get install -y nodejs
    else
        log "Node.js 已满足：$(node -v)"
    fi
else
    warn "--no-node：跳过 Node——需在本地构建前端，并在发布时带 --skip-frontend"
fi

if [[ "$CN_MIRROR" -eq 1 ]]; then
    log "配置国内镜像源（pip / npm）"
    mkdir -p /etc/pip.conf.d
    cat > /etc/pip.conf <<'EOF'
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
timeout = 60
EOF
    if command -v npm >/dev/null 2>&1; then
        npm config set registry https://registry.npmmirror.com
    fi
fi

# ---------------------------------------------------------------- 防火墙提示
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    log "放行 ufw：22/tcp 与 ${WEB_PORT}/tcp"
    ufw allow 22/tcp >/dev/null
    ufw allow "${WEB_PORT}/tcp" >/dev/null
fi

cat <<EOF

===================== 初始化完成 =====================
代码目录 : $APP_ROOT
静态目录 : $WEB_ROOT
备份目录 : $BACKUP_DIR
站点端口 : $WEB_PORT
Python   : $PY_BIN

接下来（按顺序）：
  1) 写配置：sudo cp $APP_ROOT/deploy/.env.production.example $APP_ROOT/.env
             sudo vi $APP_ROOT/.env
             sudo chown root:$SVC_USER $APP_ROOT/.env && sudo chmod 640 $APP_ROOT/.env
  2) 建数据库账号（见部署指南第四章）
  3) 迁移数据（见部署指南第六章）
  4) 发布   ：sudo bash $APP_ROOT/deploy/release.sh /tmp/<发布包>.tar.gz
  5) 别忘了阿里云控制台安全组放行 ${WEB_PORT}/tcp
======================================================
EOF
