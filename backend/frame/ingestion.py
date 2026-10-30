from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable, Iterable
from PIL import ExifTags, Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from .models import Asset, Photo

RENDERED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
RAW_EXTENSIONS = {".cr2", ".nef", ".arw", ".dng"}
SUPPORTED_EXTENSIONS = RENDERED_EXTENSIONS | RAW_EXTENSIONS


@dataclass
class ImageMetadata:
    width: int | None = None
    height: int | None = None
    capture_time: datetime | None = None
    camera: str | None = None
    lens: str | None = None
    error: str | None = None


def validate_source(raw_path: str) -> Path:
    if not raw_path or "\0" in raw_path:
        raise ValueError("A valid directory path is required")
    source = Path(raw_path).expanduser().resolve()
    if not source.exists():
        raise ValueError("Directory does not exist or is not mounted")
    if not source.is_dir():
        raise ValueError("Source must be a directory")
    return source


def discover(source: Path) -> Iterable[Path]:
    for root, directories, files in os.walk(source, followlinks=False):
        directories[:] = [d for d in directories if d not in {".frame-data", ".git"}]
        for name in sorted(files):
            path = Path(root, name)
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield path


def logical_key(path: Path) -> tuple[str, str]:
    return os.path.normcase(str(path.parent.resolve())), path.stem.casefold()


def stable_id(path: Path) -> str:
    parent, stem = logical_key(path)
    return hashlib.sha256(f"{parent}\0{stem}".encode()).hexdigest()


def group_assets(paths: Iterable[Path]) -> list[list[Path]]:
    grouped: dict[tuple[str, str], list[Path]] = {}
    for path in paths:
        grouped.setdefault(logical_key(path), []).append(path)
    return [sorted(items, key=lambda p: (p.suffix.lower() in RAW_EXTENSIONS, str(p))) for items in grouped.values()]


def read_metadata(path: Path) -> ImageMetadata:
    if path.suffix.lower() not in RENDERED_EXTENSIONS:
        return ImageMetadata()
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            result = ImageMetadata(width=image.width, height=image.height)
            named = {ExifTags.TAGS.get(k, str(k)): v for k, v in image.getexif().items()}
            result.camera = _clean(named.get("Model"))
            result.lens = _clean(named.get("LensModel"))
            raw_date = named.get("DateTimeOriginal") or named.get("DateTime")
            if raw_date:
                try:
                    result.capture_time = datetime.strptime(str(raw_date), "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    pass
            return result
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        return ImageMetadata(error=f"Unreadable image: {exc}")


def _clean(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def make_thumbnail(source: Path, destination: Path, size=(640, 640)) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    with Image.open(source) as image:
        image.thumbnail(size)
        image.convert("RGB").save(temporary, "JPEG", quality=85, optimize=True)
    temporary.replace(destination)


def scan(source: Path, factory: sessionmaker[Session], thumbnail_dir: Path, cancelled: Event, progress: Callable[[int, int, str], None]) -> tuple[int, int]:
    groups = group_assets(discover(source))
    total, completed = len(groups), 0
    for paths in groups:
        if cancelled.is_set():
            break
        rendered = next((p for p in paths if p.suffix.lower() in RENDERED_EXTENSIONS), None)
        metadata = read_metadata(rendered) if rendered else ImageMetadata()
        identifier = stable_id(paths[0])
        thumb = thumbnail_dir / f"{identifier}.jpg"
        if rendered and not metadata.error:
            newest = max(p.stat().st_mtime_ns for p in paths)
            if not thumb.exists() or thumb.stat().st_mtime_ns < newest:
                try:
                    make_thumbnail(rendered, thumb)
                except (OSError, UnidentifiedImageError) as exc:
                    metadata.error = f"Thumbnail failed: {exc}"
        with factory() as session:
            photo = session.scalar(select(Photo).where(Photo.stable_id == identifier))
            if photo is None:
                photo = Photo(stable_id=identifier, display_name=paths[0].stem)
                session.add(photo)
                session.flush()
            photo.display_name = paths[0].stem
            photo.width, photo.height = metadata.width, metadata.height
            photo.capture_time, photo.camera, photo.lens = metadata.capture_time, metadata.camera, metadata.lens
            photo.error = metadata.error or ("RAW preview unavailable" if rendered is None else None)
            photo.thumbnail_path = str(thumb) if thumb.exists() else None
            current = {asset.source_path: asset for asset in photo.assets}
            seen: set[str] = set()
            for path in paths:
                canonical = str(path.resolve())
                seen.add(canonical)
                info = path.stat()
                asset = current.get(canonical)
                if asset is None:
                    asset = Asset(photo=photo, source_path=canonical)
                    session.add(asset)
                asset.extension = path.suffix.lower()
                asset.kind = "raw" if path.suffix.lower() in RAW_EXTENSIONS else "rendered"
                asset.size, asset.modified_ns = info.st_size, info.st_mtime_ns
            for source_path, asset in current.items():
                if source_path not in seen:
                    session.delete(asset)
            session.commit()
        completed += 1
        progress(completed, total, paths[0].name)
    return completed, total
