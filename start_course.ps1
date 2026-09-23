# Start local course AI assistant (all local: Ollama + Whisper; no cloud).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1) Ollama must be running (localhost:11434 only)
try {
    Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null
} catch {
    Write-Host "[start] starting ollama serve..."
    Start-Process -FilePath "D:\Ollama\ollama.exe" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

# 2) Model configuration (RTX 5070 12GB: VLM during class; 27B only after class).
#    Only set env defaults here — do NOT invent FUSION_MODEL overrides.
#    Python settings.py owns REALTIME_FUSION_MODEL / FINAL_MODEL roles.
$env:VISION_MODEL         = if ($env:VISION_MODEL) { $env:VISION_MODEL } else { "qwen3-vl:8b" }
$env:FALLBACK_VISION_MODEL = if ($env:FALLBACK_VISION_MODEL) { $env:FALLBACK_VISION_MODEL } else { "qwen3-vl:4b" }
$env:REALTIME_FUSION_MODEL = if ($env:REALTIME_FUSION_MODEL) { $env:REALTIME_FUSION_MODEL } else { $env:VISION_MODEL }
$env:FINAL_MODEL          = if ($env:FINAL_MODEL) { $env:FINAL_MODEL } else { "qwen38-27b-main:latest" }
$env:WHISPER_MODEL        = if ($env:WHISPER_MODEL) { $env:WHISPER_MODEL } else { "turbo" }
$env:WHISPER_LANGUAGE     = "zh"
$env:WHISPER_DEVICE      = if ($env:WHISPER_DEVICE) { $env:WHISPER_DEVICE } else { "auto" }
# Round 10 observe-only instrumentation (default off; set true for gap analysis).
$env:INSTRUMENTATION     = if ($env:INSTRUMENTATION) { $env:INSTRUMENTATION } else { "false" }

# Print effective roles (must match docs/MODEL_LIFECYCLE_ARCHITECTURE.md)
Write-Host "[start] VISION_MODEL=$env:VISION_MODEL"
Write-Host "[start] REALTIME_FUSION_MODEL=$env:REALTIME_FUSION_MODEL"
Write-Host "[start] FINAL_MODEL=$env:FINAL_MODEL"
Write-Host "[start] INSTRUMENTATION=$env:INSTRUMENTATION"

# 3) Launch course app (venv python)
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[start] venv missing; creating..."
    py -3.12 -m venv (Join-Path $root ".venv")
    & $py -m pip install --upgrade pip -q
    & $py -m pip install -r (Join-Path $root "requirements.txt") -q
}

Write-Host "[start] launching course session GUI..."
Set-Location $root
& $py -m app.course_session
