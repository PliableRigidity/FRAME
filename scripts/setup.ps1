$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
python -m pip install -e "$Root\backend[dev]"
Push-Location "$Root\frontend"
try { npm install } finally { Pop-Location }
Write-Host 'FRAME dependencies installed.' -ForegroundColor Green
