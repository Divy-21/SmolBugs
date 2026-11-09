"""Records one labelled run from the board over Bluetooth into data/raw.

    python -m ml.record --session 1 --label normal_free --seconds 300
    python -m ml.record --session 1 --label imbalance --seconds 300 --rpm 1800

This is the normal Bluetooth receiver (ingest/ble_receiver.py) with --out set to
the right folder: data/raw/S1_normal_free_01. The run number is picked
automatically (next free one). Each Bluetooth window holds 2.56 s of samples and
arrives every 10 s, so a 300 s run gives about 30 windows.

No firmware change is needed if the receiver already prints "Vib window" lines.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from .recordings import NAME_RE
from .settings import FS, FS_TOLERANCE, LABELS

WINDOW_PERIOD_S = 10.0
MIN_WINDOWS = 10


def next_run_number(out: Path, session: int, label: str) -> int:
    taken = []
    for p in out.glob(f"S{session}_{label}_*"):
        m = NAME_RE.match(p.name)
        if p.is_dir() and m and m["label"] == label:
            taken.append(int(m["run"]))
    return max(taken, default=0) + 1


def run_folder(out: Path, session: int, label: str) -> Path:
    return out / f"S{session}_{label}_{next_run_number(out, session, label):02d}"


def receiver_args(folder: Path, seconds: float, rpm: float | None, machine_id: str,
                  cloud: bool = False) -> list[str]:
    args = ["--out", str(folder), "--duration", str(seconds), "--machine-id", machine_id]
    if rpm:
        args += ["--rpm", str(rpm)]
    if cloud:
        args.append("--cloud")  # also upload live: device key from DEVICE_KEY or smolbugs.env
    return args


def check_run(folder: Path, seconds: float) -> list[str]:
    """Problems worth re-recording for. Empty list means the run looks fine."""
    files = sorted((folder / "windows").glob("*.npz"))
    expected = int(seconds // WINDOW_PERIOD_S)
    problems = []
    if not files:
        return ["no vibration windows were saved. If the receiver said 'Vibration characteristic not "
                "available' or 'older firmware', the board needs the machine1 firmware for vibration."]
    if len(files) < 0.8 * expected:
        problems.append(f"only {len(files)} windows saved, expected about {expected}: "
                        "windows were lost (move the laptop closer to the board?)")
    if len(files) < MIN_WINDOWS:
        problems.append(f"only {len(files)} windows: record for longer (at least {MIN_WINDOWS * 10:.0f} s)")
    with np.load(files[0]) as d:
        fs = float(d["sample_rate_hz"])
    if abs(fs - FS) > FS_TOLERANCE * FS:
        problems.append(f"sample rate {fs:.0f} Hz, expected about {FS:.0f} Hz: check settings.FS")
    return problems


def main(argv: list[str] | None = None, receiver_main=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--session", type=int, required=True, help="one sensor mounting; re-clip between sessions")
    p.add_argument("--label", choices=LABELS, required=True)
    p.add_argument("--seconds", type=float, default=300.0)
    p.add_argument("--rpm", type=float, default=1200.0,
                   help="chuck speed; the drill runs at 1200 rpm (20 Hz; its motor shows at 60 Hz = 3x)")
    p.add_argument("--machine-id", default="MG-001")
    p.add_argument("--out", default="data/raw")
    p.add_argument("--countdown", type=int, default=3, help="seconds to get ready before recording")
    p.add_argument("--cloud", action="store_true",
                   help="also upload to the SmolBugs cloud while recording (dashboard + phone alerts)")
    args = p.parse_args(argv)
    if args.seconds < MIN_WINDOWS * WINDOW_PERIOD_S:
        p.error(f"--seconds must be at least {MIN_WINDOWS * WINDOW_PERIOD_S:.0f}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    folder = run_folder(out, args.session, args.label)
    for i in range(args.countdown, 0, -1):
        print(f"recording {folder.name} in {i}...", flush=True)
        time.sleep(1)

    if receiver_main is None:
        from ingest.ble_receiver import main as receiver_main
    receiver_main(receiver_args(folder, args.seconds, args.rpm, args.machine_id, args.cloud))

    problems = check_run(folder, args.seconds)
    if problems:
        print(f"\nWARNING for {folder.name}:")
        for problem in problems:
            print(f"  - {problem}")
        print("Delete the folder and record it again if this run is not usable.")
        return 1
    print(f"\nsaved {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
