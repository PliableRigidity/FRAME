$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$WheelDir = Join-Path $Root 'release\wheels'
New-Item -ItemType Directory -Force -Path $WheelDir | Out-Null
Get-ChildItem -LiteralPath $WheelDir -Filter '*.whl' -File | Remove-Item -Force
Push-Location (Join-Path $Root 'frontend')
try { npm ci; npm run build } finally { Pop-Location }
$StaticDir = Join-Path $Root 'backend\frame\static'
if (Test-Path -LiteralPath $StaticDir) { Remove-Item -LiteralPath $StaticDir -Recurse -Force }
Copy-Item -LiteralPath "$Root\frontend\dist" -Destination $StaticDir -Recurse
python -m pip wheel "$Root\backend" --no-deps --wheel-dir $WheelDir
Compress-Archive -Path "$Root\frontend\dist","$Root\scripts","$Root\README.md","$Root\docs","$Root\release\wheels" -DestinationPath "$Root\release\FRAME-1.0.0-windows.zip" -Force
Write-Host "Created release\FRAME-1.0.0-windows.zip" -ForegroundColor Green
