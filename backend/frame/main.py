from pathlib import Path
import json
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from .config import Settings
from .database import create_database, session_dependency
from .ingestion import RAW_EXTENSIONS, RENDERED_EXTENSIONS, validate_source
from .jobs import ScanCoordinator
from .models import GroupMember, Photo, PhotoGroup
from .quality import QualityConfig, recommend
from .selection import ConstraintError, PROFILES, SelectionRequest, collection_metrics, select_collection
from .exporting import safe_export


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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    engine, factory = create_database(settings)
    jobs = ScanCoordinator(factory, settings.thumbnail_dir)
    app = FastAPI(title="FRAME", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
    app.state.settings, app.state.engine, app.state.factory, app.state.jobs = settings, engine, factory, jobs

    def db():
        yield from session_dependency(factory)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "raw_preview": False, "rendered_formats": sorted(RENDERED_EXTENSIONS), "catalogued_raw_formats": sorted(RAW_EXTENSIONS)}

    @app.get("/api/quality/config")
    def get_quality_config():
        return jobs.quality_config.__dict__

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

    @app.get("/api/photos/{photo_id}/thumbnail")
    def thumbnail(photo_id: int, session: Session = Depends(db)):
        photo = session.get(Photo, photo_id)
        if not photo or not photo.thumbnail_path:
            raise HTTPException(404, "Thumbnail unavailable")
        path = Path(photo.thumbnail_path)
        if not path.is_file() or path.parent.resolve() != settings.thumbnail_dir.resolve():
            raise HTTPException(404, "Thumbnail unavailable")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    return app


def _serialize(photo: Photo) -> dict:
    analysis = json.loads(photo.analysis_json) if photo.analysis_json else None
    return {"id": photo.id, "name": photo.display_name, "capture_time": photo.capture_time, "width": photo.width, "height": photo.height, "camera": photo.camera, "lens": photo.lens, "status": photo.status, "error": photo.error, "has_thumbnail": bool(photo.thumbnail_path), "technical_score": photo.technical_score, "recommendation": photo.recommendation, "analysis": analysis, "pinned": photo.pinned, "excluded": photo.excluded, "selected": photo.selected, "selection_rank": photo.selection_rank, "selection_explanation": photo.selection_explanation, "assets": [{"path": a.source_path, "kind": a.kind, "extension": a.extension, "size": a.size} for a in photo.assets]}


app = create_app()
