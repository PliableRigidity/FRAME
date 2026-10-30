from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from .models import Photo
from .similarity import visual_similarity

PROFILES = {
    "general": {"quality_weight": .65, "diversity_weight": .35},
    "travel": {"quality_weight": .5, "diversity_weight": .5},
    "portrait": {"quality_weight": .7, "diversity_weight": .3},
    "landscape": {"quality_weight": .62, "diversity_weight": .38},
    "sports": {"quality_weight": .75, "diversity_weight": .25},
    "custom": {"quality_weight": .65, "diversity_weight": .35},
}


class ConstraintError(ValueError):
    pass


@dataclass(frozen=True)
class SelectionRequest:
    target: int
    method: Literal["quality", "grouped", "diversity"] = "diversity"
    profile: str = "general"
    diversity_weight: float | None = None


def quality(photo: Photo) -> float:
    score = photo.technical_score if photo.technical_score is not None else 50.0
    return max(0, min(1, score / 100))


def similarity(a: Photo, b: Photo) -> float:
    if not a.feature_json or not b.feature_json: return 0.0
    distance, colour = visual_similarity(json.loads(a.feature_json), json.loads(b.feature_json))
    return max(0.0, min(1.0, .75 * (1 - distance / 64) + .25 * colour))


def select_collection(photos: list[Photo], request: SelectionRequest, group_by_photo: dict[int, set[str]] | None = None) -> list[tuple[Photo, str]]:
    if request.target < 0: raise ConstraintError("Target size cannot be negative")
    if request.method not in {"quality", "grouped", "diversity"}: raise ConstraintError("Unknown selection method")
    if request.profile not in PROFILES: raise ConstraintError("Unknown selection profile")
    pinned = sorted((p for p in photos if p.pinned), key=lambda p: p.id)
    if any(p.excluded for p in pinned): raise ConstraintError("A photograph cannot be both pinned and excluded")
    if len(pinned) > request.target: raise ConstraintError("Pinned photographs exceed the target size")
    eligible = [p for p in photos if not p.excluded and not p.pinned]
    target = min(request.target, len(eligible) + len(pinned))
    result = [(p, "Pinned by the user.") for p in pinned]
    if len(result) == target: return result
    ordered = sorted(eligible, key=lambda p: (-quality(p), p.id))
    group_by_photo = group_by_photo or {}
    if request.method == "quality":
        result.extend((p, "Selected by individual technical-quality ranking.") for p in ordered[:target - len(result)])
        return result
    if request.method == "grouped":
        represented: set[str] = set().union(*(group_by_photo.get(p.id, set()) for p, _ in result))
        first, rest = [], []
        for photo in ordered:
            groups = group_by_photo.get(photo.id, set())
            (rest if groups & represented else first).append(photo)
            represented.update(groups)
        chosen = (first + rest)[:target - len(result)]
        result.extend((p, "Selected as a strong representative before redundant alternatives." if p in first else "Selected after group representatives were covered.") for p in chosen)
        return result
    weights = PROFILES[request.profile]
    diversity = request.diversity_weight if request.diversity_weight is not None else weights["diversity_weight"]
    if not 0 <= diversity <= 1: raise ConstraintError("Diversity weight must be between zero and one")
    selected = [p for p, _ in result]
    remaining = ordered.copy()
    feature_cache = {p.id: json.loads(p.feature_json) for p in photos if p.feature_json}
    def cached_similarity(a: Photo, b: Photo) -> float:
        if a.id not in feature_cache or b.id not in feature_cache: return 0.0
        distance, colour = visual_similarity(feature_cache[a.id], feature_cache[b.id])
        return max(0.0, min(1.0, .75 * (1 - distance / 64) + .25 * colour))
    redundancy = {photo.id: max((cached_similarity(photo, chosen) for chosen in selected), default=0.0) for photo in remaining}
    while remaining and len(result) < target:
        def utility(photo):
            return ((1 - diversity) * quality(photo) - diversity * redundancy[photo.id], quality(photo), -photo.id)
        winner = max(remaining, key=utility); remaining.remove(winner)
        winner_redundancy = redundancy[winner.id]
        reason = "Selected for strong quality and additional visual coverage." if winner_redundancy < .65 else "Selected despite similarity because its quality remained competitive."
        result.append((winner, reason)); selected.append(winner)
        for photo in remaining:
            redundancy[photo.id] = max(redundancy[photo.id], cached_similarity(photo, winner))
    return result


def collection_metrics(selected: list[Photo], all_photos: list[Photo]) -> dict:
    pairs = [similarity(a, b) for i, a in enumerate(selected) for b in selected[i + 1:]]
    return {"selected_count": len(selected), "candidate_count": len(all_photos), "average_technical_quality": round(sum(quality(p) * 100 for p in selected) / len(selected), 1) if selected else None, "average_pair_similarity": round(sum(pairs) / len(pairs), 3) if pairs else 0, "review_count": sum(p.recommendation == "REVIEW" for p in selected)}
