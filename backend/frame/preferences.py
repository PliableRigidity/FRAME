from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GroupMember, PairwiseFeedback, Photo, PhotoGroup, PreferenceProfile

FEATURE_NAMES = ["quality", "sharpness", "brightness", "clipping", "noise", "uniqueness"]


def photo_features(photo: Photo, group_count: int = 0) -> np.ndarray:
    payload = json.loads(photo.analysis_json) if photo.analysis_json else {}
    metrics = payload.get("metrics", {})
    return np.array([
        (photo.hybrid_score if photo.hybrid_score is not None else photo.technical_score if photo.technical_score is not None else 50) / 100,
        min(1, math.sqrt(max(0, metrics.get("tenengrad", 0))) / .15),
        metrics.get("mean_luminance", .5),
        1 - min(1, metrics.get("highlight_clipping", 0) + metrics.get("shadow_clipping", 0)),
        1 - min(1, metrics.get("noise_estimate", 0) * 10),
        1 / (1 + group_count),
    ], dtype=np.float64)


def train(feedback: list[tuple[np.ndarray, np.ndarray, str]], regularisation: float = 1.0) -> dict | None:
    examples = [(a - b, 1.0 if choice == "a" else 0.0) for a, b, choice in feedback if choice in {"a", "b"}]
    if len(examples) < 3: return None
    weights = np.zeros(len(FEATURE_NAMES), dtype=np.float64)
    for _ in range(250):
        gradient = regularisation * weights
        for difference, target in examples:
            probability = 1 / (1 + math.exp(-float(np.clip(weights @ difference, -30, 30))))
            gradient += (probability - target) * difference
        weights -= .08 * gradient / len(examples)
    return {"version": 1, "features": FEATURE_NAMES, "weights": [round(float(x), 8) for x in weights], "examples": len(examples), "note": "Regularised pairwise logistic model; small-data fallback remains active."}


def apply_profile(session: Session, profile: PreferenceProfile) -> int:
    group_counts: dict[int, int] = {}
    for member in session.scalars(select(GroupMember)):
        group_counts[member.photo_id] = group_counts.get(member.photo_id, 0) + 1
    photos = list(session.scalars(select(Photo)))
    if not profile.enabled or not profile.model_json:
        for photo in photos: photo.preference_score = None
        return 0
    weights = np.asarray(json.loads(profile.model_json)["weights"])
    raw = [float(weights @ photo_features(photo, group_counts.get(photo.id, 0))) for photo in photos]
    low, high = (min(raw), max(raw)) if raw else (0, 0)
    for photo, value in zip(photos, raw): photo.preference_score = 50 if high == low else round(100 * (value - low) / (high - low), 2)
    return len(photos)


def retrain(session: Session, profile: PreferenceProfile) -> dict:
    feedback = list(session.scalars(select(PairwiseFeedback).where(PairwiseFeedback.profile_id == profile.id).order_by(PairwiseFeedback.id)))
    photos = {p.id: p for p in session.scalars(select(Photo))}
    rows = [(photo_features(photos[f.photo_a_id]), photo_features(photos[f.photo_b_id]), f.choice) for f in feedback if f.photo_a_id in photos and f.photo_b_id in photos]
    model = train(rows)
    profile.model_json = json.dumps(model) if model else None; profile.model_version += 1
    applied = apply_profile(session, profile); session.commit()
    return {"trained": model is not None, "feedback_count": len(feedback), "applied_to": applied, "model_version": profile.model_version}


def review_queue(session: Session, budget: int | None) -> list[tuple[Photo, float, str]]:
    photos = list(session.scalars(select(Photo)))
    memberships: dict[int, int] = {}
    for member in session.scalars(select(GroupMember)): memberships[member.photo_id] = memberships.get(member.photo_id, 0) + 1
    selected_scores = sorted((p.hybrid_score or p.technical_score or 50 for p in photos if not p.excluded), reverse=True)
    boundary = selected_scores[min(len(selected_scores) - 1, max(0, (budget or len(selected_scores)) - 1))] if selected_scores else 50
    queue = []
    for photo in photos:
        quality = photo.hybrid_score or photo.technical_score or 50
        evidence_gap = .25 if not photo.analysis_json else 0
        disagreement = abs((photo.technical_score or 50) - (photo.hybrid_score or photo.technical_score or 50)) / 100
        boundary_value = max(0, 1 - abs(quality - boundary) / 30)
        unique = 1 / (1 + memberships.get(photo.id, 0))
        priority = round(100 * (.35 * boundary_value + .25 * disagreement + .2 * evidence_gap + .2 * unique), 2)
        reason = "Near the current selection boundary" if boundary_value > .7 else "Potentially useful preference evidence"
        photo.review_priority = priority; queue.append((photo, priority, reason))
    queue.sort(key=lambda item: (-item[1], item[0].id))
    return queue if budget is None else queue[:max(0, budget)]


def next_pair(session: Session, profile_id: int) -> tuple[Photo, Photo] | None:
    seen = {tuple(sorted((f.photo_a_id, f.photo_b_id))) for f in session.scalars(select(PairwiseFeedback).where(PairwiseFeedback.profile_id == profile_id))}
    photos = sorted(session.scalars(select(Photo)), key=lambda p: (-(p.review_priority or 0), p.id))
    candidates = [(a, b) for i, a in enumerate(photos) for b in photos[i + 1:] if (a.id, b.id) not in seen]
    if not candidates: return None
    return min(candidates, key=lambda pair: (abs((pair[0].technical_score or 50) - (pair[1].technical_score or 50)), pair[0].id, pair[1].id))
