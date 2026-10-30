# Phase 1–3 architecture

FRAME is a modular monolith. FastAPI owns the local API, SQLite persistence, and background scan coordinator. The React client talks only to that API. Imaging and ingestion functions do not depend on HTTP, and database rows store logical photographs separately from their source assets.

Pipeline: directory validation → recursive discovery → cheap file identity → metadata/decoding → RAW/rendered pairing → database upsert → cached thumbnail → gallery API.

Stable photo identity is a SHA-256 digest of the canonical parent path and normalized stem. Asset change detection uses canonical path, size, and nanosecond modification time; full RAW files are not hashed. Duplicate basenames in different directories remain distinct.

## Phase 1 limits

- RAW files are catalogued and paired, but RAW pixel decoding is optional and unavailable unless `rawpy` is installed.
- Browser sandboxing prevents returning a native absolute directory from a standard web folder input. The UI accepts a path typed or pasted by the user.
- EXIF extraction uses Pillow for decoded formats; ExifTool integration follows in hardening work.
- One scan runs at a time. Interrupted scans can be started again and unchanged assets are upserted without duplicate rows.

## Next phase

Phase 2 stores versioned technical measurements and their source signature in the existing photo row. Decision configuration is separate: changing thresholds recomputes recommendations from JSON metrics without decoding source images. An additive startup migration upgrades Phase 1 SQLite databases.

Measurements are resolution-bounded and include Laplacian variance, Tenengrad energy, regional detail distribution, luminance percentiles, per-channel and luminance clipping, robust residual noise, entropy, and dynamic range. Recommendations use multiple signals and reserve REJECT for independent severe evidence or an explicitly less-conservative policy. They do not claim subject focus or calibrated confidence.

Phase 3 adds cached content/perceptual features and normalized group membership tables. Candidate generation uses 16-bit pHash bands, preventing unconditional all-pairs comparisons; candidates are checked with pHash, dHash and HSV histogram intersection. Exact, near, burst and conservative similar-scene groups remain distinct.

Selection exposes quality-only, group-representative and diversity-aware greedy baselines. The diversity method uses an interpretable MMR-style quality-minus-redundancy utility with deterministic tie-breaking. Pins and exclusions are persisted hard constraints. Exports use `copy2`, collision-safe stable-ID suffixes and only report completion after every copy succeeds.

Learned semantic embeddings and calibrated profile weights remain Phase 4 work. Current scene grouping detects visual composition, not semantic identity.

Phase 5 adds a bounded impact-aware review queue and local pairwise preference profiles. The Bradley–Terry-style logistic learner uses six existing, interpretable features with L2 regularisation and deterministic optimisation. It refuses to claim learning below three informative comparisons, keeps profiles isolated, and caps preference influence in selection.
