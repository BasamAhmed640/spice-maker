"""Run LTspice decks and report switching facts from the .raw (read-only use of the product)."""

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

from boardmodeler.simulation.log import parse_log
from boardmodeler.simulation.ltspice import run_batch
from boardmodeler.simulation.raw import read_raw

LTSPICE = Path(r"C:\Users\basam\AppData\Local\Programs\ADI\LTspice\LTspice.exe")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trace(raw, name):
    for column, key in enumerate(raw.variables):
        if key.lower() == name.lower():
            return np.asarray(raw.data[:, column].real, dtype=float)
    return None


def facts(deck: Path, t0: float, t1: float) -> dict:
    run_dir = deck.parent / (deck.stem + "_run")
    run_dir.mkdir(exist_ok=True)
    started = time.monotonic()
    result = run_batch(LTSPICE, deck, run_dir, timeout_s=120)
    wall = time.monotonic() - started
    out = {"deck": deck.name, "deck_sha256": sha(deck), "exit": result.exit_code, "wall_s": round(wall, 2)}
    if result.log_path is not None:
        log = parse_log(result.log_path)
        out["log_sha256"] = sha(result.log_path)
        out["completed"] = log.completed
        out["convergence_issues"] = log.convergence_issues[:2]
    if result.raw_path is None or not result.raw_path.is_file():
        out["raw"] = "missing"
        return out
    out["raw_sha256"] = sha(result.raw_path)
    raw = read_raw(result.raw_path)
    t = np.abs(trace(raw, "time"))
    window = (t >= t0) & (t <= t1)
    ph = trace(raw, "V(ph)")
    rising = int(np.sum((ph[:-1] < 0.5 * 12) & (ph[1:] >= 0.5 * 12) & window[1:])) if ph is not None else None
    out["ph_rising_edges_in_window"] = rising
    out["window_s"] = [t0, t1]
    out["t_end_s"] = float(t[-1])
    for name in ("V(ph,sw)", "V(ph)", "V(sw)", "V(vout)", "V(ss)", "V(comp)", "V(xdut:run)", "V(xdut:en_ok)", "V(xdut:uv_ok)", "V(bm_fixture_ground)"):
        if name == "V(ph,sw)":
            a, b = trace(raw, "V(ph)"), trace(raw, "V(sw)")
            values = None if a is None or b is None else (a - b) * 50.0
            label = "I(Rsense)=V(ph,sw)*50 [A]"
        else:
            values = trace(raw, name)
            label = name
        if values is not None and window.any():
            out[label] = {"max": float(np.max(values[window])), "mean": float(np.mean(values[window])), "end": float(values[-1])}
    return out


if __name__ == "__main__":
    t0, t1 = float(sys.argv[1]), float(sys.argv[2])
    results = [facts(Path(arg).resolve(), t0, t1) for arg in sys.argv[3:]]
    print(json.dumps(results, indent=1))
