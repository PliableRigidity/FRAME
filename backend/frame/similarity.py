from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Iterable

import cv2
import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from .models import GroupMember, Photo, PhotoGroup

FEATURE_VERSION = "similarity-1"


@dataclass(frozen=True)
class SimilarityConfig:
    version: int = 1
    near_hamming: int = 8
    scene_hamming: int = 16
    histogram_similarity: float = 0.94
    burst_max_gap_seconds: float = 3.0
    burst_hamming: int = 18
    minimum_group_size: int = 2

    def __post_init__(self):
        if self.version != 1 or not 0 <= self.near_hamming <= self.scene_hamming <= 32:
            raise ValueError("Invalid similarity configuration")
        if not 0 <= self.histogram_similarity <= 1 or self.burst_max_gap_seconds <= 0 or self.minimum_group_size < 2:
            raise ValueError("Invalid similarity configuration")


def sha256_file(path: Path, cancelled: Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancelled and cancelled.is_set():
                raise InterruptedError("Hashing cancelled")
            digest.update(chunk)
    return digest.hexdigest()


def _hash_bits(gray: np.ndarray, kind: str) -> str:
    if kind == "dhash":
        tiny = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        bits = tiny[:, 1:] > tiny[:, :-1]
    else:
        tiny = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
        dct = cv2.dct(tiny.astype(np.float32))[:8, :8]
        bits = dct > np.median(dct[1:])
    return f"{int(''.join('1' if bit else '0' for bit in bits.ravel()), 2):016x}"


def extract_features(path: Path) -> dict:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Image pixels could not be decoded")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256]).ravel()
    histogram /= max(float(histogram.sum()), 1)
    return {"version": FEATURE_VERSION, "phash": _hash_bits(gray, "phash"), "dhash": _hash_bits(gray, "dhash"), "histogram": [round(float(x), 6) for x in histogram]}


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def histogram_similarity(a: list[float], b: list[float]) -> float:
    return float(np.minimum(a, b).sum())


def visual_similarity(a: dict, b: dict) -> tuple[int, float]:
    return max(hamming(a["phash"], b["phash"]), hamming(a["dhash"], b["dhash"])), histogram_similarity(a["histogram"], b["histogram"])


def _components(ids: Iterable[int], edges: Iterable[tuple[int, int]]) -> list[list[int]]:
    parent = {value: value for value in ids}
    def root(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]; value = parent[value]
        return value
    for a, b in edges:
        ra, rb = root(a), root(b)
        if ra != rb: parent[rb] = ra
    groups: dict[int, list[int]] = {}
    for value in parent: groups.setdefault(root(value), []).append(value)
    return [sorted(values) for values in groups.values() if len(values) > 1]


def find_groups(photos: list[Photo], config: SimilarityConfig = SimilarityConfig()) -> dict[str, list[list[int]]]:
    usable = [p for p in photos if p.feature_json]
    features = {p.id: json.loads(p.feature_json) for p in usable}
    exact_buckets: dict[str, list[int]] = {}
    for photo in usable:
        if photo.content_hash: exact_buckets.setdefault(photo.content_hash, []).append(photo.id)
    exact = [sorted(v) for v in exact_buckets.values() if len(v) >= config.minimum_group_size]

    # Candidate generation: four 16-bit pHash bands; only colliding photos are compared.
    buckets: dict[tuple[int, str], list[int]] = {}
    for photo in usable:
        value = features[photo.id]["phash"]
        for index in range(4): buckets.setdefault((index, value[index * 4:(index + 1) * 4]), []).append(photo.id)
    candidates = {tuple(sorted((a, b))) for values in buckets.values() for i, a in enumerate(values) for b in values[i + 1:]}
    near_edges, scene_edges = [], []
    exact_pairs = {tuple(sorted((a, b))) for group in exact for i, a in enumerate(group) for b in group[i + 1:]}
    for a, b in sorted(candidates):
        if (a, b) in exact_pairs: continue
        distance, colour = visual_similarity(features[a], features[b])
        if distance <= config.near_hamming and colour >= config.histogram_similarity:
            near_edges.append((a, b))
        elif distance <= config.scene_hamming and colour >= max(.97, config.histogram_similarity):
            scene_edges.append((a, b))
    near = _components(features, near_edges)
    scene = _components(features, scene_edges)

    ordered = sorted((p for p in usable if p.capture_time), key=lambda p: (p.capture_time, p.id))
    burst_edges = []
    for index, photo in enumerate(ordered):
        for other in ordered[index + 1:]:
            gap = (other.capture_time - photo.capture_time).total_seconds()
            if gap > config.burst_max_gap_seconds: break
            distance, colour = visual_similarity(features[photo.id], features[other.id])
            if distance <= config.burst_hamming and colour >= .75: burst_edges.append((photo.id, other.id))
    burst = _components(features, burst_edges)
    return {"exact": exact, "near": near, "burst": burst, "scene": scene}


def best_frame(photos: list[Photo]) -> tuple[int, list[int], str]:
    def value(photo: Photo):
        decision_penalty = {"KEEP": 4, "REVIEW": 0, "REJECT": -20}.get(photo.recommendation, 0)
        return ((photo.technical_score if photo.technical_score is not None else 50) + decision_penalty, -photo.id)
    ranked = sorted(photos, key=value, reverse=True)
    lead = ranked[0]
    explanation = "Recommended for the strongest balanced technical score in this group; sharpness, exposure, clipping and noise are already represented in that score."
    return lead.id, [p.id for p in ranked], explanation


def persist_groups(session: Session, config: SimilarityConfig = SimilarityConfig()) -> dict[str, int]:
    photos = list(session.scalars(select(Photo).options(selectinload(Photo.assets))))
    found = find_groups(photos, config)
    existing_manual = {g.stable_id: g.lead_photo_id for g in session.scalars(select(PhotoGroup).where(PhotoGroup.manual_lead.is_(True)))}
    session.execute(delete(PhotoGroup))
    by_id = {p.id: p for p in photos}
    counts = {}
    for kind, groups in found.items():
        counts[kind] = len(groups)
        for member_ids in groups:
            stable = hashlib.sha256(f"{kind}:{','.join(map(str, member_ids))}".encode()).hexdigest()
            lead, ranked, explanation = best_frame([by_id[i] for i in member_ids])
            manual = existing_manual.get(stable)
            if manual in member_ids: lead = manual
            group = PhotoGroup(stable_id=stable, kind=kind, explanation=explanation, lead_photo_id=lead, manual_lead=manual in member_ids)
            session.add(group); session.flush()
            for rank, photo_id in enumerate(ranked, 1): session.add(GroupMember(group_id=group.id, photo_id=photo_id, relative_rank=rank))
    session.commit()
    return counts
