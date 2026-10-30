$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Start-Process python -ArgumentList '-m','uvicorn','frame.main:app','--app-dir',"$Root\backend",'--reload' -PassThru -WindowStyle Hidden
try {
  Push-Location "$Root\frontend"
  npm run dev
} finally {
  Pop-Location
  Stop-Process -Id $Backend.Id -ErrorAction SilentlyContinue
}
