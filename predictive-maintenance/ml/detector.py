"""The live detector: learns a machine's normal on install, then checks every window.

    det = MachineGuardDetector.load("models/machineguard.joblib")
    det.calibrate(first_60_seconds_of_windows)       # "learn normal" on install
    result = det.predict(accel_window, temp_c, temp_slope)
    if result["shutoff"]: cut power

Three opinions are combined:
1. Isolation forest: does this window look like normal? (catches anything unusual)
2. Classifier: which fault is it? (trained on recorded fault runs)
3. FFT rules: physics check (1x = imbalance, 2x = bent bit, spiky = loose, hot = overheat)
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .features import AXES, estimate_run_hz, make_baseline, relative_features, window_features
from .settings import DRILL_FFT, FAULT_LABELS, FS, NORMAL_LABELS

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
    if rel["temp_slope_c_per_min"] > 3.0 or rel["temp_rise_c"] > 8.0:
        return "overheat"
    if rel[f"{axis}_kurtosis"] > 5.0 or rel[f"{axis}_crest"] > 5.0:
        return "loose_bit"
    if rel[f"{axis}_h2x_rel"] > 1.5 and rel[f"{axis}_h2_over_h1"] > -1.0:
        return "bent_bit"
    if rel[f"{axis}_h1x_rel"] > 1.5:  # 1x is about 3 times its normal size
        return "imbalance"
    return None


class MachineGuardDetector:
    def __init__(self, iforest, classifier, feature_columns: list[str], fault_confidence: float = 0.6):
        self.iforest = iforest
        self.classifier = classifier
        self.feature_columns = list(feature_columns)
        self.fault_confidence = fault_confidence
        self.baseline: pd.Series | None = None
        self.run_hz: float | None = None
        self.main_axis = "x"

    # ---------- saving ----------
    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"iforest": self.iforest, "classifier": self.classifier,
                     "feature_columns": self.feature_columns, "fault_confidence": self.fault_confidence}, path)

    @classmethod
    def load(cls, path: str | Path) -> "MachineGuardDetector":
        d = joblib.load(path)
        return cls(d["iforest"], d["classifier"], d["feature_columns"], d["fault_confidence"])

    # ---------- learn normal ----------
    def calibrate(self, windows: list[tuple[np.ndarray, float, float]]) -> None:
        """windows: (accel_g (n,3), temp_c, temp_slope_c_per_min) from about 60 s of normal running."""
        accels = [a for a, _, _ in windows]
        self.run_hz = estimate_run_hz(accels, FS)
        raw = pd.DataFrame([window_features(a, t, s, self.run_hz) for a, t, s in windows])
        self.baseline = make_baseline(raw)
        self.main_axis = AXES[int(np.argmax([self.baseline[f"{ax}_rms"] for ax in AXES]))]

    # ---------- check one window ----------
    def predict(self, accel_g: np.ndarray, temp_c: float, temp_slope_c_per_min: float) -> dict:
        if self.baseline is None:
            raise RuntimeError("call calibrate() with normal running data first")
        raw = pd.DataFrame([window_features(accel_g, temp_c, temp_slope_c_per_min, self.run_hz, FS, DRILL_FFT)])
        rel = relative_features(raw, self.baseline).reindex(columns=self.feature_columns, fill_value=0.0)
        row = rel.iloc[0]

        # Main axis under a quarter of its normal level: the drill is switched off
        if row[f"{self.main_axis}_rms_rel"] < -2.0:
            return {"status": "off", "label": "off", "message": "Machine off",
                    "recommendation": RECOMMEND["off"], "shutoff": False}

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
        return {
            "status": status,
            "label": label,
            "confidence": round(confidence, 3),
            "anomaly_score": round(anomaly_score, 4),
            "rule_label": rule,
            "message": message,
            "recommendation": RECOMMEND[label] if status == "fault" else RECOMMEND[status],
            "shutoff": status == "fault",
        }
