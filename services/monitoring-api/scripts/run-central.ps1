param([ValidateSet("api","worker","verify")][string]$Role="api")
$ErrorActionPreference="Stop"
$serviceRoot=Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $serviceRoot
$pythonPath=Join-Path $serviceRoot "../../.venv/Scripts/python.exe"
$env:MONITORING_STORAGE="supabase_compact"
$env:SHADOW_FORECAST_ENABLED="true"
$env:PYTHONDONTWRITEBYTECODE="1"
$env:EXPORT_DIR=Join-Path $env:TEMP "ffws-exports"
$env:WELANG_IMPORT_PATH=Join-Path $serviceRoot "imports/history"
if ($Role -eq "api") { & $pythonPath -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --no-access-log }
elseif ($Role -eq "worker") { & $pythonPath -m monitoring.worker run }
else { & $pythonPath -m monitoring.cloud_setup verify }
exit $LASTEXITCODE
