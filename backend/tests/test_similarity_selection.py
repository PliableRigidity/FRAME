from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event

import cv2
import numpy as np
import pytest
from PIL import Image, ImageEnhance

from frame.exporting import safe_export, validate_export_destination
from frame.ingestion import scan
from frame.models import Asset, Photo
from frame.selection import ConstraintError, SelectionRequest, select_collection
from frame.similarity import SimilarityConfig, extract_features, find_groups, sha256_file


def patterned(path: Path, shift=0, invert=False):
    y, x = np.indices((160, 200))
    pixels = np.dstack(((x * 3 + shift) % 256, (y * 2) % 256, ((x + y) * 4) % 256)).astype(np.uint8)
    if invert: pixels = 255 - pixels
    Image.fromarray(pixels).save(path)
    return path


def photo(photo_id: int, path: Path, captured=None, score=70) -> Photo:
    features = extract_features(path)
    return Photo(id=photo_id, stable_id=str(photo_id), display_name=path.stem, capture_time=captured, technical_score=score, recommendation="KEEP", feature_json=json.dumps(features), content_hash=sha256_file(path))


def test_exact_duplicate_different_names_and_same_name_different_content(tmp_path):
    first = patterned(tmp_path / "one.jpg")
    duplicate = tmp_path / "copy.jpg"; duplicate.write_bytes(first.read_bytes())
    other_dir = tmp_path / "other"; other_dir.mkdir(); different = patterned(other_dir / "one.jpg", invert=True)
    groups = find_groups([photo(1, first), photo(2, duplicate), photo(3, different)])
    assert groups["exact"] == [[1, 2]]
    assert 3 not in groups["exact"][0]


def test_near_identical_and_unrelated_close_capture(tmp_path):
    base = patterned(tmp_path / "base.png")
    near_path = tmp_path / "near.jpg"
    ImageEnhance.Brightness(Image.open(base)).enhance(.98).save(near_path, quality=95)
    unrelated = patterned(tmp_path / "other.png", invert=True)
    now = datetime(2026, 1, 1)
    groups = find_groups([photo(1, base, now), photo(2, near_path, now + timedelta(seconds=1)), photo(3, unrelated, now + timedelta(seconds=2))])
    assert any(set(group) == {1, 2} for group in groups["near"] + groups["burst"])
    assert all(not ({1, 3} <= set(group)) for group in groups["burst"])


def test_missing_and_out_of_order_timestamps(tmp_path):
    paths = [patterned(tmp_path / f"{i}.png", shift=i) for i in range(3)]
    now = datetime(2026, 1, 1)
    photos = [photo(1, paths[0], now + timedelta(seconds=2)), photo(2, paths[1], None), photo(3, paths[2], now)]
    groups = find_groups(photos)
    assert isinstance(groups["burst"], list)


def test_selection_sizes_pins_exclusions_and_determinism(tmp_path):
    paths = [patterned(tmp_path / f"{i}.png", shift=i * 10) for i in range(4)]
    photos = [photo(i + 1, path, score=60 + i * 5) for i, path in enumerate(paths)]
    photos[0].pinned = True; photos[3].excluded = True
    first = select_collection(photos, SelectionRequest(3, "diversity"))
    second = select_collection(photos, SelectionRequest(3, "diversity"))
    assert [p.id for p, _ in first] == [p.id for p, _ in second]
    assert len(first) == 3 and first[0][0].id == 1 and 4 not in [p.id for p, _ in first]
    assert select_collection(photos, SelectionRequest(20))
    assert select_collection([], SelectionRequest(0)) == []
    photos[0].excluded = True
    with pytest.raises(ConstraintError): select_collection(photos, SelectionRequest(2))


def test_safe_export_preserves_source_and_handles_collision(tmp_path):
    source_dir = tmp_path / "source"; source_dir.mkdir()
    source = patterned(source_dir / "image.jpg")
    before = source.read_bytes()
    item = photo(1, source); item.assets = [Asset(source_path=str(source), extension=".jpg", kind="rendered", size=source.stat().st_size, modified_ns=source.stat().st_mtime_ns)]
    destination = tmp_path / "export"
    first = safe_export([item], destination)
    second = safe_export([item], destination)
    assert first["complete"] and second["complete"]
    assert len(list(destination.glob("*.jpg"))) == 2
    assert source.read_bytes() == before
    with pytest.raises(ValueError): validate_export_destination(source_dir, [item])


def test_similarity_cache_invalidation(app, tmp_path):
    source = tmp_path / "photos"; source.mkdir(); path = patterned(source / "one.jpg")
    scan(source, app.state.factory, app.state.settings.thumbnail_dir, Event(), lambda *_: None)
    with app.state.factory() as session: before = session.query(Photo).one().feature_source_signature
    patterned(path, invert=True)
    scan(source, app.state.factory, app.state.settings.thumbnail_dir, Event(), lambda *_: None)
    with app.state.factory() as session: after = session.query(Photo).one().feature_source_signature
    assert before != after


def test_invalid_similarity_config():
    with pytest.raises(ValueError): SimilarityConfig(near_hamming=20, scene_hamming=10)
