from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Literal
from sqlalchemy.orm import Session, sessionmaker
from .ingestion import scan
from .quality import QualityConfig


@dataclass
class JobState:
    state: Literal["idle", "running", "completed", "cancelled", "failed"] = "idle"
    source: str | None = None
    completed: int = 0
    total: int = 0
    current_file: str | None = None
    error: str | None = None


class ScanCoordinator:
    def __init__(self, factory: sessionmaker[Session], thumbnail_dir: Path):
        self.factory, self.thumbnail_dir = factory, thumbnail_dir
        self.quality_config = QualityConfig()
        self._state, self._cancel, self._lock = JobState(), Event(), Lock()

    def start(self, source: Path) -> dict:
        with self._lock:
            if self._state.state == "running":
                raise RuntimeError("A scan is already running")
            self._state, self._cancel = JobState(state="running", source=str(source)), Event()
        Thread(target=self._run, args=(source,), daemon=True, name="frame-scan").start()
        return self.status()

    def _run(self, source: Path) -> None:
        try:
            completed, total = scan(source, self.factory, self.thumbnail_dir, self._cancel, self._progress, self.quality_config)
            with self._lock:
                self._state.completed, self._state.total = completed, total
                self._state.state = "cancelled" if self._cancel.is_set() else "completed"
        except Exception as exc:
            with self._lock:
                self._state.state, self._state.error = "failed", str(exc)

    def _progress(self, completed: int, total: int, current: str) -> None:
        with self._lock:
            self._state.completed, self._state.total, self._state.current_file = completed, total, current

    def cancel(self) -> dict:
        self._cancel.set()
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return asdict(self._state)
