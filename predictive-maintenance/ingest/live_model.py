"""Runs Xavion's trained model (ml/detector.py) on every vibration window, live, in the receiver.

The model judges a drill against that drill's own normal, so it learns normal first: the first
LEARN_WINDOWS windows in which the drill is running (not switched off) become the baseline. The
baseline is saved next to the model (models/normal-<machine>.joblib, ignored by git), so a
reconnect or a restart reuses it. After moving the sensor, start the receiver with --relearn.

Each 10 s Bluetooth window is cut into the model's short analysis windows (7 of them, see
ml/settings.py), each is judged, and the window's answer is the most common one.

What it adds to each window sent to the cloud, under "ml":
    {"state": "learning" | "off" | "ok" | "check" | "fault", "label": "normal_load" | None,
     "confidence": 0.94 | None, "message": "Running normally", "recommendation": "..." | None,
     "agree": 6, "of": 7}

It must never stop the receiver: if scikit-learn or the model file is missing, load() says so
and returns None, and the receiver carries on without it.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np

DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "machineguard.joblib"
LEARN_WINDOWS = 3          # Bluetooth windows of normal running (about 30 s)
# Learn only from windows that are clearly the motor running: real drill, Mon Nov 9, the first live
# run learned from a window at 4.8 mm/s / 4 Hz, which was the drill being moved by hand. Off is
# 0.6-0.8 mm/s, moved by hand 1-5 mm/s at 2-4 Hz, running 8-22 mm/s at 67-200 Hz.
LEARN_ABOVE_MM_S = 6.0
LEARN_ABOVE_HZ = 15.0      # same line as the cloud's "being moved" (cloud/src/lib/runState.ts)
# The model was trained on one session and has never seen strong vibration: outside what it knows,
# its classifier sometimes answers "off" (at ~35% confidence) for a drill shaking at 17-210 mm/s.
# When it says off but the FFT measures clearly-running vibration (real drill: off 0.6-0.8 mm/s,
# moved by hand 1-5, running 8-15), the answer becomes "check": something unusual, inspect it.
OFF_CONFLICT_MM_S = 6.0


class LiveModel:
    def __init__(self, detector, machine_id: str, baseline_path: Path, log: Callable[[str], None] = print,
                 relearn: bool = False):
        from ml.recordings import frame_windows
        from ml.settings import FS, FS_TOLERANCE

        self.det = detector
        self.machine_id = machine_id
        self.baseline_path = Path(baseline_path)
        self.log = log
        self._frame_windows = frame_windows
        self._fs, self._fs_tolerance = FS, FS_TOLERANCE
        self._learning: list[np.ndarray] = []  # analysis windows collected while learning
        self._learned_from = 0                 # Bluetooth windows used so far
        if not relearn and self.baseline_path.exists():
            self._load_baseline()

    @classmethod
    def load(cls, model_path: Path, machine_id: str, log: Callable[[str], None] = print,
             relearn: bool = False) -> "LiveModel | None":
        model_path = Path(model_path)
        if not model_path.exists():
            log(f"Model: no trained model at {model_path}, so the receiver runs without it.")
            return None
        try:
            from ml.detector import MachineGuardDetector
            det = MachineGuardDetector.load(model_path)
        except ImportError as err:
            log(f"Model: can't load it ({err}). Install it with: python -m pip install scikit-learn. "
                "The receiver runs without it.")
            return None
        if det.uses_temperature:
            log("Model: this model needs temperature, which the live receiver doesn't pass yet. "
                "Running without it.")
            return None
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in machine_id)
        live = cls(det, machine_id, model_path.parent / f"normal-{safe}.joblib", log, relearn)
        if live.ready:
            log(f"Model: loaded, using the saved normal for {machine_id} ({live.baseline_path.name}).")
        else:
            log(f"Model: loaded. It learns {machine_id}'s normal from the first {LEARN_WINDOWS} windows "
                "of the drill running normally (about 30 s): run it normally, no load, nothing loose.")
        return live

    @property
    def ready(self) -> bool:
        return self.det.baseline is not None

    # ---------- the saved normal ----------
    def _load_baseline(self) -> None:
        import joblib
        try:
            saved = joblib.load(self.baseline_path)
            if list(saved["feature_columns"]) != self.det.feature_columns:
                self.log(f"Model: {self.baseline_path.name} was learned with a different model; learning again.")
                return
            self.det.baseline, self.det.run_hz, self.det.main_axis = (
                saved["baseline"], saved["run_hz"], saved["main_axis"])
        except Exception as err:  # a damaged file must not stop the receiver
            self.log(f"Model: couldn't read {self.baseline_path.name} ({err}); learning again.")

    def _save_baseline(self) -> None:
        import joblib
        try:
            joblib.dump({"baseline": self.det.baseline, "run_hz": self.det.run_hz,
                         "main_axis": self.det.main_axis, "feature_columns": self.det.feature_columns,
                         "machine_id": self.machine_id}, self.baseline_path)
        except OSError as err:
            self.log(f"Model: couldn't save the normal ({err}); it will be learned again next start.")

    # ---------- every window ----------
    def check(self, accel_g: np.ndarray, fs: float, velocity_mm_s: float,
              main_freq_hz: float | None = None) -> dict[str, Any] | None:
        """The model's answer for one Bluetooth window, or None if this window can't be judged."""
        if abs(fs - self._fs) > self._fs_tolerance * self._fs:
            return None  # the model was trained at 1600 Hz; anything else would be guesswork
        pieces = [accel_g[s:e] for s, e in self._frame_windows(len(accel_g))]
        if not pieces:
            return None

        if not self.ready:
            motor = main_freq_hz is None or main_freq_hz >= LEARN_ABOVE_HZ
            if velocity_mm_s >= LEARN_ABOVE_MM_S and motor:  # never off, never moved by hand
                self._learning.extend(pieces)
                self._learned_from += 1
            if self._learned_from < LEARN_WINDOWS:
                return {"state": "learning", "label": None, "confidence": None,
                        "message": f"Learning this drill's normal ({self._learned_from} of {LEARN_WINDOWS})",
                        "recommendation": "Run the drill normally until this finishes.", "agree": None, "of": None}
            self.det.calibrate(self._learning, fs=fs)
            self._learning = []
            self._save_baseline()
            self.log(f"Model: learned {self.machine_id}'s normal (running speed {self.det.run_hz:.1f} Hz, "
                     f"main axis {self.det.main_axis}); saved to {self.baseline_path.name}.")
            return {"state": "learning", "label": None, "confidence": None,
                    "message": "Learned this drill's normal", "recommendation": None, "agree": None, "of": None}

        results = [self.det.predict(p, fs=fs) for p in pieces]
        state, agree = Counter(r["status"] for r in results).most_common(1)[0]
        same = [r for r in results if r["status"] == state]
        label = Counter(r["label"] for r in same).most_common(1)[0][0]
        pick = [r for r in same if r["label"] == label]
        confs = [r["confidence"] for r in pick if "confidence" in r]
        if state == "off" and velocity_mm_s >= OFF_CONFLICT_MM_S:
            return {"state": "check", "label": None, "confidence": None,
                    "message": "Unusual vibration, type unknown: inspect the drill",
                    "recommendation": "Stop and inspect the drill. If nothing looks wrong, run it again so it "
                                      "can relearn normal.",
                    "agree": agree, "of": len(results),
                    "note": f"the model said off, but the drill is vibrating at {velocity_mm_s:.0f} mm/s"}
        return {
            "state": state,
            "label": label,
            "confidence": round(float(np.mean(confs)), 3) if confs else None,
            "message": pick[0]["message"],
            "recommendation": pick[0]["recommendation"],
            "agree": agree,
            "of": len(results),
        }
