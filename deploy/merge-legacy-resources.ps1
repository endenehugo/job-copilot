# Job Copilot —— 归并旧原型（llmrag）运行产物到当前项目的 resources
#
# 背景：服务器 MySQL 的 llmrag 库里，conversation_documents 记录来自两个时代：
#   f:\code\llmrag\resources\...                    （旧 Flask 原型）
#   F:\code\job-copilot\backend\resources\...       （当前项目）
# 部署到服务器后两者都要能落到 /opt/job-copilot/backend/resources 下，否则
# 那条旧会话的文档检索会失效（甚至在新传文档时被静默踢出 FAISS 索引）。
#
# 用法（仓库根目录）：
#   pwsh -File deploy\merge-legacy-resources.ps1                       # 默认从 F:\code\llmrag\resources 归并
#   pwsh -File deploy\merge-legacy-resources.ps1 -LegacyResources 'E:\code\llmrag\resources'
#   pwsh -File deploy\merge-legacy-resources.ps1 -DryRun               # 只看会做什么
#
# 只归并 uploads / parsed_docs / faiss_index_uploads 三类「按会话存放」的产物：
#   - 不归并 faiss_index、faiss_index_knowledge：索引必须与当前语料一致，部署后用
#     POST /api/v1/knowledge/rebuild 重建更干净；
#   - 不归并 generated_docs、chat_history.json、memory.txt：旧原型的导出/缓存，当前代码不使用。
[CmdletBinding()]
param(
    [string]$LegacyResources = 'F:\code\llmrag\resources',
    [string]$Target,
    [string[]]$SubDirs = @('uploads', 'parsed_docs', 'faiss_index_uploads'),
    [string]$EnvFile,
    [switch]$Force,
    [switch]$SkipVerify,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Target) { $Target = Join-Path $repoRoot 'backend\resources' }
if (-not $EnvFile) { $EnvFile = Join-Path $repoRoot '.env' }

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "[警告] $Text" -ForegroundColor Yellow }

if (-not (Test-Path -LiteralPath $LegacyResources)) { throw "找不到旧原型 resources：$LegacyResources" }
if (-not (Test-Path -LiteralPath $Target)) { throw "找不到目标 resources：$Target" }

Write-Host "旧原型 : $LegacyResources"
Write-Host "目标   : $Target"

# ---------------------------------------------------------------- 1. 归并
Write-Step '1/2 归并按会话存放的运行产物'
$copied = 0; $skipped = 0; $copiedList = New-Object System.Collections.Generic.List[string]

foreach ($sub in $SubDirs) {
    $srcRoot = Join-Path $LegacyResources $sub
    if (-not (Test-Path -LiteralPath $srcRoot)) { Write-Warn "旧原型没有 $sub，跳过"; continue }
    foreach ($file in Get-ChildItem -LiteralPath $srcRoot -Recurse -File) {
        $relative = $file.FullName.Substring($srcRoot.Length).TrimStart('\')
        $destDir = Join-Path (Join-Path $Target $sub) (Split-Path -Parent $relative)
        $destFile = Join-Path $destDir (Split-Path -Leaf $relative)
        if ((Test-Path -LiteralPath $destFile) -and -not $Force) { $skipped++; continue }
        if (-not $DryRun) {
            New-Item -ItemType Directory -Force -Path $destDir | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $destFile -Force
        }
        $copied++
        $copiedList.Add("$sub\$relative")
    }
}
Write-Host ("  新增 {0} 个文件，跳过已存在 {1} 个" -f $copied, $skipped)
$copiedList | Select-Object -First 15 | ForEach-Object { Write-Host "    + $_" }
if ($copiedList.Count -gt 15) { Write-Host "    ... 另有 $($copiedList.Count - 15) 个" }
if ($DryRun) { Write-Host '  （-DryRun：未实际复制）' }

# ---------------------------------------------------------------- 2. 与数据库对账
if ($SkipVerify) { return }

Write-Step '2/2 与数据库对账：每条文档记录的绝对路径能否在本地找到'

function ReadEnvValue {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $null
}

$mysqlExe = (Get-Command mysql -ErrorAction SilentlyContinue).Source
if (-not $mysqlExe) { Write-Warn '找不到 mysql 客户端，跳对账'; return }

$dbHost = ReadEnvValue -Path $EnvFile -Key 'MYSQL_HOST'
$dbPort = ReadEnvValue -Path $EnvFile -Key 'MYSQL_PORT'
$dbUser = ReadEnvValue -Path $EnvFile -Key 'MYSQL_USER'
$dbPass = ReadEnvValue -Path $EnvFile -Key 'MYSQL_PASSWORD'
$dbName = ReadEnvValue -Path $EnvFile -Key 'MYSQL_DATABASE'
if (-not $dbHost -or -not $dbUser -or -not $dbName) { Write-Warn '读不到 .env 的 MYSQL_* 配置，跳对账'; return }
if (-not $dbPort) { $dbPort = '3306' }

$env:MYSQL_PWD = $dbPass
try {
    $rows = & $mysqlExe "--host=$dbHost" "--port=$dbPort" "--user=$dbUser" -N -B -e `
        "SELECT document_id, conversation_id, stored_path, parsed_text_path FROM $dbName.conversation_documents;"
}
finally {
    Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
}

$missing = 0
foreach ($row in $rows) {
    if (-not $row) { continue }
    $parts = $row -split "`t"
    if ($parts.Count -lt 4) { continue }
    $docId = $parts[0]; $conversationId = $parts[1]
    foreach ($raw in @($parts[2], $parts[3])) {
        $normalized = $raw -replace '\\', '/'
        $index = $normalized.LastIndexOf('/resources/')
        if ($index -lt 0) { Write-Warn "无法解析路径：$docId -> $raw"; $missing++; continue }
        $relative = $normalized.Substring($index + '/resources/'.Length) -replace '/', '\'
        $local = Join-Path $Target $relative
        if (Test-Path -LiteralPath $local) {
            Write-Host "  OK   $docId  $relative"
        }
        else {
            Write-Warn "缺失 $docId ($conversationId) -> $relative"
            $missing++
        }
    }
}

if ($missing -eq 0) {
    Write-Host "`n全部文档记录都能在本地 resources 找到对应文件，可以执行 deploy\export-data.ps1 打包上传。" -ForegroundColor Green
}
else {
    Write-Warn "有 $missing 个路径在本地找不到文件。这些文档在服务器上检索会失效——"
    Write-Warn "请确认旧原型目录是否还有别的副本，或接受让用户重新上传这几份文档。"
}
