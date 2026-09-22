# Stop the course session gracefully (asks the running app to finalize).
# If the GUI is still open, prefer clicking「结束课程」in the app.
# This script: (1) reads sessions/current_session.json (active marker),
#              (2) writes STOP_REQUEST into THAT session dir,
#              (3) falls back to newest session dir only if marker missing.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$sessions = Join-Path $root "sessions"
if (-not (Test-Path $sessions)) { Write-Host "No sessions directory."; exit 1 }

$target = $null
$marker = Join-Path $sessions "current_session.json"
if (Test-Path $marker) {
    try {
        $j = Get-Content -LiteralPath $marker -Raw -Encoding utf8 | ConvertFrom-Json
        if ($j.session_path -and (Test-Path -LiteralPath $j.session_path)) {
            $target = Get-Item -LiteralPath $j.session_path
        } elseif ($j.session_dir) {
            $cand = Join-Path $sessions $j.session_dir
            if (Test-Path -LiteralPath $cand) { $target = Get-Item -LiteralPath $cand }
        }
    } catch {
        Write-Host "[stop] marker unreadable: $_"
    }
    if (-not $target) {
        Write-Host "[stop] current_session.json present but target missing; falling back to newest dir."
    }
}

if (-not $target) {
    $latest = Get-ChildItem $sessions -Directory |
        Where-Object { $_.Name -notlike '_*' } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { Write-Host "No session found."; exit 1 }
    Write-Host "[stop] WARNING: no active marker; using newest dir $($latest.Name)"
    $target = $latest
}

$flag = Join-Path $target.FullName "STOP_REQUEST"
Set-Content -Path $flag -Value (Get-Date -Format o) -Encoding utf8
Write-Host "[stop] STOP_REQUEST written: $flag"
Write-Host "[stop] If the course app is open, it will finalize transcript + final_summary.md."
