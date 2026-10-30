from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from .models import Photo


def safe_export(photos: list[Photo], destination: Path, mode: str = "jpeg", metadata: str = "json") -> dict:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    copied, errors = [], []
    for photo in photos:
        assets = [a for a in photo.assets if mode == "pairs" or a.kind == "rendered"]
        for asset in assets:
            source = Path(asset.source_path)
            if destination == source.parent or destination.is_relative_to(source.parent):
                errors.append({"path": str(source), "error": "Destination cannot be inside a source directory"}); continue
            target = destination / source.name
            if target.exists():
                suffix = photo.stable_id[:8]
                target = destination / f"{source.stem}-{suffix}{source.suffix}"
            if target.exists():
                errors.append({"path": str(source), "error": "Collision-safe target already exists"}); continue
            try:
                shutil.copy2(source, target)
                copied.append({"photo_id": photo.id, "source": str(source), "destination": str(target)})
            except OSError as exc:
                errors.append({"path": str(source), "error": str(exc)})
    report = {"copied": copied, "errors": errors, "complete": not errors}
    if metadata == "json":
        (destination / "frame-selection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    elif metadata == "csv":
        with (destination / "frame-selection.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["photo_id", "source", "destination"]); writer.writeheader(); writer.writerows(copied)
    return report
