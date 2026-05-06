# ai-video-editor — one-click deploy (Windows PowerShell)
#
# Usage (from PowerShell):
#   .\deploy.ps1 up         start (builds image on first run)
#   .\deploy.ps1 rebuild    force image rebuild
#   .\deploy.ps1 restart    restart container without rebuilding
#   .\deploy.ps1 down       stop and remove container
#   .\deploy.ps1 logs       tail container logs
#   .\deploy.ps1 status     show health + ports
#
# If Windows blocks running scripts, enable once per session:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'rebuild', 'restart', 'down', 'logs', 'status')]
    [string]$Command = 'up'
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Error "docker not found. Install Docker Desktop first."
    }
    try { docker info *>$null } catch {
        Write-Error "Docker daemon not running. Start Docker Desktop and retry."
    }
    try { docker compose version *>$null } catch {
        Write-Error "'docker compose' not available. Docker 20.10+ with Compose v2 required."
    }
}

function Test-Port {
    param([int]$Port)
    $inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($inUse) {
        $pid = $inUse[0].OwningProcess
        Write-Error "Port $Port is in use (PID=$pid). Free it or change WEB_PORT/MCP_PORT in .env."
    }
}

function Initialize-Env {
    if (Test-Path .env) { return }

    # Detect JianYing install
    $draftDir = "$env:LOCALAPPDATA\JianyingPro\User Data\Projects\com.lveditor.draft"
    $cacheDir = "$env:LOCALAPPDATA\JianyingPro\User Data\Cache\artistEffect"

    # docker-compose on Windows needs forward slashes + absolute paths
    $draftDirCompose = $draftDir -replace '\\', '/'
    $cacheDirCompose = $cacheDir -replace '\\', '/'

    if (-not (Test-Path $draftDir)) {
        Write-Host "[deploy] JianYing not detected at '$draftDir'"
        Write-Host "         => drafts will land in .\drafts (edit .env later to change)"
        $draftDirCompose = ''
        $cacheDirCompose = ''
    }

    @"
# Auto-generated on first run by deploy.ps1 ($(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
# OS: windows
WEB_PORT=8000
MCP_PORT=9001
JIANYING_DRAFT_DIR=$draftDirCompose
JIANYING_CACHE_DIR=$cacheDirCompose
"@ | Set-Content -Path .env -Encoding UTF8

    Write-Host "[deploy] wrote .env (JianYing draft dir: $(if ($draftDirCompose) { $draftDirCompose } else { './drafts' }))"
}

function Get-EnvValue {
    param([string]$Key, [string]$Default)
    if (-not (Test-Path .env)) { return $Default }
    $line = Select-String -Path .env -Pattern "^$Key=" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $line) { return $Default }
    $value = $line.Line -replace "^$Key=", ''
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

function Wait-Healthy {
    $port = Get-EnvValue -Key 'WEB_PORT' -Default '8000'
    Write-Host -NoNewline "[deploy] waiting for service to come up"
    for ($i = 1; $i -le 60; $i++) {
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/config/status" `
                -UseBasicParsing -TimeoutSec 2 *>$null
            Write-Host ""
            Write-Host "[deploy] ✓ service ready on http://localhost:$port"
            return $true
        } catch {
            Write-Host -NoNewline "."
            Start-Sleep -Seconds 1
        }
    }
    Write-Host ""
    Write-Host "[deploy] ✗ service did not respond within 60s. Run '.\deploy.ps1 logs'." -ForegroundColor Red
    return $false
}

switch ($Command) {
    'up' {
        Test-Docker
        Initialize-Env
        Test-Port ([int](Get-EnvValue -Key 'WEB_PORT' -Default '8000'))
        Test-Port ([int](Get-EnvValue -Key 'MCP_PORT' -Default '9001'))
        Write-Host "[deploy] starting (first build takes 3–5 minutes)..."
        docker compose up -d --build
        if (-not (Wait-Healthy)) { exit 1 }
        $port = Get-EnvValue -Key 'WEB_PORT' -Default '8000'
        Write-Host ""
        Write-Host "  ➜  Open http://localhost:$port"
        Write-Host "  ➜  Click the ⚙ gear icon on first run to log in to Claude"
    }
    'rebuild' {
        Test-Docker
        Initialize-Env
        docker compose down
        docker compose build --no-cache
        Test-Port ([int](Get-EnvValue -Key 'WEB_PORT' -Default '8000'))
        Test-Port ([int](Get-EnvValue -Key 'MCP_PORT' -Default '9001'))
        docker compose up -d
        Wait-Healthy
    }
    'restart' {
        Test-Docker
        docker compose restart
        Wait-Healthy
    }
    'down' {
        Test-Docker
        docker compose down
        Write-Host "[deploy] stopped"
    }
    'logs' {
        docker compose logs -f --tail=100
    }
    'status' {
        docker compose ps
        Write-Host ""
        $webPort = Get-EnvValue -Key 'WEB_PORT' -Default '8000'
        $mcpPort = Get-EnvValue -Key 'MCP_PORT' -Default '9001'
        try {
            Invoke-WebRequest "http://127.0.0.1:$webPort/api/config/status" -UseBasicParsing -TimeoutSec 3 *>$null
            Write-Host "  ✓ web $webPort"
        } catch { Write-Host "  ✗ web $webPort unreachable" -ForegroundColor Red }
        try {
            Invoke-WebRequest "http://127.0.0.1:$mcpPort/health" -UseBasicParsing -TimeoutSec 3 *>$null
            Write-Host "  ✓ capcut-mcp $mcpPort"
        } catch { Write-Host "  ✗ capcut-mcp $mcpPort unreachable" -ForegroundColor Red }
    }
}
