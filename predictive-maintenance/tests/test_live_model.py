"""The trained model, run live in the receiver (ingest/live_model.py)."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from ingest.ble_receiver import MachineGuardHandler
from ingest.live_model import LEARN_WINDOWS, LiveModel

FS = 1600.0
REAL_MODEL = Path(__file__).resolve().parents[1] / "models" / "machineguard.joblib"


class StubDetector:
    """Stands in for ml.detector.MachineGuardDetector: a piece is a fault if its first sample is > 5 g."""

    feature_columns = ["x_rms_rel"]
    uses_temperature = False

    def __init__(self):
        self.baseline = None
        self.run_hz = None
        self.main_axis = "x"
        self.calibrated_with = 0

    def calibrate(self, windows, fs):
        self.calibrated_with = len(windows)
        self.baseline = pd.Series({"x_rms": 1.0})
        self.run_hz = 20.0

    def predict(self, piece, fs):
        if piece[0, 0] < 0:
            return {"status": "off", "label": "off", "confidence": 0.35, "message": "Machine off",
                    "recommendation": "No action needed."}
        if piece[0, 0] > 5:
            return {"status": "fault", "label": "loose_bit", "confidence": 0.8, "message": "Loose bit",
                    "recommendation": "Tighten the chuck."}
        return {"status": "ok", "label": "normal_free", "confidence": 0.6, "message": "Running normally",
                "recommendation": "No action needed."}


def window(first=0.0, n=4096):
    a = np.zeros((n, 3))
    a[:, 0] = first
    return a


def model(tmp_path, **kw):
    return LiveModel(StubDetector(), "MG-T", tmp_path / "normal-MG-T.joblib", log=lambda _: None, **kw)


def test_learns_from_running_windows_only_then_judges(tmp_path):
    m = model(tmp_path)
    assert m.check(window(), FS, velocity_mm_s=0.5)["state"] == "learning"  # off: not learned from
    out = m.check(window(), FS, velocity_mm_s=4.8, main_freq_hz=4.0)  # moved by hand (real, Nov 9)
    assert "(0 of" in out["message"]
    assert "(0 of" in m.check(window(), FS, velocity_mm_s=9, main_freq_hz=3.0)["message"]  # shaken hard by hand
    for i in range(LEARN_WINDOWS - 1):
        out = m.check(window(), FS, velocity_mm_s=10, main_freq_hz=135.0)
        assert out["state"] == "learning" and f"({i + 1} of {LEARN_WINDOWS})" in out["message"]
    assert m.check(window(), FS, 10)["message"] == "Learned this drill's normal"
    assert m.det.calibrated_with == LEARN_WINDOWS * 7  # 7 analysis windows per Bluetooth window
    out = m.check(window(), FS, 10)
    assert out == {"state": "ok", "label": "normal_free", "confidence": 0.6, "message": "Running normally",
                   "recommendation": "No action needed.", "agree": 7, "of": 7}


def test_window_answer_is_the_majority_of_its_pieces(tmp_path):
    m = model(tmp_path)
    for _ in range(LEARN_WINDOWS):
        m.check(window(), FS, 10)
    a = window()
    a[: 512 * 5, 0] = 9.0  # pieces 0-3 start inside the faulty stretch, 4-6 don't
    out = m.check(a, FS, 10)
    assert (out["state"], out["label"], out["agree"], out["of"]) == ("fault", "loose_bit", 5, 7)
    assert out["confidence"] == 0.8


def test_off_while_clearly_vibrating_becomes_check(tmp_path):
    m = model(tmp_path)
    for _ in range(LEARN_WINDOWS):
        m.check(window(), FS, 10)
    assert m.check(window(-1.0), FS, velocity_mm_s=0.7)["state"] == "off"  # really off: trusted
    out = m.check(window(-1.0), FS, velocity_mm_s=140)  # "off" at 140 mm/s: the model is out of its depth
    assert (out["state"], out["label"], out["confidence"]) == ("check", None, None)
    assert "140 mm/s" in out["note"]


def test_saved_normal_is_reused_and_relearn_ignores_it(tmp_path):
    m = model(tmp_path)
    for _ in range(LEARN_WINDOWS):
        m.check(window(), FS, 10)
    assert (tmp_path / "normal-MG-T.joblib").exists()
    assert model(tmp_path).ready
    assert not model(tmp_path, relearn=True).ready


def test_normal_from_a_different_model_is_not_reused(tmp_path):
    m = model(tmp_path)
    for _ in range(LEARN_WINDOWS):
        m.check(window(), FS, 10)
    other = StubDetector()
    other.feature_columns = ["something_else"]
    assert not LiveModel(other, "MG-T", tmp_path / "normal-MG-T.joblib", log=lambda _: None).ready


def test_other_sample_rates_are_not_judged(tmp_path):
    assert model(tmp_path).check(window(), 4000.0, 10) is None


def test_missing_model_file_means_no_model(tmp_path):
    said = []
    assert LiveModel.load(tmp_path / "nope.joblib", "MG-T", log=said.append) is None
    assert "runs without it" in said[0]


def test_receiver_adds_the_answer_and_survives_a_model_error(tmp_path):
    handler = MachineGuardHandler("MG-T", log=lambda _: None, model=model(tmp_path))
    w = SimpleNamespace(accel_g=window(), sample_rate_hz=FS)
    result = {"axes": {ax: {"velocity_rms_mm_s": 10.0, "dominant_amp_g": 0.1, "dominant_freq_hz": 135.0} for ax in "xyz"}}
    assert handler._run_model(w, result)["state"] == "learning"

    def boom(*_):
        raise RuntimeError("bad window")
    handler.model.check = boom
    assert handler._run_model(w, result) is None
    handler.model.check = lambda *_: pytest.fail("a failed model must stay off for the rest of the run")
    assert handler._run_model(w, result) is None


@pytest.mark.skipif(not REAL_MODEL.exists(), reason="the trained model lives only on the training laptop")
def test_real_model_runs_on_a_drill_like_window(tmp_path):
    pytest.importorskip("sklearn")
    import shutil

    from simulation.packet_simulator import simulate_window_signals
    shutil.copy(REAL_MODEL, tmp_path)
    m = LiveModel.load(tmp_path / REAL_MODEL.name, "MG-T", log=lambda _: None)
    for i in range(LEARN_WINDOWS):
        m.check(simulate_window_signals("healthy", rpm=1200, fs=FS, seed=i), FS, 10)
    out = m.check(simulate_window_signals("healthy", rpm=1200, fs=FS, seed=9), FS, 10)
    assert out["state"] in {"ok", "off", "check", "fault"} and out["of"] == 7
