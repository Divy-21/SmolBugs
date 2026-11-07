"""Checks the bearing wear warning on real run-to-failure data.

Dataset: "Ball Bearing Vibration, and Temperature Run-to-Failure Dataset" (Kaggle,
Jung et al. 2023). A bearing ran at about 1775 RPM under heavy load for 128 hours
until it failed. One recording per hour: x/y vibration and bearing + room
temperature at 25.6 kHz.

To match our MPU-6050, every recording is cut down to 1000 Hz (the same bandwidth
our sensor sees) before it goes through the same features as the drill.

    python -m ml.bearing_validation extract --zip "C:/Users/rimsh/Downloads/archive.zip"
    python -m ml.bearing_validation analyze

extract can be stopped and re-run; it skips hours it already saved.
"""

from __future__ import annotations

import argparse
import re
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import resample_poly

from .features import time_features
from .settings import FS, STEP, WINDOW

SOURCE_FS = 25_600
SECONDS = 10                     # 10 s from each hourly recording
RUN_HZ = 1775 / 60               # bearing test speed, close to the drill's
CACHE = Path("data/bearing")
NAME_RE = re.compile(r"LogFile_(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.csv$")


def extract(zip_path: str, limit_s: float = 150.0) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(zip_path)
    names = sorted(n for n in z.namelist() if NAME_RE.search(n))
    start = time.monotonic()
    done = 0
    for name in names:
        stamp = NAME_RE.search(name)[1]
        out = CACHE / f"{stamp}.npz"
        if out.exists():
            done += 1
            continue
        if time.monotonic() - start > limit_s:
            break
        df = pd.read_csv(z.open(name), header=None, nrows=SOURCE_FS * SECONDS)
        vib = df.iloc[:, :2].to_numpy(dtype=float)
        vib_1k = resample_poly(vib, up=5, down=128, axis=0)  # 25.6 kHz -> 1 kHz with anti-alias filter
        np.savez(out, vib=vib_1k, bearing_c=float(df.iloc[:, 2].mean()), room_c=float(df.iloc[:, 3].mean()))
        done += 1
    print(f"{done}/{len(names)} hours extracted to {CACHE}")


def load_hours() -> pd.DataFrame:
    rows = []
    for path in sorted(CACHE.glob("*.npz")):
        d = np.load(path)
        rows.append({"stamp": path.stem, "vib": d["vib"], "bearing_c": float(d["bearing_c"]), "room_c": float(d["room_c"])})
    df = pd.DataFrame(rows)
    t0 = pd.to_datetime(df["stamp"].iloc[0], format="%Y-%m-%d-%H-%M-%S")
    df["hour"] = (pd.to_datetime(df["stamp"], format="%Y-%m-%d-%H-%M-%S") - t0).dt.total_seconds() / 3600
    return df


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--zip", required=True)
    e.add_argument("--limit-s", type=float, default=1e9, help="stop after this many seconds (re-run to continue)")
    sub.add_parser("analyze")
    args = p.parse_args()
    if args.cmd == "extract":
        extract(args.zip, args.limit_s)
    else:
        from .wear import analyze_bearing
        analyze_bearing(load_hours())


if __name__ == "__main__":
    main()
