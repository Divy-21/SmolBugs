"""Turns one window of accelerometer data (plus temperature) into numbers the models use.

Two layers:
1. window_features(): raw features for one window. Uses the team's FFT code
   (processing.analyze_axis) and adds time-domain stats (RMS, kurtosis, crest...).
2. relative_features(): divides those by the machine's own "normal" baseline, so a
   classifier trained on one install still works after the sensor is moved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, skew

from processing import FFTConfig, amplitude_spectrum, analyze_axis, dominant_frequency

from .settings import DRILL_FFT

AXES = ("x", "y", "z")
EPS = 1e-6


def time_features(signal: np.ndarray) -> dict[str, float]:
    x = np.asarray(signal, dtype=float)
    x = x - x.mean()  # remove gravity
    rms = float(np.sqrt(np.mean(x**2)))
    peak = float(np.max(np.abs(x)))
    if rms < 1e-9:  # flat signal, stats are undefined
        return {"rms": 0.0, "peak": 0.0, "std": 0.0, "crest": 0.0, "skew": 0.0, "kurtosis": 3.0}
    return {
        "rms": rms,
        "peak": peak,
        "std": float(np.std(x)),
        "crest": peak / rms,
        "skew": float(skew(x)),
        "kurtosis": float(kurtosis(x, fisher=False)),  # about 3 for normal vibration
    }


def estimate_run_hz(windows: list[np.ndarray], fs: float, cfg: FFTConfig = DRILL_FFT) -> float:
    """Running speed in Hz: median dominant frequency of the strongest axis over normal windows."""
    freqs = []
    for w in windows:
        axis = int(np.argmax(np.std(w, axis=0)))
        spec = amplitude_spectrum(w[:, axis], fs, cfg.detrend_type)
        f, _ = dominant_frequency(spec, cfg.min_freq_hz, min(cfg.max_freq_hz, fs / 2))
        freqs.append(f)
    return float(np.median(freqs))


def window_features(
    accel_g: np.ndarray,
    temp_c: float,
    temp_slope_c_per_min: float,
    run_hz: float | None,
    fs: float = DRILL_FFT.sample_rate_hz,
    cfg: FFTConfig = DRILL_FFT,
) -> dict[str, float]:
    """All raw features for one window. accel_g has shape (n, 3) with columns x, y, z in g."""
    feats: dict[str, float] = {}
    for i, ax in enumerate(AXES):
        sig = accel_g[:, i]
        for name, value in time_features(sig).items():
            feats[f"{ax}_{name}"] = value
        a = analyze_axis(sig, fs, cfg, rpm=run_hz * 60.0 if run_hz else None)
        feats[f"{ax}_dom_freq_hz"] = a["dominant_freq_hz"]
        feats[f"{ax}_dom_amp"] = a["dominant_amp_g"]
        feats[f"{ax}_velocity_mm_s"] = a["velocity_rms_mm_s"]
        for band, power in a["band_power_g2"].items():
            feats[f"{ax}_band_{band}"] = power
        for h in a.get("harmonics", []):
            feats[f"{ax}_h{h['order']}x"] = h["amp_g"]
    feats["temp_c"] = float(temp_c)
    feats["temp_slope_c_per_min"] = float(temp_slope_c_per_min)
    return feats


def make_baseline(raw: pd.DataFrame) -> pd.Series:
    """The machine's 'normal': median of every feature over the calibration windows."""
    return raw.median(numeric_only=True)


def _log_ratio(a, b):
    # 0 means "same as normal", 1 means double, -1 means half
    return np.log2((np.asarray(a, dtype=float) + EPS) / (float(b) + EPS))


def relative_features(raw: pd.DataFrame, baseline: pd.Series) -> pd.DataFrame:
    """Machine-independent features: everything compared to this machine's own baseline."""
    out = pd.DataFrame(index=raw.index)
    bands = sorted({c.split("_band_")[1] for c in raw.columns if "_band_" in c})
    for ax in AXES:
        for name in ("rms", "peak", "velocity_mm_s", "h1x", "h2x", "h3x"):
            col = f"{ax}_{name}"
            out[f"{ax}_{name}_rel"] = _log_ratio(raw[col], baseline[col])
        for band in bands:
            col = f"{ax}_band_{band}"
            out[f"{col}_rel"] = _log_ratio(raw[col], baseline[col])
        # These have no units, so they already mean the same thing on any machine
        out[f"{ax}_kurtosis"] = raw[f"{ax}_kurtosis"]
        out[f"{ax}_crest"] = raw[f"{ax}_crest"]
        out[f"{ax}_skew"] = raw[f"{ax}_skew"]
        out[f"{ax}_h2_over_h1"] = _log_ratio(raw[f"{ax}_h2x"], 1.0) - _log_ratio(raw[f"{ax}_h1x"], 1.0)
        out[f"{ax}_h3_over_h1"] = _log_ratio(raw[f"{ax}_h3x"], 1.0) - _log_ratio(raw[f"{ax}_h1x"], 1.0)
    out["temp_rise_c"] = raw["temp_c"] - baseline["temp_c"]
    out["temp_slope_c_per_min"] = raw["temp_slope_c_per_min"]
    return out.fillna(0.0)
