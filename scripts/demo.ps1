# The demo on one Windows laptop: the server, the hub with the demo scenarios, and the Dashboard.
# The same as scripts/demo.sh, for Windows PowerShell 5.1 or PowerShell 7.
#
#   .\scripts\demo.ps1 up               fresh demo home, then server + hub + Dashboard; Ctrl-C stops all three
#   .\scripts\demo.ps1 up -KeepDb       keep data\hub\app.db (for example scenario 3 after scenario 1 earlier)
#   .\scripts\demo.ps1 up -NoWeb        without the Dashboard
#   .\scripts\demo.ps1 play 1           scenario 1 (new_control), then pass or fail; 3 is one_press, demo is 1 then 3
#   .\scripts\demo.ps1 play 3 -Speed 2
#
# If Windows refuses to run scripts: powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 up
# The first `up` asks for a new app password and keeps only its hash in data\demo.env (git-ignored, C-10).
# `play` asks for that password (or reads JUDGE_PASSWORD). The hand comes from settings\hub.yaml:
# `hand: script` records the presses, `hand: linear_actuators` drives the Arduino (serial_port: COM3 or the
# like, see firmware\README.md).
param(
    [Parameter(Position = 0)][string]$Command,
    [Parameter(Position = 1)][string]$Which,
    [switch]$KeepDb,
    [switch]$NoWeb,
    [double]$Speed = 0
)
$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')
$WebDir = if ($env:WEB_DIR) { $env:WEB_DIR } else { '..\live-demo' }
$Port = if ($env:PORT) { $env:PORT } else { '8000' }
$EnvFile = if ($env:DEMO_ENV) { $env:DEMO_ENV } else { 'data\demo.env' }
$LogDir = 'data\demo'
$env:PYTHONUTF8 = '1'  # logs and settings in UTF-8, not the console's code page (cp949)

function Read-DemoEnv {
    # KEY=value lines; the value may be in single quotes (this script) or bash %q-escaped (demo.sh)
    foreach ($line in Get-Content $EnvFile) {
        if ($line -notmatch '^([A-Z_]+)=(.*)$') { continue }
        $key, $v = $Matches[1], $Matches[2]  # before the next -match replaces $Matches
        if ($v -match "^'(.*)'$") { $v = $Matches[1] } else { $v = $v -replace '\\(.)', '$1' }
        Set-Item -Path "Env:$key" -Value $v
    }
}

function New-DemoEnv {
    Write-Host "First run: choose the app password for the demo (only its hash is kept in $EnvFile)."
    $hash = & uv run backend hash-password | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0 -or -not $hash) { throw 'no password hash' }
    $hash = "$hash".Trim()
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    Set-Content -Path $EnvFile -Encoding ASCII -Value @("HUB_TOKEN=$token", "APP_PASSWORD_HASH='$hash'")
}

function Start-Part($Name, $File, $ArgList, $Dir = (Get-Location).Path) {
    # Two log files: Start-Process cannot send stdout and stderr to one file
    $logs = (Resolve-Path $LogDir).Path  # absolute: the Dashboard starts in another folder
    Start-Process -FilePath $File -ArgumentList $ArgList -WorkingDirectory $Dir -NoNewWindow -PassThru `
        -RedirectStandardOutput "$logs\$Name.log" -RedirectStandardError "$logs\$Name.err.log"
}

function Stop-Parts($Parts) {
    # uv and npx start the real programs as children: /T stops the whole tree
    foreach ($p in $Parts) {
        if ($p -and -not $p.HasExited) {
            # its stderr ("not found" for a child that already ended) must not stop the cleanup
            try { & taskkill /PID $p.Id /T /F *> $null } catch { }
        }
    }
}

function Test-Server {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$Port/healthz" | Out-Null; $true }
    catch { $false }
}

function Up {
    New-Item -ItemType Directory -Force -Path data, $LogDir | Out-Null
    if (-not (Test-Path $EnvFile)) { New-DemoEnv }
    Read-DemoEnv

    if (-not $KeepDb) {
        Remove-Item -Force -ErrorAction SilentlyContinue data\hub\app.db, data\hub\app.db-wal, data\hub\app.db-shm
    }
    if (-not (Test-Path data\hub\app.db)) {
        & uv run backend seed-demo
        if ($LASTEXITCODE -ne 0) { throw 'seed-demo failed' }
    }

    $parts = @()
    try {
        $parts += Start-Part 'server' 'uv' @('run', 'backend', 'server', '--port', $Port)
        for ($i = 0; $i -lt 50 -and -not (Test-Server); $i++) { Start-Sleep -Milliseconds 200 }
        if (-not (Test-Server)) { throw "the server did not start: $LogDir\server.log, $LogDir\server.err.log" }

        $parts += Start-Part 'hub' 'uv' @('run', 'backend', 'hub', '--stdout', '--demo-scenarios',
            '--server-url', "ws://127.0.0.1:$Port/ws/hub")

        $web = -not $NoWeb
        if ($web) {
            if (Test-Path "$WebDir\node_modules") {
                $env:VITE_MOCK = 'off'  # what `npm run dev:real` sets, which cmd.exe cannot
                $parts += Start-Part 'web' 'npx.cmd' @('vite', '--strictPort') (Resolve-Path $WebDir).Path
            } else {
                Write-Warning "no $WebDir\node_modules: run npm install there, or use -NoWeb"
                $web = $false
            }
        }

        Start-Sleep -Seconds 2
        if ($parts | Where-Object { $_.HasExited }) { throw "something stopped at start; see $LogDir\*.log" }
        $hand = (Select-String -Path settings\hub.yaml -Pattern '^hand:\s*([a-z_]+)').Matches[0].Groups[1].Value
        Write-Host "server  http://127.0.0.1:$Port   (log $LogDir\server.log)"
        Write-Host "hub     hand: $hand   (log $LogDir\hub.log; data\hub\hub.log is not used with --stdout)"
        if ($web) { Write-Host "web     http://localhost:5173   (log $LogDir\web.log)" }
        Write-Host 'start a scenario in another window: .\scripts\demo.ps1 play 1   (then play 3)'
        Write-Host 'Ctrl-C stops everything'
        while (-not ($parts | Where-Object { $_.HasExited })) { Start-Sleep -Seconds 1 }
        Write-Warning "a part stopped; see $LogDir\*.log"
    } finally {
        Write-Host 'stopping'
        Stop-Parts $parts
    }
}

function Play {
    $name = switch ($Which) {
        '1' { 'new_control' }
        '3' { 'one_press' }
        { $_ -in 'demo', 'new_control', 'one_press', 'leave_home' } { $Which }
        default { Write-Error 'play 1, play 3 or play demo'; exit 2 }
    }
    $a = @('run', 'backend', 'judge', '--url', "http://127.0.0.1:$Port", '--scenario', $name)
    if ($Speed -gt 0) { $a += @('--speed', $Speed.ToString([cultureinfo]::InvariantCulture)) }
    & uv @a
    exit $LASTEXITCODE
}

switch ($Command) {
    'up' { Up }
    'play' { Play }
    default { Get-Content $PSCommandPath | Select-Object -Skip 1 -First 13 | ForEach-Object { $_ -replace '^# ?', '' }; exit 2 }
}
