# Download qwen3 blobs into the Ollama models directory. A blob already at the
# expected size is left in place, so a restored cache is not fetched again.
param(
  [string[]]$Tags = @("4b", "8b")
)

$ErrorActionPreference = "Stop"
$models = Join-Path $env:USERPROFILE ".ollama\models"
$blobDir = Join-Path $models "blobs"
New-Item -ItemType Directory -Force -Path $blobDir | Out-Null
$registry = "https://registry.ollama.ai/v2/library/qwen3"
Write-Host "models dir $models"

foreach ($tag in $Tags) {
  $manifestPath = Join-Path $models "manifests\registry.ollama.ai\library\qwen3\$tag"
  New-Item -ItemType Directory -Force -Path (Split-Path $manifestPath) | Out-Null
  if (-not (Test-Path $manifestPath) -or (Get-Item $manifestPath).Length -lt 50) {
    Write-Host "fetching manifest $tag"
    & curl.exe -fL --retry 5 --retry-all-errors --retry-delay 2 -H "Accept: application/vnd.docker.distribution.manifest.v2+json" -o $manifestPath "$registry/manifests/$tag"
    if ($LASTEXITCODE -ne 0) { throw "manifest download failed for $tag" }
  }
  $manifest = Get-Content -Raw $manifestPath | ConvertFrom-Json
  $parts = New-Object System.Collections.Generic.List[object]
  $parts.Add([pscustomobject]@{ digest = [string]$manifest.config.digest; size = [int64]$manifest.config.size })
  foreach ($layer in @($manifest.layers)) {
    $parts.Add([pscustomobject]@{ digest = [string]$layer.digest; size = [int64]$layer.size })
  }
  foreach ($part in $parts) {
    $name = ([string]$part.digest).Replace(":", "-")
    $dest = Join-Path $blobDir $name
    $expected = [int64]$part.size
    if ((Test-Path $dest) -and (Get-Item $dest).Length -eq $expected) {
      Write-Host "present $name $expected"
      continue
    }
    $partial = "$dest.partial"
    if ((Test-Path $partial) -and (Get-Item $partial).Length -gt $expected) {
      Remove-Item $partial -Force
    }
    $done = $false
    foreach ($attempt in 1..8) {
      Write-Host "attempt $attempt $tag $name expected $expected"
      $curlArgs = @("-fL", "--retry", "3", "--retry-all-errors", "--retry-delay", "2", "-o", $partial, "$registry/blobs/$($part.digest)")
      if (Test-Path $partial) { $curlArgs = @("-fL", "--retry", "3", "--retry-all-errors", "--retry-delay", "2", "-C", "-", "-o", $partial, "$registry/blobs/$($part.digest)") }
      $proc = Start-Process -FilePath "curl.exe" -ArgumentList $curlArgs -PassThru -NoNewWindow
      $last = -1
      $stalls = 0
      while (-not $proc.HasExited) {
        Start-Sleep -Seconds 15
        $len = 0
        if (Test-Path $partial) { $len = (Get-Item $partial).Length }
        Write-Host ("progress {0} {1:N0}/{2:N0}" -f $name, $len, $expected)
        if ($len -eq $last) { $stalls++ } else { $stalls = 0; $last = $len }
        if ($stalls -ge 8) {
          Write-Host "stalled $name, restarting"
          try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
          break
        }
      }
      if ((Test-Path $partial) -and (Get-Item $partial).Length -eq $expected) {
        Move-Item -Force $partial $dest
        Write-Host "saved $name"
        $done = $true
        break
      }
      $got = 0
      if (Test-Path $partial) { $got = (Get-Item $partial).Length }
      Write-Host "incomplete $name exit=$($proc.ExitCode) size=$got"
    }
    if (-not $done) { throw "failed to fetch $name" }
  }
  Write-Host "qwen3:$tag blobs ready"
}
