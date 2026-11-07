"""Turns a stream of samples into dashboard messages.

Samples come from a Source (replay of recorded CSVs, or the ESP32 over USB serial).
Every 0.5 s a new 1 s window is cut, run through the ML detector and the FFT, and
turned into one JSON message for the web page.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ml.detector import MachineGuardDetector, RECOMMEND
from ml.settings import FS, STEP, TEMP_SLOPE_SECONDS, WINDOW
from processing import amplitude_spectrum

MAX_SPECTRUM_HZ = 260.0
SHORT = {"imbalance": "Imbalance", "bent_bit": "Bent bit", "loose_bit": "Loose bit", "overheat": "Overheating"}
STATUS_TEXT = {
    "ok": "Running normally",
    "off": "Machine off",
    "check": "Unusual vibration, type unknown",
    "learning": "Learning normal",
    "waiting": "Waiting for data",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- sources
class ReplaySource:
    """Plays one or more recorded CSVs back in real time, as if the drill were running."""

    name = "replay"

    def __init__(self, paths: list[Path], loop: bool = True):
        frames = [pd.read_csv(p)[["ax", "ay", "az", "temp_c"]] for p in paths]
        self.data = pd.concat(frames, ignore_index=True).to_numpy(dtype=float)
        self.files = [p.name for p in paths]
        # where each file starts, so the page can say which recording is playing
        self.starts = np.cumsum([0] + [len(f) for f in frames[:-1]])
        self.loop = loop
        self.pos = 0
        self.t0 = time.monotonic()

    def read(self) -> np.ndarray:
        """All samples that should have arrived since the last call."""
        due = int((time.monotonic() - self.t0) * FS)
        if due <= self.pos:
            return np.empty((0, 4))
        if self.pos >= len(self.data):
            if not self.loop:
                return np.empty((0, 4))
            self.pos, self.t0 = 0, time.monotonic()
            return np.empty((0, 4))
        chunk = self.data[self.pos : min(due, len(self.data))]
        self.pos += len(chunk)
        return chunk

    def current_file(self) -> str:
        idx = int(np.searchsorted(self.starts, self.pos, side="right") - 1)
        return self.files[max(idx, 0)]


class SerialSource:
    """Reads 't_us,ax,ay,az,temp_c' lines from the ESP32 in a background thread."""

    name = "serial"

    def __init__(self, port: str, baud: int = 921600):
        import serial  # pyserial

        self.ser = serial.Serial(port, baud, timeout=1)
        self.port = port
        self.buf: deque = deque()
        self.lock = threading.Lock()
        self.running = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        from ml.record import parse_line

        self.ser.reset_input_buffer()
        while self.running:
            try:
                line = self.ser.readline()
            except Exception:
                break
            sample = parse_line(line) if line else None
            if sample:
                with self.lock:
                    self.buf.append(sample[1:])

    def read(self) -> np.ndarray:
        with self.lock:
            rows = list(self.buf)
            self.buf.clear()
        return np.asarray(rows, dtype=float).reshape(-1, 4)

    def current_file(self) -> str:
        return f"live on {self.port}"

    def close(self) -> None:
        self.running = False
        self.ser.close()


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self, model_path: Path):
        self.detector = MachineGuardDetector.load(model_path)
        self.source = None
        self.samples = np.empty((0, 4))
        self.temp_hist: deque = deque()          # (seconds, temp)
        self.t_stream = 0.0                      # seconds of data received
        self.since_last = 0
        self.learning_left = 0.0
        self.learn_windows: list = []
        self.calibrated = False
        self.active_fault: dict | None = None    # stays until "Fixed it"
        self.pending: tuple | None = None
        self.stable_status = "waiting"
        self.running_seconds = 0.0
        self.events: deque = deque(maxlen=200)
        self.last_message: dict | None = None

    # ------------- control
    def set_source(self, source, learn_seconds: float) -> None:
        if self.source is not None and hasattr(self.source, "close"):
            self.source.close()
        self.source = source
        self.samples = np.empty((0, 4))
        self.temp_hist.clear()
        self.t_stream = 0.0
        self.since_last = 0
        self.active_fault = None
        self.stable_status = "waiting"
        self.calibrate(learn_seconds)

    def calibrate(self, seconds: float) -> None:
        self.learning_left = float(seconds)
        self.learn_windows = []
        self.calibrated = False
        self.active_fault = None
        self.event("Learning normal started", f"Keep the machine running normally for {seconds:.0f} seconds.")

    def reset(self) -> None:
        if self.active_fault:
            self.event(f"Marked as fixed: {SHORT.get(self.active_fault['label'], self.active_fault['label'])}")
        self.active_fault = None
        self.pending = None

    def event(self, text: str, recommendation: str = "") -> dict:
        e = {"type": "event", "time": now_iso(), "text": text, "recommendation": recommendation}
        self.events.appendleft(e)
        return e

    # ------------- main step, called many times a second
    def step(self) -> list[dict]:
        if self.source is None:
            return []
        new = self.source.read()
        if len(new) == 0:
            return []
        t = self.t_stream + np.arange(len(new)) / FS
        for ti, temp in zip(t[::50], new[::50, 3]):  # temperature history at 20 Hz is plenty
            if np.isfinite(temp):
                self.temp_hist.append((ti, temp))
        while self.temp_hist and self.temp_hist[0][0] < self.t_stream - 600:
            self.temp_hist.popleft()
        self.t_stream += len(new) / FS
        self.samples = np.vstack([self.samples, new])[-WINDOW:]
        self.since_last += len(new)

        out = []
        while self.since_last >= STEP and len(self.samples) >= WINDOW:
            self.since_last -= STEP
            out.extend(self.process_window(self.samples[-WINDOW:]))
        return out

    def temp_slope(self) -> float:
        pts = [(t, y) for t, y in self.temp_hist if t >= self.t_stream - TEMP_SLOPE_SECONDS]
        if len(pts) < 3 or pts[-1][0] - pts[0][0] < 3.0:
            return 0.0
        t, y = np.array(pts).T
        return float(np.polyfit(t, y, 1)[0] * 60.0)

    def process_window(self, w: np.ndarray) -> list[dict]:
        accel, temps = w[:, :3], w[:, 3]
        temp = float(np.nanmean(temps)) if np.isfinite(temps).any() else float("nan")
        slope = self.temp_slope()
        out: list[dict] = []

        if self.learning_left > 0:
            self.learn_windows.append((accel.copy(), temp, slope))
            self.learning_left -= STEP / FS
            if self.learning_left <= 0:
                self.detector.calibrate(self.learn_windows)
                self.calibrated = True
                out.append(self.event(
                    f"Learned normal: running speed {self.detector.run_hz:.1f} Hz ({self.detector.run_hz * 60:.0f} RPM)",
                    "Monitoring started."))
            result = {"status": "learning", "label": "learning", "message": STATUS_TEXT["learning"],
                      "recommendation": "Keep the machine running normally while it learns.", "confidence": None}
        elif not self.calibrated:
            result = {"status": "waiting", "label": "waiting", "message": STATUS_TEXT["waiting"],
                      "recommendation": "Press Learn normal while the machine runs normally.", "confidence": None}
        else:
            r = self.detector.predict(accel, temp, slope)
            if r["status"] == "ok":
                r["message"] = STATUS_TEXT["ok"]
            result = {k: r.get(k) for k in ("status", "label", "message", "recommendation", "confidence")}
            out.extend(self.track_status(result))

        if result["status"] not in ("off", "waiting"):
            self.running_seconds += STEP / FS

        shown = dict(result)
        if self.active_fault and result["status"] != "learning":
            shown = {**result, **{k: self.active_fault[k] for k in ("status", "label", "message", "recommendation")},
                     "live_status": result["status"], "live_message": result["message"]}

        axis_idx = "xyz".index(self.detector.main_axis) if self.calibrated else int(np.argmax(np.std(accel, axis=0)))
        spec = amplitude_spectrum(accel[:, axis_idx], FS)
        keep = spec.freqs_hz <= MAX_SPECTRUM_HZ
        centred = accel - accel.mean(axis=0)
        msg = {
            "type": "window",
            "machine_id": "drill-1",
            "time": now_iso(),
            **shown,
            "fault_since": self.active_fault["time"] if self.active_fault else None,
            "learning_seconds_left": max(0.0, round(self.learning_left, 1)) if result["status"] == "learning" else None,
            "rms_g": {ax: float(np.sqrt(np.mean(centred[:, i] ** 2))) for i, ax in enumerate("xyz")},
            "temp_c": None if not np.isfinite(temp) else round(temp, 2),
            "temp_slope_c_per_min": round(slope, 2),
            "run_hz": round(self.detector.run_hz, 2) if self.calibrated else None,
            "running_hours": round(self.running_seconds / 3600, 4),
            "source": {"mode": self.source.name, "playing": self.source.current_file()},
            "spectrum": {
                "axis": "xyz"[axis_idx],
                "freqs_hz": np.round(spec.freqs_hz[keep], 2).tolist(),
                "amp_g": np.round(spec.amplitude[keep], 5).tolist(),
            },
        }
        self.last_message = msg
        out.append(msg)
        return out

    def track_status(self, result: dict) -> list[dict]:
        """Logs a change only after it holds for 3 windows (1.5 s), so one odd window is ignored."""
        key = (result["status"], result["label"])
        if self.pending and self.pending[0] == key:
            self.pending = (key, self.pending[1] + 1)
        else:
            self.pending = (key, 1)
        if self.pending[1] != 3 or key[0] == self.stable_status and key[0] != "fault":
            return []
        prev = self.stable_status
        self.stable_status = key[0]
        if key[0] == "fault":
            if self.active_fault and self.active_fault["label"] == result["label"]:
                return []
            self.active_fault = {**result, "time": now_iso()}
            return [self.event(f"{SHORT.get(result['label'], result['label'])} detected", result["recommendation"])]
        if key[0] == "check":
            return [self.event("Unusual vibration, type unknown", result["recommendation"])]
        if key[0] == "off":
            return [self.event("Machine switched off")]
        if key[0] == "ok" and prev in ("off", "waiting"):
            return [self.event("Machine running normally")]
        return []
