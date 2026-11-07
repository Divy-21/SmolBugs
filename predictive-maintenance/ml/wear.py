"""Slow wear warning (for example a worn bearing).

Faults like imbalance show up instantly. Wear creeps in over days, so instead of
judging one window, WearTracker judges each period of use (for example each hour):

1. Learn normal: an isolation forest is trained on the first hours of use.
2. Every later hour: what share of its windows look unusual?
3. Smooth that over the last few hours so one odd reading does not trigger it.
4. Most windows unusual for several hours in a row: "Wear likely, schedule service".
   Temperature far above its normal rise, or vibration several times normal:
   "Critical, stop now".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from processing import analyze_axis

from .features import time_features
from .settings import DRILL_FFT, FS, STEP, WINDOW

EPS = 1e-6
RECOMMEND_WEAR = "Bearing wear likely: schedule service or replace the bearing soon."
RECOMMEND_CRITICAL = "Critical: stop the machine now. Temperature or vibration far above normal."


def wear_window_features(accel: np.ndarray, temp_rise_c: float, run_hz: float) -> list[dict]:
    """Features for every 1 s window of one period. accel has one column per axis, in any unit."""
    rows = []
    for start in range(0, len(accel) - WINDOW + 1, STEP):
        w = accel[start : start + WINDOW]
        feats: dict[str, float] = {"temp_rise_c": float(temp_rise_c)}
        for i in range(w.shape[1]):
            for name, value in time_features(w[:, i]).items():
                feats[f"a{i}_{name}"] = value
            a = analyze_axis(w[:, i], FS, DRILL_FFT, rpm=run_hz * 60)
            feats[f"a{i}_velocity"] = a["velocity_rms_mm_s"]
            for band, power in a["band_power_g2"].items():
                feats[f"a{i}_band_{band}"] = power
        rows.append(feats)
    return rows


def wear_relative(raw: pd.DataFrame, baseline: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame(index=raw.index)
    for col in raw.columns:
        if col == "temp_rise_c":
            out[col] = raw[col] - baseline[col]
        elif col.endswith(("_kurtosis", "_crest", "_skew")):
            out[col] = raw[col]
        else:
            out[col] = np.log2((raw[col] + EPS) / (baseline[col] + EPS))
    return out.fillna(0.0)


class WearTracker:
    def __init__(self, smooth_periods: int = 6, warn_fraction: float = 0.5, hold_periods: int = 3,
                 critical_temp_rise_c: float = 15.0, critical_vibration_ratio: float = 3.0):
        self.smooth_periods = smooth_periods
        self.warn_fraction = warn_fraction
        self.critical_temp_rise_c = critical_temp_rise_c
        self.critical_log2 = float(np.log2(critical_vibration_ratio))
        self.hold_periods = hold_periods  # must stay above the threshold this many periods in a row
        self.history: list[float] = []
        self.above = 0

    def learn_normal(self, raw_windows: pd.DataFrame) -> None:
        self.baseline = raw_windows.median()
        rel = wear_relative(raw_windows, self.baseline)
        self.columns = list(rel.columns)
        self.iforest = IsolationForest(n_estimators=300, contamination="auto", random_state=0).fit(rel)

    def add_period(self, raw_windows: pd.DataFrame) -> dict:
        rel = wear_relative(raw_windows, self.baseline)[self.columns]
        fraction = float(np.mean(self.iforest.predict(rel) == -1))
        self.history.append(fraction)
        smoothed = float(np.median(self.history[-self.smooth_periods :]))
        temp_rise = float(rel["temp_rise_c"].median())
        rms_cols = [c for c in rel.columns if c.endswith("_rms")]
        vib_ratio_log2 = float(rel[rms_cols].median().max())

        self.above = self.above + 1 if smoothed >= self.warn_fraction else 0
        if temp_rise > self.critical_temp_rise_c or vib_ratio_log2 > self.critical_log2:
            status, rec = "critical", RECOMMEND_CRITICAL
        elif self.above >= self.hold_periods:
            status, rec = "wear", RECOMMEND_WEAR
        else:
            status, rec = "ok", "No action needed."
        return {"unusual_fraction": fraction, "smoothed": smoothed, "temp_rise_above_normal_c": temp_rise,
                "vibration_vs_normal": float(2 ** vib_ratio_log2), "status": status, "recommendation": rec}


def analyze_bearing(hours: pd.DataFrame, learn_hours: float = 24.0, out_png: str = "reports/bearing_wear_warning.png") -> pd.DataFrame:
    """Runs the tracker over the run-to-failure data hour by hour and draws the pitch chart."""
    from pathlib import Path

    run_hz = 1775 / 60
    per_hour = []
    for _, h in hours.iterrows():
        vib = h["vib"][~np.isnan(h["vib"]).any(axis=1)]
        per_hour.append(pd.DataFrame(wear_window_features(vib, h["bearing_c"] - h["room_c"], run_hz)))

    learn = hours["hour"] < learn_hours
    tracker = WearTracker()
    tracker.learn_normal(pd.concat([w for w, l in zip(per_hour, learn) if l], ignore_index=True))

    rows = []
    for (_, h), windows, is_learning in zip(hours.iterrows(), per_hour, learn):
        r = tracker.add_period(windows)
        if is_learning:
            r["status"] = "learning"
        rows.append({"hour": h["hour"], **r})
    result = pd.DataFrame(rows)

    end = result["hour"].max()
    first_wear = result[result["status"].isin(["wear", "critical"])]["hour"].min()
    first_critical = result[result["status"] == "critical"]["hour"].min()
    print(f"test length {end:.0f} h, learned normal from the first {learn_hours:.0f} h")
    print(f"first wear warning (held 3 h): hour {first_wear:.0f}  ({end - first_wear:.0f} h before the test ended at failure)")
    print(f"first critical warning: hour {first_critical:.0f}  ({end - first_critical:.0f} h before the end)")
    later = result[result["hour"] >= first_wear]
    print(f"after the first warning, {100 * later['status'].isin(['wear', 'critical']).mean():.0f}% of hours stayed in warning")
    print("\nhour  unusual  smoothed  temp rise  vibration  status")
    for _, r in result.iloc[::4].iterrows():
        print(f"{r['hour']:4.0f}  {r['unusual_fraction']:6.0%}  {r['smoothed']:7.0%}  {r['temp_rise_above_normal_c']:+7.1f}C  "
              f"{r['vibration_vs_normal']:7.2f}x  {r['status']}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
    ax1.bar(result["hour"], 100 * result["unusual_fraction"], width=0.9, color="#c9d3df", label="Windows that look unusual (each hour)")
    ax1.plot(result["hour"], 100 * result["smoothed"], color="#1f4e79", lw=2.2, label="Smoothed over 6 hours")
    ax1.axhline(100 * tracker.warn_fraction, color="#b45f06", ls="--", lw=1.2, label="Wear warning threshold")
    ax1.axvspan(0, learn_hours, color="#e8f1e4", label="Learning normal")
    ax1.set_ylabel("% unusual")
    ax1.set_ylim(0, 105)
    ax1.legend(loc="upper left", fontsize=8, frameon=False)
    ax2.plot(hours["hour"], hours["bearing_c"] - hours["room_c"], color="#a61c00", lw=2, label="Bearing temperature above room (°C)")
    ax2.set_ylabel("°C above room")
    ax2.set_xlabel("Hours of running")
    ax2.legend(loc="upper left", fontsize=8, frameon=False)
    for ax in (ax1, ax2):
        ax.axvline(first_wear, color="#b45f06", lw=1.5)
        ax.axvline(end, color="black", lw=1.5)
        ax.spines[["top", "right"]].set_visible(False)
    ax1.text(first_wear, 102, f" Wear warning (hour {first_wear:.0f})", color="#b45f06", fontsize=9, va="bottom")
    ax1.text(end, 102, "Failure ", color="black", fontsize=9, ha="right", va="bottom")
    fig.suptitle(f"Real bearing run-to-failure test: warning {end - first_wear:.0f} hours before failure", fontsize=12, x=0.01, ha="left")
    fig.tight_layout()
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=160)
    print(f"\nchart saved to {out_png}")
    return result
