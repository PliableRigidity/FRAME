from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Iterable

import cv2
import numpy as np

PREPROCESS_VERSION = "rgb-224-imagenet-1"
CONTEXTS = {"general", "portrait", "landscape", "architecture", "street", "sports", "wildlife", "night"}


class ModelUnavailable(RuntimeError): pass


@dataclass(frozen=True)
class ModelManifest:
    identity: str
    version: str
    weights: str
    sha256: str
    output_min: float = 0.0
    output_max: float = 1.0

    @classmethod
    def load(cls, path: Path) -> "ModelManifest":
        return cls(**json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class MLSettings:
    mode: str = "classical"
    device: str = "auto"
    classical_weight: float = .6
    learned_weight: float = .4

    def __post_init__(self):
        if self.mode not in {"classical", "learned", "hybrid"} or self.device not in {"auto", "cpu", "cuda"}:
            raise ValueError("Invalid learned-analysis settings")
        if self.classical_weight < 0 or self.learned_weight < 0 or self.classical_weight + self.learned_weight <= 0:
            raise ValueError("Scoring weights must be non-negative with a positive total")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


class OnnxQualityModel:
    """Adapter for explicitly installed scalar-output ONNX IQA models."""
    def __init__(self, manifest_path: Path, device: str = "auto"):
        try: import onnxruntime as ort
        except ImportError as exc: raise ModelUnavailable("Install FRAME's optional AI dependencies") from exc
        self.manifest_path = manifest_path.resolve(); self.manifest = ModelManifest.load(self.manifest_path)
        weights = (self.manifest_path.parent / self.manifest.weights).resolve()
        if weights.parent != self.manifest_path.parent or not weights.is_file(): raise ModelUnavailable("Model weights are missing")
        if file_sha256(weights) != self.manifest.sha256: raise ModelUnavailable("Model checksum mismatch")
        available = ort.get_available_providers()
        if device == "cuda" and "CUDAExecutionProvider" not in available: raise ModelUnavailable("CUDA execution provider is unavailable")
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if device in {"auto", "cuda"} and "CUDAExecutionProvider" in available else ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(weights), providers=providers)
        self.device = "cuda" if self.session.get_providers()[0] == "CUDAExecutionProvider" else "cpu"
        self.input_name = self.session.get_inputs()[0].name

    def predict(self, paths: Iterable[Path], cancelled: Event | None = None, batch_size: int = 8) -> list[dict]:
        paths = list(paths); results = []
        for start in range(0, len(paths), max(1, batch_size)):
            if cancelled and cancelled.is_set(): break
            batch_paths = paths[start:start + batch_size]
            batch = np.stack([self._preprocess(path) for path in batch_paths])
            outputs = np.asarray(self.session.run(None, {self.input_name: batch})[0]).reshape(len(batch_paths), -1)[:, 0]
            for path, raw in zip(batch_paths, outputs):
                low, high = self.manifest.output_min, self.manifest.output_max
                normalised = float(np.clip((float(raw) - low) / max(high - low, 1e-9), 0, 1))
                results.append({"model": self.manifest.identity, "model_version": self.manifest.version, "weight_checksum": self.manifest.sha256, "preprocess_version": PREPROCESS_VERSION, "raw_output": float(raw), "normalised_score": normalised * 100, "device": self.device, "analysed_at": datetime.now(UTC).isoformat(), "source": str(path)})
        return results

    @staticmethod
    def _preprocess(path: Path) -> np.ndarray:
        image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        if image is None: raise ValueError("Model input could not be decoded")
        image = cv2.cvtColor(cv2.resize(image, (224, 224), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB).astype(np.float32) / 255
        image = (image - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
        return image.transpose(2, 0, 1)


def hybrid_score(classical: float | None, learned: float | None, settings: MLSettings) -> float | None:
    if settings.mode == "classical": return classical
    if settings.mode == "learned": return learned if learned is not None else classical
    values, weights = [], []
    if classical is not None: values.append(classical); weights.append(settings.classical_weight)
    if learned is not None: values.append(learned); weights.append(settings.learned_weight)
    return round(sum(v * w for v, w in zip(values, weights)) / sum(weights), 2) if values else None


def context_adjustment(profile: str | None, metrics: dict) -> str:
    profile = profile if profile in CONTEXTS else "general"
    if profile == "night" and metrics.get("mean_luminance", 1) < .12: return "Dark luminance is tolerated by the Night context override."
    if profile == "portrait" and metrics.get("detail_concentration", 0) > 3: return "Concentrated detail is compatible with portrait depth of field."
    return f"{profile.title()} context uses the standard conservative interpretation."


class LearnedCoordinator:
    def __init__(self, factory, manifest_path: Path):
        self.factory, self.manifest_path = factory, manifest_path
        self.settings, self.cancelled, self.lock = MLSettings(), Event(), Lock()
        self.state = {"state": "idle", "completed": 0, "total": 0, "error": None}

    def status(self):
        with self.lock: return dict(self.state)

    def start(self):
        with self.lock:
            if self.state["state"] == "running": raise RuntimeError("Learned analysis is already running")
            self.state = {"state": "running", "completed": 0, "total": 0, "error": None}; self.cancelled = Event()
        Thread(target=self._run, daemon=True, name="frame-ml").start(); return self.status()

    def cancel(self): self.cancelled.set(); return self.status()

    def _run(self):
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from .models import Photo
        try:
            model = OnnxQualityModel(self.manifest_path, self.settings.device)
            with self.factory() as session: photos = list(session.scalars(select(Photo).options(selectinload(Photo.assets))))
            candidates = [(p.id, Path(next(a.source_path for a in p.assets if a.kind == "rendered"))) for p in photos if any(a.kind == "rendered" for a in p.assets)]
            with self.lock: self.state["total"] = len(candidates)
            for photo_id, path in candidates:
                if self.cancelled.is_set(): break
                result = model.predict([path], self.cancelled)[0]
                result["source_signature"] = f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
                with self.factory() as session:
                    photo = session.get(Photo, photo_id); photo.learned_json = json.dumps(result); photo.hybrid_score = hybrid_score(photo.technical_score, result["normalised_score"], self.settings); session.commit()
                with self.lock: self.state["completed"] += 1
            with self.lock: self.state["state"] = "cancelled" if self.cancelled.is_set() else "completed"
        except Exception as exc:
            with self.lock: self.state.update(state="failed", error=str(exc))
