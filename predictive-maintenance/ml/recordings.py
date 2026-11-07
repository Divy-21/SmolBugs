"""Loads recorded runs (one CSV per run) and turns them into a feature table.

File names must look like  S1_imbalance_03.csv  (session, label, run number).
Columns: t_us, ax, ay, az, temp_c   (acceleration in g, temperature in °C).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .features import estimate_run_hz, window_features
from .settings import BASELINE_LABEL, CSV_COLUMNS, FS, LABELS, STEP, TEMP_SLOPE_SECONDS, WINDOW

NAME_RE = re.compile(r"^S(?P<session>\d+)_(?P<label>[a-z_]+?)_(?P<run>\d+)\.csv$")


def parse_name(path: Path) -> dict:
    m = NAME_RE.match(path.name)
    if not m:
        raise ValueError(f"{path.name}: expected a name like S1_imbalance_03.csv")
    label = m["label"]
    if label not in LABELS:
        raise ValueError(f"{path.name}: unknown label {label!r}, expected one of {LABELS}")
    return {"session": int(m["session"]), "label": label, "run": int(m["run"]), "file": path.name}


def load_run(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in CSV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")
    return df


def check_timing(t_us: np.ndarray, fs: float = FS) -> dict:
    """Counts samples the ESP32 dropped, using its own timestamps."""
    dt = np.diff(np.asarray(t_us, dtype=float))
    expected = 1e6 / fs
    gaps = dt > 1.5 * expected
    dropped = int(np.sum(np.round(dt[gaps] / expected) - 1))
    actual_fs = 1e6 / np.median(dt) if dt.size else float("nan")
    return {"samples": int(len(t_us)), "gaps": int(gaps.sum()), "dropped": dropped, "actual_fs": float(actual_fs)}


def temp_slope(t_s: np.ndarray, temp: np.ndarray, end_idx: int, seconds: float = TEMP_SLOPE_SECONDS) -> float:
    """°C per minute over the last few seconds before end_idx."""
    start_idx = int(np.searchsorted(t_s, t_s[end_idx - 1] - seconds))
    t, y = t_s[start_idx:end_idx:50], temp[start_idx:end_idx:50]
    ok = np.isfinite(y)
    if ok.sum() < 3 or t[ok][-1] - t[ok][0] < 3.0:
        return 0.0
    return float(np.polyfit(t[ok], y[ok], 1)[0] * 60.0)


def run_windows(df: pd.DataFrame):
    """Yields (start_index, end_index) of each 1 s window with 50% overlap."""
    for start in range(0, len(df) - WINDOW + 1, STEP):
        yield start, start + WINDOW


def run_features(df: pd.DataFrame, run_hz: float | None) -> list[dict]:
    accel = df[["ax", "ay", "az"]].to_numpy(dtype=float)
    temp = df["temp_c"].to_numpy(dtype=float)
    t_s = (df["t_us"].to_numpy(dtype=float) - df["t_us"].iloc[0]) / 1e6
    rows = []
    for start, end in run_windows(df):
        t_mean = float(np.nanmean(temp[start:end])) if np.isfinite(temp[start:end]).any() else np.nan
        feats = window_features(accel[start:end], t_mean, temp_slope(t_s, temp, end), run_hz)
        feats["window_start_s"] = float(t_s[start])
        rows.append(feats)
    return rows


def build_feature_table(raw_dir: str | Path, verbose: bool = True) -> pd.DataFrame:
    """Every window of every run, with session/label/run columns and raw features."""
    paths = sorted(Path(raw_dir).glob("S*_*_*.csv"))
    if not paths:
        raise FileNotFoundError(f"no recordings like S1_normal_free_01.csv in {raw_dir}")
    runs = [(parse_name(p), load_run(p)) for p in paths]

    # Running speed per session, from that session's normal runs (like the device does on install)
    run_hz: dict[int, float | None] = {}
    for session in sorted({m["session"] for m, _ in runs}):
        normal = [df for m, df in runs if m["session"] == session and m["label"] == BASELINE_LABEL]
        windows = [df[["ax", "ay", "az"]].to_numpy()[s:e] for df in normal for s, e in run_windows(df)]
        run_hz[session] = estimate_run_hz(windows, FS) if windows else None
        if verbose:
            shown = f"{run_hz[session]:.2f} Hz ({run_hz[session] * 60:.0f} RPM)" if windows else "unknown (no normal_free runs)"
            print(f"session {session}: running speed {shown}")

    rows = []
    for meta, df in runs:
        timing = check_timing(df["t_us"].to_numpy())
        if verbose and timing["dropped"]:
            print(f"  warning {meta['file']}: {timing['dropped']} dropped samples in {timing['gaps']} gaps")
        for feats in run_features(df, run_hz[meta["session"]]):
            rows.append({**meta, **feats})
    table = pd.DataFrame(rows)
    if verbose:
        print(f"{len(table)} windows from {len(runs)} runs")
    return table
