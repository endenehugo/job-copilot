<#
.SYNOPSIS
Fixed-configuration launcher for the three AI load-test stages.

.DESCRIPTION
Every round of a stage is started with the same preset, so rounds are comparable and the
run can be reproduced. The script:
  - clears stale LT_* environment variables (credentials and LT_HOST are kept);
  - applies the stage preset, plus any explicit override you pass;
  - never overwrites an earlier round (the output prefix must be new);
  - writes <prefix>_stats.csv / _stats_history.csv / _failures.csv / .html / .log and the
    <prefix>_ai_summary.json / <prefix>_ai_config.json produced by the Locust scripts.
Compare two rounds afterwards with: python diff_config.py <a>_ai_config.json <b>_ai_config.json

.EXAMPLE
.\run_ai_stage.ps1 -Stage 2 -Model qwen3.8-flash -Label r1 -ServerCommit cf83407

.EXAMPLE
.\run_ai_stage.ps1 -Stage 3 -Mode rag -Model qwen3.8-flash -Label r1 -ServerCommit cf83407

.EXAMPLE
.\run_ai_stage.ps1 -Stage 3 -Mode agent -ConfirmAgent -Model qwen3.8-flash -Label r1 -ServerCommit cf83407
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("1", "2", "3")][string]$Stage,
    [ValidateSet("rag", "agent")][string]$Mode,
    [string]$Model,
    [switch]$AllowServerDefaultModel,
    [string]$Label,
    [string]$ServerCommit,
    [string]$Note,
    [string]$Target,
    [int]$Users,
    [int]$RequestsPerUser,
    [double]$ThinkTime,
    [double]$MaxRps,
    [string]$RunTime,
    [ValidateSet("upload", "builtin")][string]$RagSource,
    [switch]$ConfirmAgent,
    [switch]$ConfirmTwoUsers,
    [string]$OutDir,
    [string]$Locust
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

# Presets. Stage 2 uses 1 RPS because the global gate spaces request starts by 1/RPS seconds;
# at 0.5 RPS two users take turns instead of overlapping.
$presets = @{
    "1" = @{ Script = "locustfile_ai_stage1.py"; Name = "ai-s1"; Users = 1; Requests = 1;  Think = 1; MaxRps = 0.5; RunTime = "120s" }
    "2" = @{ Script = "locustfile_ai_stage2.py"; Name = "ai-s2"; Users = 2; Requests = 10; Think = 3; MaxRps = 1;   RunTime = "120s" }
    "3" = @{ Script = "locustfile_ai_stage3.py"; Name = "ai-s3"; Users = 1; Requests = 3;  Think = 1; MaxRps = 0.5; RunTime = "120s" }
}
$preset = $presets[$Stage]

if ($Stage -eq "3") {
    if (-not $Mode) { throw "Stage 3 needs -Mode rag or -Mode agent (one mode per run)." }
} elseif ($Mode) {
    throw "-Mode only applies to Stage 3 (stages 1 and 2 are memory mode)."
}
if ($RagSource -and $Mode -ne "rag") { throw "-RagSource only applies to -Stage 3 -Mode rag." }
if (-not $Model -and -not $AllowServerDefaultModel) {
    throw "Pass -Model <name> to pin the model, or -AllowServerDefaultModel to accept the server default (the default can change between deployments)."
}

$users = if ($PSBoundParameters.ContainsKey("Users")) { $Users } else { $preset.Users }
$requests = if ($PSBoundParameters.ContainsKey("RequestsPerUser")) { $RequestsPerUser } else { $preset.Requests }
$think = if ($PSBoundParameters.ContainsKey("ThinkTime")) { $ThinkTime } else { $preset.Think }
$maxRps = if ($PSBoundParameters.ContainsKey("MaxRps")) { $MaxRps } else { $preset.MaxRps }
$runTime = if ($RunTime) { $RunTime } else { $preset.RunTime }

if ($Mode -eq "agent" -and -not $ConfirmAgent) {
    throw "Agent mode writes files (HTML resume), records and an index on the target. Re-run with -ConfirmAgent after checking cost, quota and cleanup."
}
if ($Stage -eq "3" -and $users -gt 1 -and -not $ConfirmTwoUsers) {
    throw "More than 1 user in stage 3 needs -ConfirmTwoUsers (cost, quota and tool side effects confirmed)."
}

if (-not $Label) { $Label = Get-Date -Format "HHmmss" }
$day = Get-Date -Format "yyyyMMdd"
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot "reports\$day" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$name = $preset.Name
if ($Mode) { $name = "$name-$Mode" }
$prefix = Join-Path $OutDir "$name-$Label"
if ((Get-ChildItem -Path "${prefix}_*", "${prefix}.*" -ErrorAction SilentlyContinue)) {
    throw "Output prefix already exists: $prefix. Use a new -Label; earlier rounds are never overwritten."
}

if (-not $Locust) {
    $venv = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")) ".venv-loadtest\Scripts\locust.exe"
    $onPath = Get-Command locust -ErrorAction SilentlyContinue
    if (Test-Path $venv) { $Locust = $venv }
    elseif ($onPath) { $Locust = $onPath.Source }
    else { throw "locust not found. Install it (see README) or pass -Locust <path to locust.exe>." }
}

# Start from a clean LT_* environment so nothing stale leaks into the run.
$keep = @("LT_HOST", "LT_ADMIN_TOKEN", "LT_TOKEN", "LT_BASIC_AUTH_USER", "LT_BASIC_AUTH_PASSWORD")
$cleared = @()
foreach ($item in (Get-ChildItem Env: | Where-Object { $_.Name -like "LT_*" -and $keep -notcontains $_.Name })) {
    $cleared += $item.Name
    Remove-Item "Env:$($item.Name)"
}

$inv = [Globalization.CultureInfo]::InvariantCulture
$env:LT_ENABLE_AI = "1"
$env:LT_USERS = "$users"
$env:LT_AI_MAX_REQUESTS_PER_USER = "$requests"
$env:LT_AI_THINK_TIME = $think.ToString($inv)
$env:LT_AI_MAX_RPS = $maxRps.ToString($inv)
$env:LT_RUN_TIME = $runTime
$env:LT_LABEL = $Label
if ($Model) { $env:LT_MODEL = $Model }
if ($ServerCommit) { $env:LT_SERVER_COMMIT = $ServerCommit }
if ($Note) { $env:LT_NOTE = $Note }
if ($Target) { $env:LT_HOST = $Target }
if ($Mode) { $env:LT_MODE = $Mode }
if ($Mode -eq "agent") { $env:LT_ENABLE_AGENT = "1" }
if ($RagSource) { $env:LT_RAG_SOURCE = $RagSource }
if ($Stage -eq "3" -and $users -gt 1) { $env:LT_STAGE3_CONFIRM_2_USERS = "YES" }

if (-not $ServerCommit) {
    Write-Warning "No -ServerCommit given: the deployed server version will not be recorded (required for before/after comparisons)."
}
Write-Host "Stage $Stage $Mode | users=$users requests/user=$requests think=${think}s max_rps=$maxRps run_time=$runTime model=$(if ($Model) { $Model } else { '<server default>' })"
Write-Host "Target: $(if ($env:LT_HOST) { $env:LT_HOST } else { '<script default>' })"
if ($cleared.Count -gt 0) { Write-Host "Cleared stale variables: $($cleared -join ', ')" }
Write-Host "Output prefix: $prefix"

$scriptPath = Join-Path $PSScriptRoot $preset.Script
& $Locust -f $scriptPath --headless --csv $prefix --csv-full-history --html "$prefix.html" --logfile "$prefix.log"
$code = $LASTEXITCODE

Write-Host ""
Write-Host "---- key lines from $prefix.log ----"
if (Test-Path "$prefix.log") {
    $lines = @(Get-Content "$prefix.log" -Encoding UTF8)
    $lines | Where-Object { $_ -match "loadtest" } | Select-Object -First 30
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].StartsWith("=====")) { $lines[$i..($lines.Count - 1)]; break }
    }
}
Write-Host "---- files ----"
Get-ChildItem -Path "${prefix}_*", "${prefix}.*" | ForEach-Object { Write-Host $_.Name }
Write-Host "Exit code: $code (non-zero = safety stop or failed requests; see the log lines above)"
exit $code
