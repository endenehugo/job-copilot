# Job Copilot —— 开发机打包并推送到阿里云服务器
#
# 用法（在仓库根目录执行）：
#   pwsh -File deploy\push.ps1 -Server root@1.2.3.4                       # 打包 + scp + 服务器上发布
#   pwsh -File deploy\push.ps1 -Server root@1.2.3.4 -FirstTime            # 首次：解包 + 初始化服务器
#   pwsh -File deploy\push.ps1 -Server root@1.2.3.4 -Build                # 本地构建前端，随包上传
#   pwsh -File deploy\push.ps1 -Server root@1.2.3.4 -KeyFile $HOME\.ssh\id_ed25519
#   pwsh -File deploy\push.ps1 -Server root@1.2.3.4 -DryRun               # 只打包，打印要执行的命令
#
# 发布包内容：后端源码 + 前端源码 + deploy 脚本 + 文档。
# 不含 .env、node_modules、.venv、以及 backend/resources 下的运行态数据
# （上传文件/FAISS 索引走 deploy\export-data.ps1 的数据迁移通道）。
# 若加了 -Build，则额外带上本地构建好的 frontend/dist。
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [int]$SshPort = 22,
    [string]$KeyFile,
    [string]$RemoteRoot = '/opt/job-copilot',
    [switch]$Build,
    [switch]$FirstTime,
    [switch]$SkipDeploy,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$artifactDir = Join-Path $repoRoot 'deploy\_artifacts'
$archiveName = "job-copilot-$stamp.tar.gz"
$archive = Join-Path $artifactDir $archiveName

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Green }
function Invoke-Native {
    # 注意：必须用具名参数调用（-FilePath/-Arguments）。简单函数会把多余的位置参数
    # 丢进 $args 而不报错，曾导致 ssh 少收 destination 与远端命令。
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$AllowFailure
    )
    Write-Host ("  $FilePath " + ($Arguments -join ' ')) -ForegroundColor DarkGray
    & $FilePath @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $AllowFailure) { throw "$FilePath 退出码 $code" }
    return $code
}

foreach ($tool in 'ssh', 'scp', 'tar') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "找不到 $tool（Windows 10 1809+ 自带 OpenSSH 客户端）"
    }
}

New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null

# ---------------------------------------------------------------- 0. 可选：本地构建前端
if ($Build) {
    Write-Step '0/4 本地构建前端（解决 2G 内存服务器构建 OOM 的场景）'
    Push-Location (Join-Path $repoRoot 'frontend')
    try {
        if (-not (Test-Path 'node_modules')) { Invoke-Native -FilePath npm -Arguments @('ci', '--no-audit', '--no-fund') | Out-Null }
        Invoke-Native -FilePath npm -Arguments @('run', 'build') | Out-Null
    }
    finally { Pop-Location }
    if (-not (Test-Path (Join-Path $repoRoot 'frontend\dist\index.html'))) { throw '前端构建失败：没有 dist\index.html' }
    Write-Host '本地构建完成'
}

# ---------------------------------------------------------------- 1. 打包
Write-Step "1/4 打包 -> $archiveName"
$excludes = New-Object System.Collections.Generic.List[string]
foreach ($pattern in @(
        './.git', './.git/*',
        './.env',
        './backend/.venv', './backend/.venv/*',
        './backend/.pytest_cache', './backend/.pytest_cache/*',
        './backend/resources/uploads', './backend/resources/uploads/*',
        './backend/resources/parsed_docs', './backend/resources/parsed_docs/*',
        './backend/resources/faiss_index_uploads', './backend/resources/faiss_index_uploads/*',
        './backend/resources/faiss_index_knowledge', './backend/resources/faiss_index_knowledge/*',
        './backend/resources/generated_docs', './backend/resources/generated_docs/*',
        './backend/resources/chat_history.json',
        './frontend/node_modules', './frontend/node_modules/*',
        './deploy/_artifacts', './deploy/_artifacts/*',
        './backend/backend.log', './frontend/frontend.log',
        '*.pyc', '__pycache__'
    )) { $excludes.Add($pattern) }
if (-not $Build) { $excludes.Add('./frontend/dist'); $excludes.Add('./frontend/dist/*') }

# 版本清单：服务器上 release.sh 会把它留成 /opt/job-copilot/.release-commit
$manifestPath = Join-Path $repoRoot '.release-manifest.txt'
$commit = (& git -C $repoRoot rev-parse --short HEAD 2>$null)
if (-not $commit) { $commit = 'no-git' }
@(
    "built_at=$stamp"
    "commit=$commit"
    "built_on=$env:COMPUTERNAME"
    "with_dist=$([int][bool]$Build)"
) | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$tarExe = (Get-Command tar).Source
$tarArgs = @('-czf', $archive, '-C', $repoRoot)
foreach ($pattern in $excludes) { $tarArgs += "--exclude=$pattern" }
$tarArgs += @('.')   # '.' 已递归包含 .release-manifest.txt，无需重复指定
try {
    Invoke-Native -FilePath $tarExe -Arguments $tarArgs | Out-Null
}
finally {
    Remove-Item -LiteralPath $manifestPath -ErrorAction SilentlyContinue
}

# 自检：运行态数据与依赖目录绝不能进包
$listing = & $tarExe -tzf $archive
foreach ($forbidden in @('node_modules', '/.venv/', 'backend/resources/uploads/', 'backend/resources/faiss_index_uploads/', '/.git/')) {
    $hit = ($listing | Where-Object { $_ -like "*$forbidden*" } | Measure-Object).Count
    if ($hit -gt 0) { throw "发布包里出现了不该有的内容：$forbidden（$hit 项）" }
}
$sizeMB = [math]::Round((Get-Item $archive).Length / 1MB, 2)
Write-Host "打包完成：$archive ($sizeMB MB, $(($listing | Measure-Object).Count) 项, commit=$commit)"

if ($DryRun) {
    Write-Step '2/4 试运行：跳过 scp/ssh'
    Write-Host "  scp -P $SshPort $archive ${Server}:/tmp/"
    Write-Host "  ssh -p $SshPort $Server `"sudo bash $RemoteRoot/deploy/release.sh /tmp/$archiveName`""
    return
}

# ---------------------------------------------------------------- 2. 上传
Write-Step '2/4 上传到服务器 /tmp'
$scpArgs = New-Object System.Collections.Generic.List[string]
if ($SshPort -ne 22) { $scpArgs.Add('-P'); $scpArgs.Add([string]$SshPort) }
if ($KeyFile) { $scpArgs.Add('-i'); $scpArgs.Add($KeyFile) }
$scpArgs.Add($archive)
$scpArgs.Add("${Server}:/tmp/")
Invoke-Native -FilePath scp -Arguments $scpArgs.ToArray() | Out-Null

# ---------------------------------------------------------------- 3. 服务器端动作
# 不加 -t：sudo 需要免密（root 直连天然满足；普通用户请配 NOPASSWD，见部署指南第二章）
$sshArgs = New-Object System.Collections.Generic.List[string]
if ($SshPort -ne 22) { $sshArgs.Add('-p'); $sshArgs.Add([string]$SshPort) }
if ($KeyFile) { $sshArgs.Add('-i'); $sshArgs.Add($KeyFile) }

if ($FirstTime) {
    Write-Step '3/4 首次部署：解包 + 初始化服务器'
    $remote = "sudo mkdir -p $RemoteRoot && sudo tar xzf /tmp/$archiveName -C $RemoteRoot && " +
    "sudo bash $RemoteRoot/deploy/bootstrap.sh"
    Invoke-Native -FilePath ssh -Arguments ($sshArgs.ToArray() + @($Server, $remote)) | Out-Null
    Write-Host @"

首次初始化已执行。接下来按顺序完成（详见 deploy\部署指南.md）：
  1) 写配置：sudo cp $RemoteRoot/deploy/.env.production.example $RemoteRoot/.env && sudo vi $RemoteRoot/.env
  2) 建库账号（第四章）
  3) 迁移数据：deploy\export-data.ps1 -> scp -> 服务器 import-data.sh（第六章）
  4) 再执行一次：pwsh -File deploy\push.ps1 -Server $Server        # 正式发布
"@
    return
}

if ($SkipDeploy) {
    Write-Step '3/4 -SkipDeploy：只上传，不发布'
    Write-Host "  服务器上手动发布：sudo bash $RemoteRoot/deploy/release.sh /tmp/$archiveName"
    return
}

Write-Step '3/4 服务器端发布（release.sh）'
$skipFrontendArg = ''
if ($Build) { $skipFrontendArg = ' --skip-frontend' }
$releaseCmd = "sudo bash $RemoteRoot/deploy/release.sh /tmp/$archiveName$skipFrontendArg"
Invoke-Native -FilePath ssh -Arguments ($sshArgs.ToArray() + @($Server, $releaseCmd)) | Out-Null

Write-Step '4/4 完成'
Write-Host "  验证地址：http://$($Server.Split('@')[-1]):8080/"
Write-Host "  查看日志：ssh $Server 'sudo journalctl -u job-copilot -f'"
