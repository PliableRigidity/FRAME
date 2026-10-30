# FRAME

FRAME is a local-first photo culling application. Phase 1 provides recursive photo discovery, RAW/JPEG pairing, SQLite persistence, cached thumbnails, background scan progress, cancellation, and a responsive gallery. Originals are opened read-only and never changed.

## Quick start (Windows PowerShell)

Requirements: Python 3.12+ and Node.js 20+.

```powershell
.\scripts\setup.ps1
.\scripts\dev.ps1
```

Open <http://127.0.0.1:5173>. The API is at <http://127.0.0.1:8000/docs>.

For backend-only use:

```powershell
python -m pip install -e ".\backend[dev]"
python -m uvicorn frame.main:app --app-dir backend --reload
```

## Data and safety

- The default database and thumbnail cache live in `.frame-data/` and are git-ignored.
- Source photographs are never modified, moved, or deleted.
- Supported preview formats are JPEG, PNG, and TIFF. CR2, NEF, ARW, and DNG are discovered and paired, but embedded RAW preview decoding requires the optional `rawpy` dependency and is not claimed in Phase 1.
- A matching RAW and rendered file are paired only when their normalized base name and parent directory match.
- Network paths work when they are mounted and readable by the current Windows user.

## Test

```powershell
python -m pytest backend/tests
cd frontend; npm test -- --run; npm run build
```

See [docs/architecture.md](docs/architecture.md) for design and known limitations.
