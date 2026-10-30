$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$env:FRAME_DATA_DIR = Join-Path $Root '.frame-data'
python -m frame.launcher
