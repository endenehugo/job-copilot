#!/usr/bin/env bash
# Job Copilot —— 密钥/敏感文件扫描（提交前防呆，也可手动全量体检）
#
# 用法：
#   bash deploy/check-secrets.sh --staged     # 只扫本次暂存的内容（pre-commit 钩子用）
#   bash deploy/check-secrets.sh --tracked    # 扫所有已跟踪文件
#   bash deploy/check-secrets.sh --history    # 扫全部提交历史（推 GitHub 前跑一次）
#   bash deploy/check-secrets.sh --tracked --strict   # 把"警告"也当成失败
#
# 设计要点：
#   - 命中时只打印文件名与模式名，**绝不回显匹配到的原文**（否则密钥会进终端日志）；
#   - 占位符（sk-xxxx、sk-替换为真实Key、your-password）不会误报：长度/字符集都不满足；
#   - 文件里写注释 `# secret-scan: allow` 可跳过该文件（用于测试夹具）。
set -uo pipefail

MODE=""
STRICT=0
for arg in "$@"; do
    case "$arg" in
        --staged)  MODE=staged ;;
        --tracked) MODE=tracked ;;
        --history) MODE=history ;;
        --strict)  STRICT=1 ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "未知参数：$arg" >&2; exit 2 ;;
    esac
done
[[ -n "$MODE" ]] || { sed -n '2,14p' "$0"; exit 2; }

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "不在 git 仓库内" >&2; exit 2; }
cd "$REPO_ROOT" || exit 2

# ---- 内容特征：高信号、低误报 ----
# 注意：模式与标签用平行数组而不是 "正则|标签" 拼接——正则会包含 '|'（分支），
# 用 ${var%%|*} 切分会把正则截断（曾导致 grep 报 Unmatched ( 且全部模式失效）。
PATTERNS=(
    'sk-[A-Za-z0-9_-]{20,}'
    'mk-[A-Za-z0-9_-]{20,}'
    'AKIA[0-9A-Z]{16}'
    'gh[pousr]_[A-Za-z0-9]{30,}'
    '-----BEGIN [A-Z ]*PRIVATE KEY-----'
)
PATTERN_LABELS=(
    'API Key（sk- 开头，长度 ≥24）'
    '秘塔 Key（mk- 开头）'
    'AWS Access Key ID'
    'GitHub Token'
    '私钥文件内容'
)
# ---- 只警告不拦截 ----
WARN_PATTERNS=(
    "(password|passwd|secret|api_?key)[[:space:]]*[=:][[:space:]]*[\"'][^\"']{8,}[\"']"
)
WARN_LABELS=( '疑似硬编码口令/密钥字面量' )

# ---- 文件名特征：这些文件根本不该进仓库 ----
FORBIDDEN_NAMES=(
    '(^|/)\.env$'
    '(^|/)\.env\.(local|prod|production|dev|test)$'
    '\.(pem|key|pfx|p12)$'
    '(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$'
    '(^|/)\.htpasswd$'
    '\.(sql|sql\.gz|dump)$'
    '\.tar\.gz$'
)
FORBIDDEN_LABELS=(
    '.env 文件本身'
    '环境专用 .env'
    '证书/私钥'
    'SSH 私钥'
    'Basic Auth 口令文件'
    '数据库导出'
    '打包产物'
)
ALLOW_MARKER='secret-scan: allow'

violations=0
warnings=0
skip=0
checked=0

report() { # $1=文件 $2=标签 $3=级别
    if [[ "$3" == "violation" ]]; then
        printf '  \033[1;31m✗ [%s]\033[0m %s\n' "$2" "$1"
        violations=$((violations + 1))
    else
        printf '  \033[1;33m! [%s]\033[0m %s\n' "$2" "$1"
        warnings=$((warnings + 1))
    fi
}

check_name() { # $1=路径
    local path="$1" i
    for i in "${!FORBIDDEN_NAMES[@]}"; do
        if [[ "$path" =~ ${FORBIDDEN_NAMES[$i]} ]]; then
            # .example 模板是给别人的样板，明确放行
            [[ "$path" == *.example ]] && continue
            report "$path" "${FORBIDDEN_LABELS[$i]}" violation
            return 0
        fi
    done
    return 1
}

load_content() { # $1=路径 $2=模式：把文件内容打到 stdout（失败则输出空）
    if [[ "$2" == "staged" ]]; then
        git show ":$1" 2>/dev/null
    else
        cat -- "$1" 2>/dev/null
    fi
}

check_content() { # $1=路径 $2=模式
    local path="$1" mode="$2" i content
    # 先 tr -d '\0' 再交给 bash 捕获：否则二进制文件（.faiss/.pkl）会让 bash 打印
    # "command substitution: ignored null byte in input" 告警刷屏
    content=$( { load_content "$path" "$mode" | tr -d '\0'; } 2>/dev/null )
    [[ -n "$content" ]] || return 0
    # 注意：这里不能用 `... | head -c N` 截断——set -o pipefail 下 head 提前关闭管道
    # 会让整条管道以 SIGPIPE 失败，函数提前 return，扫描变成"假干净"。
    content=${content//$'\0'/}
    content=${content:0:2000000}
    # 只认「注释形式的标记」，避免本脚本自身源码里出现这个字面量而把自己跳过
    if grep -aqE '^[[:space:]]*(#|//|<!--)[[:space:]]*secret-scan:[[:space:]]*allow' <<<"$content"; then
        skip=$((skip + 1)); return 0
    fi
    checked=$((checked + 1))
    for i in "${!PATTERNS[@]}"; do
        # 必须用 -e：私钥特征以 '-' 开头，grep 会把它当选项
        if grep -aqE -e "${PATTERNS[$i]}" <<<"$content"; then
            report "$path" "${PATTERN_LABELS[$i]}" violation
            return 0
        fi
    done
    for i in "${!WARN_PATTERNS[@]}"; do
        if grep -aqE -e "${WARN_PATTERNS[$i]}" <<<"$content"; then
            report "$path" "${WARN_LABELS[$i]}" warn
            return 0
        fi
    done
    return 0
}

echo "== 密钥扫描（模式：$MODE，仓库：$REPO_ROOT）=="

case "$MODE" in
    staged)
        while IFS= read -r -d '' path; do
            check_name "$path" || check_content "$path" staged
        done < <(git diff --cached --name-only --diff-filter=ACM -z)
        ;;
    tracked)
        while IFS= read -r -d '' path; do
            [[ -f "$path" ]] || continue
            check_name "$path" || check_content "$path" tracked
        done < <(git ls-files -z)
        ;;
    history)
        mapfile -t commits < <(git rev-list --all)
        echo "  提交数：${#commits[@]}"
        checked=${#commits[@]}
        for commit in "${commits[@]}"; do
            for i in "${!PATTERNS[@]}"; do
                while IFS= read -r hit; do
                    [[ -n "$hit" ]] || continue
                    report "${commit:0:7}:$hit" "${PATTERN_LABELS[$i]}" violation
                done < <(git grep -I -l -E -e "${PATTERNS[$i]}" "$commit" 2>/dev/null)
            done
        done
        ;;
esac

echo "----------------------------------------------------------"
echo "  已扫内容：$checked 个文件；跳过（含 allow 标记）：$skip 个"
printf '  结论：'
if [[ "$violations" -gt 0 ]]; then
    printf '\033[1;31m发现 %d 处高危命中，已阻止\033[0m\n' "$violations"
    echo "  处理：把密钥移到 .env（已被 .gitignore 忽略）或环境变量；若已提交，需改写历史并轮换密钥。"
    exit 1
fi
if [[ "$warnings" -gt 0 ]]; then
    printf '\033[1;33m%d 处疑似（不拦截）\033[0m\n' "$warnings"
    [[ "$STRICT" -eq 1 ]] && exit 1
    exit 0
fi
printf '\033[1;32m干净\033[0m\n'
exit 0
