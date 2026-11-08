"""Loads recorded runs and turns them into a feature table.

One run = one folder written by the Bluetooth receiver:

    python -m ml.record --session 1 --label normal_free --seconds 300
      -> data/raw/S1_normal_free_01/
             windows/MG-001_w00012.npz   one Bluetooth window each (accel_g, sample_rate_hz)
             vibration.jsonl             one line per window (window_id, received_at_utc, temperature_c)
             temperature.jsonl           status readings every 0.5 s

Folder names must look like  S1_imbalance_03  (session, label, run number).
Each Bluetooth window is cut into analysis windows separately, so no analysis
window ever spans the gap between two Bluetooth windows.
"""

from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .features import estimate_run_hz, window_features
from .settings import (BASELINE_LABEL, FS, FS_TOLERANCE, LABELS, STEP, TEMP_SLOPE_MIN_READINGS,
                       TEMP_SLOPE_SECONDS, WINDOW)

NAME_RE = re.compile(r"^S(?P<session>\d+)_(?P<label>[a-z_]+?)_(?P<run>\d+)$")
WINDOW_FILE_RE = re.compile(r"_w(?P<id>\d+)\.npz$")


@dataclass
class Frame:
    """One Bluetooth window: a continuous block of samples."""
    window_id: int
    accel_g: np.ndarray            # (n, 3) in g
    fs: float
    temp_c: float | None = None
    temp_slope_c_per_min: float = 0.0


@dataclass
class Run:
    meta: dict
    frames: list[Frame] = field(default_factory=list)


def parse_name(path: Path) -> dict:
    path = Path(path)
    m = NAME_RE.match(path.name)
    if not m:
        raise ValueError(f"{path.name}: expected a folder name like S1_imbalance_03")
    label = m["label"]
    if label not in LABELS:
        raise ValueError(f"{path.name}: unknown label {label!r}, expected one of {LABELS}")
    return {"session": int(m["session"]), "label": label, "run": int(m["run"]), "file": path.name}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # a half-written last line if the receiver was killed
    return rows


def _time(stamp) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp)).timestamp()
    except (TypeError, ValueError):
        return None


def temp_slope(readings: list[tuple[float, float]], at_s: float,
               seconds: float = TEMP_SLOPE_SECONDS) -> float:
    """°C per minute over the readings in the `seconds` before at_s (0.0 if too few)."""
    pts = [(t, c) for t, c in readings if at_s - seconds <= t <= at_s]
    if len(pts) < TEMP_SLOPE_MIN_READINGS:
        return 0.0
    t, c = np.array(pts).T
    if t[-1] - t[0] < seconds / 3:
        return 0.0
    return float(np.polyfit(t - t[0], c, 1)[0] * 60.0)


def load_run(run_dir: str | Path, use_temp: bool = False) -> Run:
    run_dir = Path(run_dir)
    meta = parse_name(run_dir)
    files = sorted((run_dir / "windows").glob("*.npz"))
    if not files:
        raise ValueError(f"{run_dir.name}: no windows/*.npz files (was it recorded with --out?)")

    vib = {}
    for row in _read_jsonl(run_dir / "vibration.jsonl"):
        if "window_id" in row:
            vib[int(row["window_id"])] = row
    vib_lines = len(_read_jsonl(run_dir / "vibration.jsonl"))
    if vib_lines > len(files):
        warnings.warn(f"{run_dir.name}: {vib_lines} windows logged but only {len(files)} files; "
                      "the board probably restarted and overwrote some windows", stacklevel=2)

    readings = []
    if use_temp:
        for row in _read_jsonl(run_dir / "temperature.jsonl"):
            t = _time(row.get("received_at_utc"))
            if t is not None and row.get("temperature_c") is not None:
                readings.append((t, float(row["temperature_c"])))
        readings.sort()

    run = Run(meta)
    for path in files:
        m = WINDOW_FILE_RE.search(path.name)
        wid = int(m["id"]) if m else len(run.frames)
        with np.load(path) as d:
            accel = np.asarray(d["accel_g"], dtype=float)
            fs = float(d["sample_rate_hz"])
        if abs(fs - FS) > FS_TOLERANCE * FS:
            warnings.warn(f"{run_dir.name}/{path.name}: sample rate {fs:.0f} Hz, expected about {FS:.0f} Hz",
                          stacklevel=2)
        frame = Frame(wid, accel, fs)
        if use_temp:
            row = vib.get(wid, {})
            frame.temp_c = row.get("temperature_c")
            at = _time(row.get("received_at_utc"))
            if at is not None:
                frame.temp_slope_c_per_min = temp_slope(readings, at)
        run.frames.append(frame)
    return run


def frame_windows(n_samples: int):
    """(start, end) of each analysis window inside one Bluetooth window."""
    for start in range(0, n_samples - WINDOW + 1, STEP):
        yield start, start + WINDOW


def iter_windows(run: Run):
    """(frame, start, accel window) for every analysis window of a run."""
    for frame in run.frames:
        for s, e in frame_windows(len(frame.accel_g)):
            yield frame, s, frame.accel_g[s:e]


def find_runs(raw_dir: str | Path) -> list[Path]:
    return sorted(p for p in Path(raw_dir).glob("S*_*_*") if p.is_dir() and NAME_RE.match(p.name))


def build_feature_table(raw_dir: str | Path, use_temp: bool = False, verbose: bool = True) -> pd.DataFrame:
    """Every analysis window of every run, with session/label/run columns and raw features."""
    paths = find_runs(raw_dir)
    if not paths:
        raise FileNotFoundError(f"no run folders like S1_normal_free_01 in {raw_dir}")
    runs = [load_run(p, use_temp) for p in paths]

    # Running speed per session, from that session's normal runs (like the device does on install)
    run_hz: dict[int, float | None] = {}
    for session in sorted({r.meta["session"] for r in runs}):
        normal = [w for r in runs if r.meta["session"] == session and r.meta["label"] == BASELINE_LABEL
                  for _, _, w in iter_windows(r)]
        fs = next((f.fs for r in runs if r.meta["session"] == session for f in r.frames), FS)
        run_hz[session] = estimate_run_hz(normal, fs) if normal else None
        if verbose:
            shown = (f"{run_hz[session]:.2f} Hz ({run_hz[session] * 60:.0f} RPM)" if normal
                     else f"unknown (no {BASELINE_LABEL} runs)")
            print(f"session {session}: running speed {shown}")

    rows = []
    no_temp = 0
    for run in runs:
        for frame, start, w in iter_windows(run):
            temp = frame.temp_c if use_temp else None
            if use_temp and temp is None:
                no_temp += 1
                temp = float("nan")  # becomes "same as normal" in the relative features
            feats = window_features(w, temp, frame.temp_slope_c_per_min if use_temp else None,
                                    run_hz[run.meta["session"]], frame.fs)
            rows.append({**run.meta, "window_id": frame.window_id, "window_start_s": start / frame.fs, **feats})
    table = pd.DataFrame(rows)
    if verbose:
        frames = sum(len(r.frames) for r in runs)
        print(f"{len(table)} analysis windows from {frames} Bluetooth windows in {len(runs)} runs")
        if no_temp:
            print(f"  warning: {no_temp} windows had no temperature reading")
    return table
