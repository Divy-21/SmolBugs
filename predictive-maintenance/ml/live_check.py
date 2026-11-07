"""Replays recorded runs through the detector exactly like the device would.

    python -m ml.live_check --raw data/fake/raw --session 3

1. Learns normal from the first normal_free run of that session (the install step).
2. Feeds every other run window by window and shows what the device would say.
Note: the saved model was trained on all sessions, so for an honest score use the
held-out numbers that train.py prints. This script is for checking the live logic.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from .detector import MachineGuardDetector
from .recordings import load_run, parse_name, run_windows, temp_slope
from .settings import BASELINE_LABEL


def windows_of(df):
    accel = df[["ax", "ay", "az"]].to_numpy(dtype=float)
    temp = df["temp_c"].to_numpy(dtype=float)
    t_s = (df["t_us"].to_numpy(dtype=float) - df["t_us"].iloc[0]) / 1e6
    for s, e in run_windows(df):
        yield accel[s:e], float(np.nanmean(temp[s:e])), temp_slope(t_s, temp, e)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw", default="data/raw")
    p.add_argument("--session", type=int, required=True)
    p.add_argument("--models", default="models/machineguard.joblib")
    args = p.parse_args()

    paths = sorted(Path(args.raw).glob(f"S{args.session}_*_*.csv"))
    runs = [(parse_name(path), load_run(path)) for path in paths]
    calib = [(m, df) for m, df in runs if m["label"] == BASELINE_LABEL]
    if not calib:
        raise SystemExit(f"session {args.session} has no {BASELINE_LABEL} run to calibrate on")

    det = MachineGuardDetector.load(args.models)
    calib_meta, calib_df = calib[0]
    det.calibrate(list(windows_of(calib_df)))
    print(f"learned normal from {calib_meta['file']}: running speed {det.run_hz:.2f} Hz, main axis {det.main_axis}\n")

    print(f"{'file':<28}{'what the device says (most common)':<62}{'shutoff'}")
    for meta, df in runs:
        if meta["file"] == calib_meta["file"]:
            continue
        results = [det.predict(*w) for w in windows_of(df)]
        status, message = Counter((r["status"], r["message"]) for r in results).most_common(1)[0][0]
        shut = sum(r["shutoff"] for r in results)
        print(f"{meta['file']:<28}{status + ': ' + message:<62}{shut}/{len(results)} windows")


if __name__ == "__main__":
    main()
