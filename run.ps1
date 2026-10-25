$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $Root '.env'))) { Copy-Item (Join-Path $Root '.env.example') (Join-Path $Root '.env') }
if (-not (Test-Path (Join-Path $Root '.venv\Scripts\python.exe'))) { py -3 -m venv (Join-Path $Root '.venv') }
& (Join-Path $Root '.venv\Scripts\python.exe') -m pip install -r (Join-Path $Root 'requirements.txt')
if (-not (Test-Path (Join-Path $Root 'frontend\node_modules'))) { npm --prefix (Join-Path $Root 'frontend') ci }
$env:FLOWFORGE_HOST = '127.0.0.1'
Write-Host 'Starting FlowForge API on http://127.0.0.1:8000 and UI on http://127.0.0.1:5173'
$api = Start-Process -FilePath (Join-Path $Root '.venv\Scripts\python.exe') -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory (Join-Path $Root 'backend') -PassThru -WindowStyle Hidden
try { npm --prefix (Join-Path $Root 'frontend') run dev } finally { Stop-Process -Id $api.Id -ErrorAction SilentlyContinue }
