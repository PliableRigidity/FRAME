"""Compare selection proxies on an existing FRAME database.

Proxy metrics are not substitutes for agreement with a human-curated collection.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from frame.models import Photo
from frame.selection import SelectionRequest, collection_metrics, select_collection


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("database", type=Path); parser.add_argument("--target", type=int, default=50); parser.add_argument("--human-selection", type=Path)
    args = parser.parse_args(); engine = create_engine(f"sqlite:///{args.database.resolve()}")
    with Session(engine) as session: photos = list(session.scalars(select(Photo)))
    results = {}
    for method in ("random", "quality", "grouped", "diversity"):
        tracemalloc.start(); started = time.perf_counter()
        if method == "random":
            chosen = random.Random(7).sample(photos, min(args.target, len(photos)))
        else:
            chosen = [p for p, _ in select_collection(photos, SelectionRequest(args.target, method))]
        elapsed = time.perf_counter() - started; _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
        metrics = collection_metrics(chosen, photos); metrics.update(runtime_seconds=elapsed, peak_memory_bytes=peak, target_adherence=len(chosen) == min(args.target, len(photos)))
        results[method] = metrics
    if args.human_selection:
        human = set(json.loads(args.human_selection.read_text())["photo_ids"])
        for method, metrics in results.items():
            chosen = set()  # IDs can be added to reports when human evaluation is run.
            metrics["human_agreement_note"] = "Human agreement requires retaining selected IDs; proxy run only."
    print(json.dumps({"results": results, "warning": "Redundancy and coverage are proxy metrics, not perceptual superiority."}, indent=2))


if __name__ == "__main__": main()
