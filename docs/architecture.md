# Phase 1 architecture

FRAME is a modular monolith. FastAPI owns the local API, SQLite persistence, and background scan coordinator. The React client talks only to that API. Imaging and ingestion functions do not depend on HTTP, and database rows store logical photographs separately from their source assets.

Pipeline: directory validation → recursive discovery → cheap file identity → metadata/decoding → RAW/rendered pairing → database upsert → cached thumbnail → gallery API.

Stable photo identity is a SHA-256 digest of the canonical parent path and normalized stem. Asset change detection uses canonical path, size, and nanosecond modification time; full RAW files are not hashed. Duplicate basenames in different directories remain distinct.

## Phase 1 limits

- RAW files are catalogued and paired, but RAW pixel decoding is optional and unavailable unless `rawpy` is installed.
- Browser sandboxing prevents returning a native absolute directory from a standard web folder input. The UI accepts a path typed or pasted by the user.
- EXIF extraction uses Pillow for decoded formats; ExifTool integration follows in hardening work.
- One scan runs at a time. Interrupted scans can be started again and unchanged assets are upserted without duplicate rows.

## Next phase

Technical analysis will add separately cached sharpness, exposure, clipping, contrast, and noise metrics. Recommendations will remain conservative and explanations will retain the underlying measurements.
