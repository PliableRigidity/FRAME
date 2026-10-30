from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    thumbnail_dir: Path

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(os.environ.get("FRAME_DATA_DIR", ".frame-data")).resolve()
        return cls(data_dir, os.environ.get("FRAME_DATABASE_URL", f"sqlite:///{data_dir / 'frame.db'}"), data_dir / "thumbnails")
