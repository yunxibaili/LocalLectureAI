$models = @('qwen3-vl:4b','qwen2.5vl:7b','qwen2.5:7b')
foreach ($m in $models) {
  $ok = $false
  for ($i = 1; $i -le 20; $i++) {
    & D:\Ollama\ollama.exe pull $m *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Start-Sleep -Seconds 10
  }
  if ($ok) { Add-Content -Path "$PSScriptRoot\pull_status.log" -Value "OK $m $(Get-Date -Format o)" }
  else { Add-Content -Path "$PSScriptRoot\pull_status.log" -Value "FAIL $m $(Get-Date -Format o)" }
}
Add-Content -Path "$PSScriptRoot\pull_status.log" -Value "DONE $(Get-Date -Format o)"
