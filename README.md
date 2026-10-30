# FRAME

FRAME is a local-first photo culling application. Phases 1–2 provide recursive discovery, RAW/JPEG pairing, SQLite persistence, thumbnails, background progress and conventional technical-quality analysis. Originals are opened read-only and never changed.

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

## Technical analysis

Rendered images are measured during import for Laplacian and Tenengrad sharpness, regional detail, luminance distribution, clipping, dynamic range, entropy, and a texture-discounted noise estimate. Results include conservative KEEP/REVIEW/REJECT recommendations and explanations. The displayed index is heuristic, not a probability.

Quality rules are available at `GET /api/quality/config` and can be changed with `PUT /api/quality/config`. Rule changes reuse stored measurements. To evaluate against human labels, prepare a CSV with `path,label` and run:

```powershell
python benchmarks/evaluate_quality.py labels.csv --output report.json
```

No representative real-world labelled set is bundled, so calibration remains incomplete.

## Similarity and collection selection

Phase 3 caches SHA-256 file hashes, pHash, dHash and compact HSV histograms. Exact duplicates use content hashes. Near-duplicate and scene candidates come from pHash band buckets and are verified with hash distance plus colour similarity. Bursts additionally require close capture times. These lightweight groups are intentionally conservative and are not semantic understanding.

The collection builder supports quality-only, group-first and diversity-aware MMR-style selection. General, Travel, Portrait, Landscape and Sports profiles are documented heuristic weights. Pins and exclusions are hard constraints. Selected JPEGs or RAW/JPEG pairs can be exported through `/api/selection/export`; originals are copied with metadata and never modified.

Benchmark an existing database with:

```powershell
python benchmarks/evaluate_selection.py .frame-data/frame.db --target 50
```

## Optional learned scoring

FRAME supports checksum-verified local scalar-output ONNX IQA models, CPU/CUDA provider selection, batch inference, cancellation, cached results and classical/learned/hybrid ranking. It never downloads weights during ordinary use. See [docs/models.md](docs/models.md). No pretrained weights are bundled in this release, so classical mode is the default.

## Review and personalisation

FRAME labels its review-priority number as a heuristic, combining selection-boundary proximity, missing/disagreeing evidence and uniqueness. A user-set budget bounds the queue. Pairwise A/B/equal/skip feedback trains a deterministic regularised logistic ranking model after three informative examples. Profiles, history, undo/reset and model versions remain local in SQLite. Personal preference contributes at most 20% of ranking quality and cannot override pins, exclusions or target size.

No preference improvement is claimed without held-out human labels. `benchmarks/evaluate_preferences.py` accepts feature-level labelled examples and reports held-out agreement.
