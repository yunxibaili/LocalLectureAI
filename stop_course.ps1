# Stop the course session gracefully (asks the running app to finalize).
# If the GUI is still open, prefer clicking「结束课程」in the app.
# This script: (1) writes STOP_REQUEST to the newest active session,
#              (2) if no session found, reports instructions.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$sessions = Join-Path $root "sessions"
if (-not (Test-Path $sessions)) { Write-Host "No sessions directory."; exit 1 }

$latest = Get-ChildItem $sessions -Directory | Sort-Object Name -Descending | Select-Object -First 1
if (-not $latest) { Write-Host "No session found."; exit 1 }

$flag = Join-Path $latest.FullName "STOP_REQUEST"
Set-Content -Path $flag -Value (Get-Date -Format o) -Encoding utf8
Write-Host "[stop] STOP_REQUEST written: $flag"
Write-Host "[stop] If the course app is open, click 「结束课程」 to finalize transcript + final_summary.md."
