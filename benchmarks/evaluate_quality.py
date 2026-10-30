"""Evaluate FRAME recommendations against a labelled CSV without modifying photos.

CSV columns: path,label where label is KEEP, REVIEW, or REJECT.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
from frame.quality import analyse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", type=Path)
    parser.add_argument("--output", type=Path, default=Path("quality-evaluation.json"))
    args = parser.parse_args()
    rows = list(csv.DictReader(args.labels.open(encoding="utf-8-sig")))
    confusion: dict[str, int] = {}
    false_rejects = defects = detected_defects = reviews = 0
    durations = []
    for row in rows:
        started = time.perf_counter()
        predicted = analyse(Path(row["path"]))["decision"]["recommendation"]
        durations.append(time.perf_counter() - started)
        actual = row["label"].upper()
        confusion[f"{actual}->{predicted}"] = confusion.get(f"{actual}->{predicted}", 0) + 1
        false_rejects += predicted == "REJECT" and actual != "REJECT"
        defects += actual == "REJECT"
        detected_defects += actual == "REJECT" and predicted in {"REVIEW", "REJECT"}
        reviews += predicted == "REVIEW"
    count = len(rows)
    report = {
        "images": count, "confusion": confusion,
        "false_rejection_rate": false_rejects / count if count else None,
        "defect_detection_recall": detected_defects / defects if defects else None,
        "review_fraction": reviews / count if count else None,
        "mean_seconds_per_image": sum(durations) / count if count else None,
        "note": "Results require representative human labels; no real-world calibration is bundled.",
    }
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
