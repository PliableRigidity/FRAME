"""Held-out evaluation for exported pairwise preference JSON."""
import argparse, json, random, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
from frame.preferences import train

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("dataset", type=Path); parser.add_argument("--seed", type=int, default=7); args=parser.parse_args()
    rows=json.loads(args.dataset.read_text())["examples"]; random.Random(args.seed).shuffle(rows); split=max(1,int(len(rows)*.8)); training,held=rows[:split],rows[split:]
    converted=[(np.asarray(r["a"]),np.asarray(r["b"]),r["choice"]) for r in training]; model=train(converted)
    if not model or not held: print(json.dumps({"trained":False,"note":"Insufficient held-out examples"})); return
    weights=np.asarray(model["weights"]); correct=sum(((weights@(np.asarray(r["a"])-np.asarray(r["b"])))>=0)==(r["choice"]=="a") for r in held if r["choice"] in {"a","b"})
    evaluated=sum(r["choice"] in {"a","b"} for r in held); print(json.dumps({"trained":True,"training":len(training),"held_out":evaluated,"agreement":correct/evaluated if evaluated else None,"warning":"Requires representative human labels."},indent=2))
if __name__=="__main__": main()
