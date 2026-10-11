$ErrorActionPreference = "Stop"

function Receive-File([string]$url, [string]$dest) {
  $partial = "$dest.partial"
  foreach ($attempt in 1..6) {
    $curlArgs = @("-fL", "--retry", "3", "--retry-all-errors", "--retry-delay", "2", "-o", $partial, $url)
    if (Test-Path $partial) {
      $curlArgs = @("-fL", "--retry", "3", "--retry-all-errors", "--retry-delay", "2", "-C", "-", "-o", $partial, $url)
    }
    $proc = Start-Process -FilePath "curl.exe" -ArgumentList $curlArgs -PassThru -NoNewWindow
    $last = -1
    $stalls = 0
    while (-not $proc.HasExited) {
      Start-Sleep -Seconds 10
      $len = 0
      if (Test-Path $partial) { $len = (Get-Item $partial).Length }
      if ($len -eq $last) { $stalls++ } else { $stalls = 0; $last = $len }
      if ($stalls -ge 8) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
        break
      }
    }
    if ($proc.HasExited -and $proc.ExitCode -eq 0 -and (Test-Path $partial) -and (Get-Item $partial).Length -gt 1000000) {
      Move-Item -Force $partial $dest
      return
    }
  }
  throw "ollama zip download failed"
}

$env:OLLAMA_NO_GPU = "1"
$env:OLLAMA_MODELS = Join-Path $env:USERPROFILE ".ollama\models"
$env:OLLAMA_HOST = "127.0.0.1:11434"
if ($env:GITHUB_ENV) {
  Add-Content -Path $env:GITHUB_ENV -Value "OLLAMA_NO_GPU=1"
  Add-Content -Path $env:GITHUB_ENV -Value "OLLAMA_MODELS=$($env:OLLAMA_MODELS)"
  Add-Content -Path $env:GITHUB_ENV -Value "OLLAMA_HOST=127.0.0.1:11434"
}

& "$PSScriptRoot\fetch-qwen-blobs.ps1" -Tags @("4b", "8b")

$appRoot = Join-Path $env:USERPROFILE ".ollama-app"
New-Item -ItemType Directory -Force -Path $appRoot | Out-Null
$ollama = Get-ChildItem -Path $appRoot -Recurse -Filter ollama.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $ollama) {
  $zip = Join-Path $env:RUNNER_TEMP "ollama-windows-amd64.zip"
  Receive-File "https://github.com/ollama/ollama/releases/download/v0.40.2/ollama-windows-amd64.zip" $zip
  tar -xf $zip -C $appRoot
  $ollama = Get-ChildItem -Path $appRoot -Recurse -Filter ollama.exe | Select-Object -First 1
}
if (-not $ollama) { throw "ollama.exe was not extracted" }

Get-Process | Where-Object { $_.ProcessName -match 'ollama' } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Process -FilePath $ollama.FullName -ArgumentList "serve" -WorkingDirectory $ollama.DirectoryName -WindowStyle Hidden
$ready = $false
foreach ($try in 1..30) {
  try {
    Invoke-RestMethod http://127.0.0.1:11434/api/tags | Out-Null
    $ready = $true
    break
  } catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) { throw "Ollama did not start" }
$names = @((Invoke-RestMethod http://127.0.0.1:11434/api/tags).models | ForEach-Object { $_.name })
if ($names -notcontains "qwen3:4b") { throw "qwen3:4b is not loaded" }
if ($names -notcontains "qwen3:8b") { throw "qwen3:8b is not loaded" }
try {
  Invoke-RestMethod http://127.0.0.1:11434/v1/models | Out-Null
} catch {
  throw "Ollama is not available at http://127.0.0.1:11434/v1"
}
Write-Output "PASS ollama"
