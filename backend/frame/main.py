from pathlib import Path
import json
import importlib.util
import logging
import platform
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from .config import Settings
from .database import create_database, session_dependency
from .ingestion import RAW_EXTENSIONS, RENDERED_EXTENSIONS, validate_source
from .jobs import ScanCoordinator
from .models import GroupMember, PairwiseFeedback, Photo, PhotoGroup, PreferenceProfile
from .quality import QualityConfig, recommend
from .selection import ConstraintError, PROFILES, SelectionRequest, collection_metrics, select_collection
from .exporting import ExportCoordinator, safe_export
from .ml import CONTEXTS, LearnedCoordinator, MLSettings, hybrid_score
from .preferences import next_pair, retrain, review_queue
from . import __version__


class ScanRequest(BaseModel):
    path: str


class QualityConfigRequest(BaseModel):
    version: int = 1
    blur_sensitivity: float = 0.5
    underexposure_tolerance: float = 0.65
    highlight_clipping_tolerance: float = 0.02
    noise_sensitivity: float = 0.5
    rejection_conservativeness: float = 0.85


class CollectionRequest(BaseModel):
    target: int
    method: str = "diversity"
    profile: str = "general"
    diversity_weight: float | None = None


class OverrideRequest(BaseModel):
    pinned: bool | None = None
    excluded: bool | None = None


class ExportRequest(BaseModel):
    destination: str
    mode: str = "jpeg"
    metadata: str = "json"


class MLSettingsRequest(BaseModel):
    mode: str = "classical"
    device: str = "auto"
    classical_weight: float = .6
    learned_weight: float = .4


class PreferenceProfileRequest(BaseModel):
    name: str
    enabled: bool = True


class FeedbackRequest(BaseModel):
    photo_a_id: int
    photo_b_id: int
    choice: str


def create_app(settings: Settings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}')
    settings = settings or Settings.load()
    engine, factory = create_database(settings)
    jobs = ScanCoordinator(factory, settings.thumbnail_dir)
    learned = LearnedCoordinator(factory, settings.data_dir / "models" / "quality" / "model.json")
    exports = ExportCoordinator(factory)
    app = FastAPI(title="FRAME", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
    app.state.settings, app.state.engine, app.state.factory, app.state.jobs, app.state.learned, app.state.exports = settings, engine, factory, jobs, learned, exports

    def db():
        yield from session_dependency(factory)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "raw_preview": bool(importlib.util.find_spec("rawpy")), "rendered_formats": sorted(RENDERED_EXTENSIONS), "catalogued_raw_formats": sorted(RAW_EXTENSIONS)}

    @app.get("/api/diagnostics")
    def diagnostics():
        return {"version": __version__, "python": platform.python_version(), "platform": platform.system(), "database": "sqlite", "scan": jobs.status(), "ml": learned.status(), "export": exports.status(), "privacy": "Local processing; no telemetry or upload endpoints."}

    @app.get("/api/quality/config")
    def get_quality_config():
        return jobs.quality_config.__dict__

    @app.get("/api/ml/status")
    def ml_status():
        status = learned.status(); status.update(installed=learned.manifest_path.is_file(), settings=learned.settings.__dict__)
        return status

    @app.put("/api/ml/settings")
    def ml_settings(request: MLSettingsRequest, session: Session = Depends(db)):
        try: learned.settings = MLSettings(**request.model_dump())
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
        for photo in session.scalars(select(Photo)):
            learned_score = json.loads(photo.learned_json)["normalised_score"] if photo.learned_json else None
            photo.hybrid_score = hybrid_score(photo.technical_score, learned_score, learned.settings)
        session.commit(); return {"settings": learned.settings.__dict__, "inference_rerun": False}

    @app.post("/api/ml/analyse", status_code=202)
    def start_ml():
        if not learned.manifest_path.is_file(): raise HTTPException(409, "No checksum-verified IQA model is installed")
        try: return learned.start()
        except RuntimeError as exc: raise HTTPException(409, str(exc)) from exc

    @app.post("/api/ml/cancel")
    def cancel_ml(): return learned.cancel()

    @app.get("/api/review/queue")
    def get_review_queue(budget: int | None = Query(25, ge=0, le=10000), session: Session = Depends(db)):
        queue = review_queue(session, budget); session.commit()
        return {"budget": budget, "items": [{"photo": _serialize(photo), "priority": priority, "label": "Heuristic review priority", "reason": reason} for photo, priority, reason in queue]}

    @app.get("/api/preferences/profiles")
    def preference_profiles(session: Session = Depends(db)):
        return {"items": [{"id": p.id, "name": p.name, "enabled": p.enabled, "model_version": p.model_version, "model": json.loads(p.model_json) if p.model_json else None} for p in session.scalars(select(PreferenceProfile).order_by(PreferenceProfile.id))]}

    @app.post("/api/preferences/profiles")
    def create_preference_profile(request: PreferenceProfileRequest, session: Session = Depends(db)):
        if not request.name.strip(): raise HTTPException(422, "Profile name is required")
        profile = PreferenceProfile(name=request.name.strip(), enabled=request.enabled); session.add(profile)
        try: session.commit()
        except Exception as exc: session.rollback(); raise HTTPException(409, "Profile name already exists") from exc
        return {"id": profile.id, "name": profile.name, "enabled": profile.enabled}

    @app.delete("/api/preferences/profiles/{profile_id}/feedback/latest")
    def undo_feedback(profile_id: int, session: Session = Depends(db)):
        feedback = session.scalar(select(PairwiseFeedback).where(PairwiseFeedback.profile_id == profile_id).order_by(PairwiseFeedback.id.desc()))
        if not feedback: raise HTTPException(404, "No feedback to undo")
        session.delete(feedback); profile = session.get(PreferenceProfile, profile_id); session.flush()
        return retrain(session, profile)

    @app.delete("/api/preferences/profiles/{profile_id}/feedback")
    def reset_feedback(profile_id: int, session: Session = Depends(db)):
        for feedback in session.scalars(select(PairwiseFeedback).where(PairwiseFeedback.profile_id == profile_id)): session.delete(feedback)
        profile = session.get(PreferenceProfile, profile_id)
        if not profile: raise HTTPException(404, "Profile not found")
        session.flush(); return retrain(session, profile)

    @app.get("/api/preferences/profiles/{profile_id}/pair")
    def comparison_pair(profile_id: int, session: Session = Depends(db)):
        if not session.get(PreferenceProfile, profile_id): raise HTTPException(404, "Profile not found")
        pair = next_pair(session, profile_id)
        return {"pair": None if not pair else {"a": _serialize(pair[0]), "b": _serialize(pair[1])}}

    @app.post("/api/preferences/profiles/{profile_id}/feedback")
    def record_feedback(profile_id: int, request: FeedbackRequest, session: Session = Depends(db)):
        if request.choice not in {"a", "b", "equal", "skip"}: raise HTTPException(422, "Unknown comparison choice")
        if request.photo_a_id == request.photo_b_id: raise HTTPException(422, "Choose two different photographs")
        profile = session.get(PreferenceProfile, profile_id)
        if not profile or not session.get(Photo, request.photo_a_id) or not session.get(Photo, request.photo_b_id): raise HTTPException(404, "Profile or photo not found")
        session.add(PairwiseFeedback(profile_id=profile_id, photo_a_id=request.photo_a_id, photo_b_id=request.photo_b_id, choice=request.choice)); session.flush()
        return retrain(session, profile)

    @app.get("/api/preferences/profiles/{profile_id}/export")
    def export_preferences(profile_id: int, session: Session = Depends(db)):
        profile = session.get(PreferenceProfile, profile_id)
        if not profile: raise HTTPException(404, "Profile not found")
        feedback = list(session.scalars(select(PairwiseFeedback).where(PairwiseFeedback.profile_id == profile_id)))
        return {"format_version": 1, "profile": {"name": profile.name, "enabled": profile.enabled}, "feedback": [{"photo_a_id": f.photo_a_id, "photo_b_id": f.photo_b_id, "choice": f.choice} for f in feedback]}

    @app.put("/api/photos/{photo_id}/context/{profile}")
    def set_context(photo_id: int, profile: str, session: Session = Depends(db)):
        if profile not in CONTEXTS: raise HTTPException(422, "Unknown context profile")
        photo = session.get(Photo, photo_id)
        if not photo: raise HTTPException(404, "Photo not found")
        photo.context_profile = profile; session.commit(); return _serialize(photo)

    @app.put("/api/quality/config")
    def put_quality_config(request: QualityConfigRequest, session: Session = Depends(db)):
        try:
            config = QualityConfig(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        jobs.quality_config = config
        updated = 0
        for photo in session.scalars(select(Photo).where(Photo.analysis_json.is_not(None))):
            payload = json.loads(photo.analysis_json)
            decision = recommend(payload["metrics"], config)
            payload["decision"] = decision
            photo.analysis_json = json.dumps(payload, separators=(",", ":"))
            photo.technical_score = decision["technical_score"]
            photo.recommendation = decision["recommendation"]
            photo.status = decision["recommendation"]
            updated += 1
        session.commit()
        return {"config": config.__dict__, "recommendations_updated": updated, "measurements_rerun": False}

    @app.post("/api/scans", status_code=202)
    def start_scan(request: ScanRequest):
        try:
            return jobs.start(validate_source(request.path))
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400 if isinstance(exc, ValueError) else 409, str(exc)) from exc

    @app.get("/api/scans/current")
    def scan_status():
        return jobs.status()

    @app.post("/api/scans/current/cancel")
    def cancel_scan():
        return jobs.cancel()

    @app.get("/api/photos")
    def photos(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), session: Session = Depends(db)):
        total = session.scalar(select(func.count(Photo.id))) or 0
        rows = session.scalars(select(Photo).options(selectinload(Photo.assets)).order_by(Photo.capture_time.desc().nullslast(), Photo.id.desc()).offset(offset).limit(limit)).all()
        return {"total": total, "items": [_serialize(photo) for photo in rows]}

    @app.get("/api/groups")
    def groups(session: Session = Depends(db)):
        rows = session.scalars(select(PhotoGroup).options(selectinload(PhotoGroup.members).selectinload(GroupMember.photo)).order_by(PhotoGroup.kind, PhotoGroup.id)).all()
        return {"items": [{"id": g.id, "stable_id": g.stable_id, "kind": g.kind, "explanation": g.explanation, "lead_photo_id": g.lead_photo_id, "manual_lead": g.manual_lead, "members": [{"photo": _serialize(m.photo), "rank": m.relative_rank} for m in sorted(g.members, key=lambda x: x.relative_rank or 999)]} for g in rows]}

    @app.put("/api/groups/{group_id}/lead/{photo_id}")
    def set_group_lead(group_id: int, photo_id: int, session: Session = Depends(db)):
        group = session.get(PhotoGroup, group_id)
        if not group or not session.scalar(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.photo_id == photo_id)):
            raise HTTPException(404, "Group member not found")
        group.lead_photo_id, group.manual_lead = photo_id, True
        session.commit()
        return {"group_id": group_id, "lead_photo_id": photo_id, "manual_lead": True}

    @app.patch("/api/photos/{photo_id}/selection")
    def override_photo(photo_id: int, request: OverrideRequest, session: Session = Depends(db)):
        photo = session.get(Photo, photo_id)
        if not photo: raise HTTPException(404, "Photo not found")
        if request.pinned is not None: photo.pinned = request.pinned
        if request.excluded is not None: photo.excluded = request.excluded
        if photo.pinned and photo.excluded: raise HTTPException(409, "A photograph cannot be pinned and excluded")
        session.commit(); return _serialize(photo)

    @app.get("/api/selection/profiles")
    def profiles():
        return PROFILES

    @app.post("/api/selection")
    def build_collection(request: CollectionRequest, session: Session = Depends(db)):
        photos = list(session.scalars(select(Photo).options(selectinload(Photo.assets))))
        memberships: dict[int, set[str]] = {}
        for member, group in session.execute(select(GroupMember, PhotoGroup).join(PhotoGroup).where(PhotoGroup.kind.in_(["exact", "near", "burst"]))):
            memberships.setdefault(member.photo_id, set()).add(group.stable_id)
        try:
            spec = SelectionRequest(request.target, request.method, request.profile, request.diversity_weight)  # type: ignore[arg-type]
            chosen = select_collection(photos, spec, memberships)
        except ConstraintError as exc:
            raise HTTPException(409, str(exc)) from exc
        selected_ids = {photo.id for photo, _ in chosen}
        for photo in photos:
            photo.selected = photo.id in selected_ids
            photo.selection_rank = next((rank for rank, (item, _) in enumerate(chosen, 1) if item.id == photo.id), None)
            photo.selection_explanation = next((reason for item, reason in chosen if item.id == photo.id), None)
        session.commit()
        return {"items": [_serialize(photo) for photo, _ in chosen], "metrics": collection_metrics([p for p, _ in chosen], photos)}

    @app.post("/api/selection/export")
    def export_selection(request: ExportRequest, session: Session = Depends(db)):
        if request.mode not in {"jpeg", "pairs"} or request.metadata not in {"json", "csv"}:
            raise HTTPException(422, "Unsupported export mode")
        photos = list(session.scalars(select(Photo).options(selectinload(Photo.assets)).where(Photo.selected.is_(True)).order_by(Photo.selection_rank)))
        return safe_export(photos, Path(request.destination), request.mode, request.metadata)

    @app.post("/api/exports", status_code=202)
    def start_export(request: ExportRequest):
        if request.mode not in {"jpeg", "pairs"} or request.metadata not in {"json", "csv"}: raise HTTPException(422, "Unsupported export mode")
        try: return exports.start(Path(request.destination), request.mode, request.metadata)
        except RuntimeError as exc: raise HTTPException(409, str(exc)) from exc

    @app.get("/api/exports/current")
    def export_status(): return exports.status()

    @app.post("/api/exports/current/cancel")
    def cancel_export(): return exports.cancel()

    @app.get("/api/photos/{photo_id}/thumbnail")
    def thumbnail(photo_id: int, session: Session = Depends(db)):
        photo = session.get(Photo, photo_id)
        if not photo or not photo.thumbnail_path:
            raise HTTPException(404, "Thumbnail unavailable")
        path = Path(photo.thumbnail_path)
        if not path.is_file() or path.parent.resolve() != settings.thumbnail_dir.resolve():
            raise HTTPException(404, "Thumbnail unavailable")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    frontend = Path(__file__).parents[2] / "frontend" / "dist"
    if not frontend.is_dir(): frontend = Path(__file__).parent / "static"
    if frontend.is_dir(): app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


def _serialize(photo: Photo) -> dict:
    analysis = json.loads(photo.analysis_json) if photo.analysis_json else None
    return {"id": photo.id, "name": photo.display_name, "capture_time": photo.capture_time, "width": photo.width, "height": photo.height, "camera": photo.camera, "lens": photo.lens, "status": photo.status, "error": photo.error, "has_thumbnail": bool(photo.thumbnail_path), "technical_score": photo.technical_score, "learned": json.loads(photo.learned_json) if photo.learned_json else None, "hybrid_score": photo.hybrid_score, "context_profile": photo.context_profile, "preference_score": photo.preference_score, "review_priority": photo.review_priority, "recommendation": photo.recommendation, "analysis": analysis, "pinned": photo.pinned, "excluded": photo.excluded, "selected": photo.selected, "selection_rank": photo.selection_rank, "selection_explanation": photo.selection_explanation, "assets": [{"path": a.source_path, "kind": a.kind, "extension": a.extension, "size": a.size} for a in photo.assets]}


app = create_app()
