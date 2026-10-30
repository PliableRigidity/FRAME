# Installation

## Core Windows installation

Install Python 3.12+ and Node.js 20+, clone FRAME, then run `scripts\setup.ps1`. Launch later with `scripts\launch.ps1`; it binds only to `127.0.0.1`, opens the browser, and serves both API and built UI from one Python process.

The core is CPU-only and offline after installation. Optional RAW preview support is `python -m pip install -e ".\backend[raw]"`. Optional ONNX AI inference is `python -m pip install -e ".\backend[ai]"`, followed by manual installation of reviewed weights described in `models.md`. CUDA use additionally requires an ONNX Runtime GPU build compatible with the local NVIDIA driver.

`scripts\build-release.ps1` reproducibly builds the frontend, Python wheel and a Windows ZIP. It is a portable release bundle, not a signed installer.
