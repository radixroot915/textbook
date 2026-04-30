param(
    [Parameter(Mandatory=$true)]
    [string]$Topic,

    [int]$MinFiles = 100,
    [int]$MaxIterations = 5,

    [switch]$SkipCleanup
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

function Write-Step($msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Fail($msg) {
    Write-Host "`n[ERROR] $msg" -ForegroundColor Red
    exit 1
}

# ── 1. Locate Python ──────────────────────────────────────────────────────────
Write-Step "Checking Python"
$Python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $Python) { Fail "python not found in PATH" }
Write-Host "  Python: $Python"

# ── 2. Check dependencies ─────────────────────────────────────────────────────
Write-Step "Checking dependencies"
$missing = @()
foreach ($pkg in @("requests", "bs4")) {
    $ok = & $Python -c "import $pkg" 2>$null
    if ($LASTEXITCODE -ne 0) { $missing += $pkg }
}
if ($missing) {
    Write-Host "  Installing: $($missing -join ', ')"
    & $Python -m pip install @($missing | ForEach-Object { if ($_ -eq "bs4") { "beautifulsoup4" } else { $_ } }) --quiet
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
}
Write-Host "  Dependencies OK"

# ── 3. Check Ollama ───────────────────────────────────────────────────────────
Write-Step "Checking Ollama"
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 5
    $models = $resp.models.name -join ", "
    Write-Host "  Ollama running. Models: $models"
} catch {
    Write-Host "  Ollama not running — attempting to start..." -ForegroundColor Yellow
    $ollamaExe = (Get-Command ollama -ErrorAction SilentlyContinue)?.Source
    if (-not $ollamaExe) {
        Write-Host "  [WARN] ollama not in PATH — LLM bootstrap will be skipped if unreachable" -ForegroundColor Yellow
    } else {
        Start-Process $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 5
        try {
            Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 5 | Out-Null
            Write-Host "  Ollama started OK"
        } catch {
            Write-Host "  [WARN] Ollama still unreachable — continuing anyway" -ForegroundColor Yellow
        }
    }
}

# ── 4. Run harvester ──────────────────────────────────────────────────────────
Write-Step "Running harvester: '$Topic' (min_files=$MinFiles, max_iter=$MaxIterations)"
Set-Location $ScriptDir
& $Python main.py $Topic $MinFiles $MaxIterations
if ($LASTEXITCODE -ne 0) { Fail "Harvester exited with code $LASTEXITCODE" }

# ── 5. Dedup cleanup ──────────────────────────────────────────────────────────
if (-not $SkipCleanup) {
    Write-Step "Running dedup cleanup"
    & $Python cleanup.py --topic ($Topic -replace '\s+','_') --go
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARN] Cleanup failed — vault unchanged" -ForegroundColor Yellow
    }
}

# ── 6. Summary ────────────────────────────────────────────────────────────────
$topicSlug = $Topic -replace '[^\w\s-]','' -replace '\s+','_'
$vaultPath = Join-Path $ScriptDir "vault\$topicSlug"
$curriculumPath = Join-Path $vaultPath "curriculum"

Write-Host ""
Write-Host "══════════════════════════════════════" -ForegroundColor Green
Write-Host " Done" -ForegroundColor Green
Write-Host "══════════════════════════════════════" -ForegroundColor Green

if (Test-Path $vaultPath) {
    $txtCount = (Get-ChildItem $vaultPath -Filter "*.txt" -ErrorAction SilentlyContinue).Count
    Write-Host "  Vault files : $txtCount"
}
if (Test-Path $curriculumPath) {
    Write-Host "  Curriculum  : $curriculumPath"
    Get-ChildItem $curriculumPath | ForEach-Object { Write-Host "    - $($_.Name)" }
}
Write-Host "  Log         : $ScriptDir\${topicSlug}_run.log"
