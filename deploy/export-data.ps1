# Job Copilot —— 开发机数据导出（MySQL 全库 + resources 运行产物）
#
# 用法（在仓库根目录或任意位置执行）：
#   pwsh -File deploy\export-data.ps1
#   pwsh -File deploy\export-data.ps1 -SkipResources          # 只导库
#   pwsh -File deploy\export-data.ps1 -Mysqldump 'D:\MySQL\MySQL Server 8.0\bin\mysqldump.exe'
#
# 产出目录：deploy\_artifacts\export-<时间戳>\
#   job_copilot.sql      MySQL 全库 dump（含建表语句，服务器上照原样导入）
#   resources.tar.gz     backend/resources 全量（上传文件、解析文本、FAISS 索引、技能包）
#   export-manifest.txt  行数与文件清单，导入后用来核对
#
# 数据库口令从 .env 读取，或由参数传入；通过 MYSQL_PWD 环境变量传给客户端，不落命令行。
[CmdletBinding()]
param(
    [string]$EnvFile,
    [string]$OutDir,
    [string]$Mysqldump = 'mysqldump',
    [string]$Mysql = 'mysql',
    [string]$DbHost,
    [int]$DbPort = 0,
    [string]$DbUser,
    [string]$DbPassword,
    [string]$Database,
    [switch]$SkipDump,
    [switch]$SkipResources
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $EnvFile) { $EnvFile = Join-Path $repoRoot '.env' }

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "[警告] $Text" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 读取 .env
function Get-DotEnvValue {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

if (-not $DbHost) { $DbHost = Get-DotEnvValue -Path $EnvFile -Key 'MYSQL_HOST' }
if (-not $DbHost) { $DbHost = '127.0.0.1' }
if ($DbPort -le 0) {
    $DbPort = 3306
    $envPort = Get-DotEnvValue -Path $EnvFile -Key 'MYSQL_PORT'
    if ($envPort) {
        $parsedPort = 0
        if ([int]::TryParse($envPort, [ref]$parsedPort) -and $parsedPort -gt 0) { $DbPort = $parsedPort }
    }
}
if (-not $DbUser) { $DbUser = Get-DotEnvValue -Path $EnvFile -Key 'MYSQL_USER' }
if (-not $DbUser) { $DbUser = 'root' }
if (-not $DbPassword) { $DbPassword = Get-DotEnvValue -Path $EnvFile -Key 'MYSQL_PASSWORD' }
if (-not $Database) { $Database = Get-DotEnvValue -Path $EnvFile -Key 'MYSQL_DATABASE' }
if (-not $Database) { $Database = 'job_copilot' }

if (-not $OutDir) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $OutDir = Join-Path $repoRoot "deploy\_artifacts\export-$stamp"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "仓库根目录 : $repoRoot"
Write-Host "配置文件   : $EnvFile"
Write-Host "数据库     : $Database @ ${DbHost}:${DbPort} (用户 $DbUser)"
Write-Host "输出目录   : $OutDir"

$manifest = New-Object System.Collections.Generic.List[string]
$manifest.Add("exported_at   = $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')")
$manifest.Add("host          = $env:COMPUTERNAME")
$manifest.Add("repo          = $repoRoot")
$manifest.Add("database      = ${Database}@${DbHost}:${DbPort}")

# ---------------------------------------------------------------- 1. mysqldump
if (-not $SkipDump) {
    Write-Step '1/3 导出 MySQL'
    $dumpExe = (Get-Command $Mysqldump -ErrorAction SilentlyContinue).Source
    if (-not $dumpExe) { throw "找不到 mysqldump：请用 -Mysqldump 指定完整路径" }
    $sqlPath = Join-Path $OutDir 'job_copilot.sql'
    $errPath = Join-Path $OutDir 'mysqldump.stderr.txt'

    $baseArgs = @("--host=$DbHost", "--port=$DbPort", "--user=$DbUser",
        '--single-transaction', '--quick', '--no-tablespaces',
        '--default-character-set=utf8mb4', $Database)
    $richArgs = @("--host=$DbHost", "--port=$DbPort", "--user=$DbUser",
        '--single-transaction', '--quick', '--no-tablespaces', '--hex-blob',
        '--default-character-set=utf8mb4', '--set-gtid-purged=OFF', '--skip-add-locks', $Database)

    function Invoke-Dump {
        param([string[]]$Arguments)
        # 用 Start-Process 重定向：写的是原始字节，避免 PowerShell 5.1 默认 UTF-16 把 dump 写坏
        $proc = Start-Process -FilePath $dumpExe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $sqlPath -RedirectStandardError $errPath
        return $proc.ExitCode
    }

    $env:MYSQL_PWD = $DbPassword
    try {
        $code = Invoke-Dump -Arguments $richArgs
        if ($code -ne 0) {
            Write-Warn "带完整选项的 mysqldump 失败（退出码 $code），改用最小选项重试"
            if (Test-Path $errPath) { Get-Content $errPath | Write-Host }
            $code = Invoke-Dump -Arguments $baseArgs
        }
        if ($code -ne 0) {
            if (Test-Path $errPath) { Get-Content $errPath | Write-Host }
            throw "mysqldump 退出码 $code"
        }
    }
    finally {
        Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
    }
    $sqlSize = [math]::Round((Get-Item $sqlPath).Length / 1KB, 1)
    if ((Get-Item $sqlPath).Length -lt 1024) { Write-Warn "dump 只有 ${sqlSize}KB，请确认库名是否正确" }
    Write-Host "已导出 $sqlPath (${sqlSize} KB)"
    $manifest.Add("dump          = job_copilot.sql (${sqlSize} KB)")

    # 顺带记录行数，导入后用同样的查询核对
    $mysqlExe = (Get-Command $Mysql -ErrorAction SilentlyContinue).Source
    if ($mysqlExe) {
        $tables = @('conversations', 'conversation_messages', 'conversation_documents',
            'resume_versions', 'interview_sessions', 'interview_messages', 'job_analysis', 'knowledge_entries')
        $env:MYSQL_PWD = $DbPassword
        try {
            $manifest.Add('')
            $manifest.Add('row_counts:')
            foreach ($table in $tables) {
                $query = "SELECT COUNT(*) FROM ``$Database``.``$table``;"
                $count = & $mysqlExe "--host=$DbHost" "--port=$DbPort" "--user=$DbUser" -N -B -e $query 2>$null
                if ($LASTEXITCODE -eq 0) { $manifest.Add("  $table = $count") }
            }
        }
        finally {
            Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
        }
    }
}
else {
    Write-Step '1/3 跳过数据库导出（-SkipDump）'
}

# ---------------------------------------------------------------- 2. resources
if (-not $SkipResources) {
    Write-Step '2/3 打包 backend/resources'
    $resDir = Join-Path $repoRoot 'backend\resources'
    if (-not (Test-Path $resDir)) { throw "找不到 $resDir" }
    $tarExe = (Get-Command tar -ErrorAction SilentlyContinue).Source
    if (-not $tarExe) { throw '找不到 tar.exe（Windows 10 1803+ 自带）' }
    $resTar = Join-Path $OutDir 'resources.tar.gz'
    & $tarExe -czf $resTar -C $repoRoot 'backend/resources'
    if ($LASTEXITCODE -ne 0) { throw "打包 resources 失败（tar 退出码 $LASTEXITCODE）" }
    $resSize = [math]::Round((Get-Item $resTar).Length / 1KB, 1)
    Write-Host "已打包 $resTar (${resSize} KB)"
    $manifest.Add("resources     = resources.tar.gz (${resSize} KB)")

    # 自检：FAISS 索引与上传文件必须进包，否则服务器上旧会话检索会失效
    $listing = & $tarExe -tzf $resTar
    $required = @('backend/resources/faiss_index/index.faiss')
    foreach ($item in $required) {
        if ($listing -notcontains $item) { Write-Warn "包里缺少 $item" }
    }
    foreach ($pattern in @('faiss_index_uploads/', 'uploads/', 'parsed_docs/', 'skills/')) {
        $hit = ($listing | Where-Object { $_ -like "*$pattern*" } | Measure-Object).Count
        $manifest.Add("resources:$pattern = $hit entries")
        Write-Host ("  {0,-24} {1} 项" -f $pattern, $hit)
    }
}
else {
    Write-Step '2/3 跳过 resources（-SkipResources）'
}

# ---------------------------------------------------------------- 3. 清单
Write-Step '3/3 写清单'
$manifestPath = Join-Path $OutDir 'export-manifest.txt'
$manifest | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Get-ChildItem -LiteralPath $OutDir | Select-Object Name, Length | Format-Table -AutoSize

@"

导出完成：$OutDir

下一步（把两个文件传到服务器）：
  scp "$OutDir\job_copilot.sql" "$OutDir\resources.tar.gz" root@<公网IP>:/tmp/
然后在服务器上执行：
  sudo bash /opt/job-copilot/deploy/import-data.sh /tmp/job_copilot.sql /tmp/resources.tar.gz
"@ | Write-Host
