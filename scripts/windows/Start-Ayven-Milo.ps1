param([switch]$Setup, [string]$MiloExecutable)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Data = Join-Path $env:LOCALAPPDATA 'AyvenEngine'
New-Item -ItemType Directory -Force -Path $Data | Out-Null
$Python = Join-Path $Data 'venv\Scripts\python.exe'
if ($Setup) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install Python 3.12 from python.org, including the Python launcher, then run Setup again.' }
    & py -3.12 -m venv (Join-Path $Data 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required. Install it and run Setup again.' }
    & $Python -m pip install -r (Join-Path $Root 'apps\api\requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Engine dependency installation failed. Run Setup again after checking the error.' }
}
if (-not (Test-Path $Python)) { throw 'Run Setup.cmd first. Your engine environment is missing.' }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw 'Install Ollama from ollama.com, reopen this window, and run Setup.cmd.' }
$env:OLLAMA_NO_CLOUD = '1'
try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 }
catch {
    Start-Process 'ollama' -ArgumentList 'serve' -RedirectStandardOutput (Join-Path $Data 'ollama.log') -RedirectStandardError (Join-Path $Data 'ollama-error.log') | Out-Null
    $Ready = $false
    for ($i=0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2; $Ready=$true; break } catch {}
    }
    if (-not $Ready) { throw 'Ollama did not start. Open Ollama and try again.' }
}
if ($Setup) {
    & ollama pull qwen3:4b
    if ($LASTEXITCODE -ne 0) { throw 'Local Qwen download failed. Check your connection and run Setup again. No paid fallback is enabled.' }
}
$Models = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
if (-not ($Models.models | Where-Object { $_.name -eq 'qwen3:4b' })) { throw 'Local qwen3:4b is missing. Run Setup.cmd to download it.' }
$KeyPath = Join-Path $Data 'engine-key.txt'
if (-not (Test-Path $KeyPath)) {
    $Bytes = New-Object byte[] 32
    $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
    $Random.GetBytes($Bytes); $Random.Dispose()
    [Convert]::ToBase64String($Bytes) | Set-Content -NoNewline -Path $KeyPath
}
$Key = (Get-Content -Raw $KeyPath).Trim()
$env:AYVEN_API_TOKEN = $Key
$env:AYVEN_DB = Join-Path $Data 'ayven.db'
$WorkflowPath = (Join-Path $Data 'workflows.sqlite').Replace('\','/')
$env:AYVEN_WORKFLOW_DATABASE_URL = 'sqlite:///' + $WorkflowPath
$env:AYVEN_DURABLE = '1'
$env:AYVEN_PUBLIC_MODE = '0'
$env:AYVEN_LLM_STUB = '0'
$env:AYVEN_RESEARCH_MODE = 'live'
$env:AYVEN_USE_QWEN_AGENT = '0'
$env:AYVEN_ALLOW_ESCALATION = '0'
$env:AYVEN_LOCAL_LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:AYVEN_LOCAL_LLM_API_KEY = ''
$env:AYVEN_PROVIDER_FORMAT = 'json_object'
$env:AYVEN_EMPLOYEE_MODEL = 'qwen3:4b'
$env:AYVEN_SUPERVISOR_MODEL = 'qwen3:4b'
$env:AYVEN_MANAGER_MODEL = 'qwen3:4b'
$Headers = @{ Authorization = "Bearer $Key" }
$Existing = $false
try {
    $Health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2
    if ($Health.runtime -ne 'durable') { throw 'Port 8000 is occupied by a different engine. Stop it before launching.' }
    $null = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/projects' -Headers $Headers -TimeoutSec 2
    $Existing = $true
} catch {
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8000 is in use and could not authenticate to this engine. Stop that service first.' }
}
if (-not $Existing) {
    $Arguments = @('-m','uvicorn','app.main:app','--app-dir', ('"' + (Join-Path $Root 'apps\api') + '"'),'--host','127.0.0.1','--port','8000')
    $Process = Start-Process $Python -ArgumentList $Arguments -WorkingDirectory $Root -PassThru -RedirectStandardOutput (Join-Path $Data 'engine.log') -RedirectStandardError (Join-Path $Data 'engine-error.log')
    $Process.Id | Set-Content (Join-Path $Data 'engine.pid')
    $Ready = $false
    for ($i=0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try { $null = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/projects' -Headers $Headers -TimeoutSec 2; $Ready=$true; break } catch {}
    }
    if (-not $Ready) { throw "Engine did not become ready. Read $Data\engine-error.log." }
}
$env:MILO_ENGINE_ENABLED = '1'
$env:AYVEN_ENGINE_BASE_URL = 'http://127.0.0.1:8000'
$env:AYVEN_ENGINE_TOKEN = $Key
$env:MILO_MODEL_PROVIDER = 'ollama'
$env:MILO_MODEL_NAME = 'qwen3:4b'
$env:MILO_MODEL_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:MILO_MODEL_API_KEY = ''
$env:MILO_BRAIN_SHADOW_MODE = 'off'
$env:MILO_DESKTOP_API_MODE = 'local'
$env:MILO_DEPLOYMENT_MODE = 'local'
$env:MILO_SERVER_HOST = '127.0.0.1'
$env:MILO_STORAGE_BACKEND = 'sqlite'
$env:MILO_REQUIRE_DEVICE_AUTH = '0'
$env:ANTHROPIC_API_KEY = ''
$env:OPENAI_API_KEY = ''
$env:TWILIO_ACCOUNT_SID = ''
$env:TWILIO_AUTH_TOKEN = ''
$env:TWILIO_WHATSAPP_NUMBER = ''
if (-not $MiloExecutable) { $MiloExecutable = Join-Path (Split-Path -Parent $Root) 'Milo\Milo.exe' }
if (Test-Path $MiloExecutable) {
    Start-Process $MiloExecutable | Out-Null
    Write-Host 'Ayven is ready and Milo is starting. Use Projects to submit a task.'
} else {
    Write-Host "Ayven is ready locally. Milo executable not found at $MiloExecutable. Pass -MiloExecutable with its full path."
}
Write-Host "Persistent engine data and logs: $Data"
Write-Host 'No paid model fallback or frontier escalation is enabled. Use this launcher for the local configuration.'
