"""The live detector: learns a machine's normal on install, then checks every window.

    det = MachineGuardDetector.load("models/machineguard.joblib")
    det.calibrate(normal_windows)        # "learn normal" on install: [(accel, temp_c, temp_slope), ...]
    result = det.predict(accel_window)   # add temp_c, temp_slope if the model uses temperature
    if result["shutoff"]: cut power

Shutoff needs k of the last n windows to be faults (settings.SHUTOFF_K / SHUTOFF_N),
so a single bump never cuts the power.

Three opinions are combined:
1. Isolation forest: does this window look like normal? (catches anything unusual)
2. Classifier: which fault is it? (trained on recorded fault runs)
3. FFT rules: physics check (1x = imbalance, 2x = bent bit, spiky = loose, hot = overheat)
"""

from __future__ import annotations

import warnings
from collections import deque
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from .features import AXES, estimate_run_hz, make_baseline, relative_features, window_features
from .settings import DRILL_FFT, FAULT_LABELS, FS, NORMAL_LABELS, SHUTOFF_K, SHUTOFF_N

RECOMMEND = {
    "ok": "No action needed.",
    "off": "No action needed.",
    "imbalance": "Stop and check the chuck and bit for anything stuck to them or mounted off-centre.",
    "bent_bit": "Replace the bit. If a new bit still wobbles, have the chuck checked.",
    "loose_bit": "Tighten the chuck and check that the bit is seated all the way in.",
    "overheat": "Let the drill cool down, clean the air vents, and use lighter pressure.",
    "check": "Stop and inspect the drill. If nothing looks wrong, run it again so it can relearn normal.",
}

FRIENDLY = {
    "imbalance": "Imbalance: something on the chuck or bit is off-centre",
    "bent_bit": "Bent or crooked bit (misalignment)",
    "loose_bit": "Loose bit or chuck: rattling",
    "overheat": "Overheating: motor temperature climbing",
}


def rule_label(rel: pd.Series, axis: str) -> str | None:
    """Plain physics rules on the strongest axis. Thresholds are starting points:
    tune them once real fault recordings exist."""
    if "temp_rise_c" in rel.index and (rel["temp_slope_c_per_min"] > 3.0 or rel["temp_rise_c"] > 8.0):
        return "overheat"
    if rel[f"{axis}_kurtosis"] > 5.0 or rel[f"{axis}_crest"] > 5.0:
        return "loose_bit"
    if rel[f"{axis}_h2x_rel"] > 1.5 and rel[f"{axis}_h2_over_h1"] > -1.0:
        return "bent_bit"
    if rel[f"{axis}_h1x_rel"] > 1.5:  # 1x is about 3 times its normal size
        return "imbalance"
    return None


class MachineGuardDetector:
    def __init__(self, iforest, classifier, feature_columns: list[str], fault_confidence: float = 0.6,
                 shutoff_k: int = SHUTOFF_K, shutoff_n: int = SHUTOFF_N, meta: dict | None = None):
        if not 1 <= shutoff_k <= shutoff_n:
            raise ValueError("need 1 <= shutoff_k <= shutoff_n")
        self.iforest = iforest
        self.classifier = classifier
        self.feature_columns = list(feature_columns)
        self.fault_confidence = fault_confidence
        self.shutoff_k = shutoff_k
        self.shutoff_n = shutoff_n
        self.meta = dict(meta or {})
        self.baseline: pd.Series | None = None
        self.run_hz: float | None = None
        self.main_axis = "x"
        self._recent_faults: deque[bool] = deque(maxlen=shutoff_n)

    @property
    def uses_temperature(self) -> bool:
        return "temp_rise_c" in self.feature_columns

    # ---------- saving ----------
    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        meta = {"sklearn_version": sklearn.__version__, "fs": FS, **self.meta}
        joblib.dump({"iforest": self.iforest, "classifier": self.classifier,
                     "feature_columns": self.feature_columns, "fault_confidence": self.fault_confidence,
                     "shutoff_k": self.shutoff_k, "shutoff_n": self.shutoff_n, "meta": meta}, path)

    @classmethod
    def load(cls, path: str | Path) -> "MachineGuardDetector":
        d = joblib.load(path)
        meta = d.get("meta", {})
        if meta.get("fs", FS) != FS:
            raise ValueError(f"{path} was trained at {meta['fs']} Hz but settings.FS is {FS} Hz: retrain it")
        if meta.get("sklearn_version") not in (None, sklearn.__version__):
            warnings.warn(f"{path} was trained with scikit-learn {meta['sklearn_version']}, running "
                          f"{sklearn.__version__}; retrain if results look off", stacklevel=2)
        return cls(d["iforest"], d["classifier"], d["feature_columns"], d["fault_confidence"],
                   d.get("shutoff_k", SHUTOFF_K), d.get("shutoff_n", SHUTOFF_N), meta)

    # ---------- learn normal ----------
    def calibrate(self, windows, fs: float = FS) -> None:
        """windows: accel_g arrays (n, 3), or (accel_g, temp_c, temp_slope_c_per_min) tuples,
        from normal running (no load). Also clears the shutoff history."""
        items = [w if isinstance(w, tuple) else (w, None, None) for w in windows]
        if not items:
            raise ValueError("calibrate() needs at least one window of normal running")
        if self.uses_temperature and any(t is None for _, t, _ in items):
            raise ValueError("this model uses temperature: pass (accel_g, temp_c, temp_slope) tuples")
        self.run_hz = estimate_run_hz([a for a, _, _ in items], fs)
        raw = pd.DataFrame([window_features(a, t if self.uses_temperature else None, s, self.run_hz, fs)
                            for a, t, s in items])
        self.baseline = make_baseline(raw)
        self.main_axis = AXES[int(np.argmax([self.baseline[f"{ax}_rms"] for ax in AXES]))]
        self._recent_faults.clear()

    def reset_shutoff(self) -> None:
        """Forget recent fault windows (for example after the machine was restarted)."""
        self._recent_faults.clear()

    # ---------- check one window ----------
    def predict(self, accel_g: np.ndarray, temp_c: float | None = None,
                temp_slope_c_per_min: float | None = None, fs: float = FS) -> dict:
        if self.baseline is None:
            raise RuntimeError("call calibrate() with normal running data first")
        if self.uses_temperature and temp_c is None:
            raise ValueError("this model uses temperature: pass temp_c and temp_slope_c_per_min")
        t = temp_c if self.uses_temperature else None
        raw = pd.DataFrame([window_features(accel_g, t, temp_slope_c_per_min, self.run_hz, fs, DRILL_FFT)])
        rel = relative_features(raw, self.baseline)
        missing = [c for c in self.feature_columns if c not in rel.columns]
        if missing:
            raise ValueError(f"features missing compared with training: {missing[:5]}")
        rel = rel[self.feature_columns]
        row = rel.iloc[0]

        # Main axis under a quarter of its normal level: the drill is switched off
        if row[f"{self.main_axis}_rms_rel"] < -2.0:
            self._recent_faults.append(False)
            return {"status": "off", "label": "off", "message": "Machine off",
                    "recommendation": RECOMMEND["off"], "fault_now": False, "shutoff": False}

        anomaly_score = float(self.iforest.decision_function(rel)[0])  # below 0 = unusual
        is_anomaly = anomaly_score < 0
        proba = self.classifier.predict_proba(rel)[0]
        best = int(np.argmax(proba))
        label, confidence = str(self.classifier.classes_[best]), float(proba[best])
        rule = rule_label(row, self.main_axis)

        if label == "off":
            status, message = "off", "Machine off"
        elif label in NORMAL_LABELS and not is_anomaly:
            status, message = "ok", "Running normally"
        elif label in FAULT_LABELS and confidence >= self.fault_confidence and (is_anomaly or rule == label):
            status, message = "fault", FRIENDLY.get(label, label)
        else:
            status, message = "check", "Unusual vibration, type unknown: inspect the drill"

        fault_now = status == "fault"
        self._recent_faults.append(fault_now)
        shutoff = fault_now and sum(self._recent_faults) >= self.shutoff_k
        return {
            "status": status,
            "label": label,
            "confidence": round(confidence, 3),
            "anomaly_score": round(anomaly_score, 4),
            "rule_label": rule,
            "message": message,
            "recommendation": RECOMMEND[label] if status == "fault" else RECOMMEND[status],
            "fault_now": fault_now,
            "shutoff": shutoff,
        }
