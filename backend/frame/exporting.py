from __future__ import annotations

import csv
import json
import shutil
import hashlib
from dataclasses import asdict, dataclass, field
from threading import Event, Lock, Thread
from pathlib import Path

from .models import Photo


def safe_export(photos: list[Photo], destination: Path, mode: str = "jpeg", metadata: str = "json") -> dict:
    destination = validate_export_destination(destination, photos)
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
                copied.append({"photo_id": photo.id, "source": str(source), "destination": str(target), "sha256": _sha256(target)})
            except OSError as exc:
                errors.append({"path": str(source), "error": str(exc)})
    report = {"copied": copied, "errors": errors, "complete": not errors}
    if metadata == "json":
        (destination / "frame-selection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    elif metadata == "csv":
        with (destination / "frame-selection.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["photo_id", "source", "destination"]); writer.writeheader(); writer.writerows(copied)
    return report


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


def validate_export_destination(destination: Path, photos: list[Photo]) -> Path:
    if "\0" in str(destination): raise ValueError("Invalid export destination")
    resolved = destination.expanduser().resolve()
    if resolved == Path(resolved.anchor): raise ValueError("Export destination cannot be a filesystem root")
    for photo in photos:
        for asset in photo.assets:
            source_parent = Path(asset.source_path).resolve().parent
            if resolved == source_parent or resolved.is_relative_to(source_parent):
                raise ValueError("Export destination cannot be inside a source directory")
    return resolved


class ExportCoordinator:
    def __init__(self, factory):
        self.factory, self.lock, self.cancelled = factory, Lock(), Event()
        self.state = {"state": "idle", "completed": 0, "total": 0, "errors": [], "destination": None}

    def status(self):
        with self.lock: return dict(self.state)

    def start(self, destination: Path, mode: str, metadata: str):
        with self.lock:
            if self.state["state"] == "running": raise RuntimeError("An export is already running")
            self.state = {"state": "running", "completed": 0, "total": 0, "errors": [], "destination": str(destination)}; self.cancelled = Event()
        Thread(target=self._run, args=(destination, mode, metadata), daemon=True, name="frame-export").start(); return self.status()

    def cancel(self): self.cancelled.set(); return self.status()

    def _run(self, destination: Path, mode: str, metadata: str):
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from .models import Photo
        copied = []
        try:
            with self.factory() as session: photos = list(session.scalars(select(Photo).options(selectinload(Photo.assets)).where(Photo.selected.is_(True)).order_by(Photo.selection_rank)))
            destination = validate_export_destination(destination, photos); destination.mkdir(parents=True, exist_ok=True)
            work = [(photo, asset) for photo in photos for asset in photo.assets if mode == "pairs" or asset.kind == "rendered"]
            with self.lock: self.state["total"] = len(work)
            for photo, asset in work:
                if self.cancelled.is_set(): break
                source = Path(asset.source_path); target = destination / source.name
                if target.exists(): target = destination / f"{source.stem}-{photo.stable_id[:8]}{source.suffix}"
                try:
                    if target.exists(): raise FileExistsError("Collision-safe target already exists")
                    shutil.copy2(source, target); copied.append({"photo_id": photo.id, "source": str(source), "destination": str(target), "sha256": _sha256(target)})
                except OSError as exc:
                    with self.lock: self.state["errors"].append({"path": str(source), "error": str(exc)})
                with self.lock: self.state["completed"] += 1
            manifest = {"copied": copied, "errors": self.state["errors"], "cancelled": self.cancelled.is_set()}
            (destination / "frame-export-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            with self.lock: self.state["state"] = "cancelled" if self.cancelled.is_set() else "partial" if self.state["errors"] else "completed"
        except Exception as exc:
            with self.lock: self.state["state"] = "failed"; self.state["errors"].append({"error": str(exc)})
