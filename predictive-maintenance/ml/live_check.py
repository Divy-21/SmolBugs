"""Replays recorded runs through the detector the way the device would.

    python -m ml.live_check --raw data/raw --session 3

1. Learns normal from the first normal_free run of that session (the install step).
2. Feeds every other run window by window and shows what the device would say,
   including how many windows would have cut the power (k-of-n shutoff).
Note: the saved model was trained on all sessions, so for an honest score use the
held-out numbers that train.py prints. This script is for checking the live logic.
"""

from __future__ import annotations

import argparse
from collections import Counter

from .detector import MachineGuardDetector
from .recordings import find_runs, iter_windows, load_run, parse_name
from .settings import BASELINE_LABEL


def windows_of(run, use_temp: bool):
    for frame, _, w in iter_windows(run):
        if use_temp:
            yield (w, frame.temp_c, frame.temp_slope_c_per_min), frame.fs
        else:
            yield w, frame.fs


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw", default="data/raw")
    p.add_argument("--session", type=int, required=True)
    p.add_argument("--models", default="models/machineguard.joblib")
    args = p.parse_args(argv)

    det = MachineGuardDetector.load(args.models)
    paths = [x for x in find_runs(args.raw) if parse_name(x)["session"] == args.session]
    runs = [load_run(x, det.uses_temperature) for x in paths]
    calib = [r for r in runs if r.meta["label"] == BASELINE_LABEL]
    if not calib:
        raise SystemExit(f"session {args.session} has no {BASELINE_LABEL} run to calibrate on")

    first = calib[0]
    items = list(windows_of(first, det.uses_temperature))
    det.calibrate([w for w, _ in items], fs=items[0][1])
    print(f"learned normal from {first.meta['file']}: running speed {det.run_hz:.2f} Hz, main axis {det.main_axis}\n")

    print(f"{'run':<26}{'what the device says (most common)':<62}{'shutoff'}")
    for run in runs:
        if run is first:
            continue
        det.reset_shutoff()  # fresh shutoff history per run
        results = []
        for w, fs in windows_of(run, det.uses_temperature):
            results.append(det.predict(*w, fs=fs) if isinstance(w, tuple) else det.predict(w, fs=fs))
        status, message = Counter((r["status"], r["message"]) for r in results).most_common(1)[0][0]
        shut = sum(r["shutoff"] for r in results)
        print(f"{run.meta['file']:<26}{status + ': ' + message:<62}{shut}/{len(results)} windows")


if __name__ == "__main__":
    main()
