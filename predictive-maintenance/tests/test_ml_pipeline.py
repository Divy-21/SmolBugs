"""ML pipeline on recordings in the Bluetooth receiver's --out format."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from ml_fakes import FS, make_dataset, record_run  # noqa: E402

from ml import record, train  # noqa: E402
from ml.detector import MachineGuardDetector  # noqa: E402
from ml.features import relative_features, window_features  # noqa: E402
from ml.recordings import (build_feature_table, frame_windows, iter_windows, load_run,  # noqa: E402
                           parse_name, temp_slope)
from ml.settings import SHUTOFF_K, WINDOW  # noqa: E402


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    return make_dataset(tmp_path_factory.mktemp("data") / "raw")


@pytest.fixture(scope="module")
def trained(dataset, tmp_path_factory):
    out = tmp_path_factory.mktemp("models")
    train.main(["--raw", str(dataset), "--out", str(out)])
    return out


# ---------- loading what the receiver wrote ----------
def test_run_folder_written_by_receiver_loads(dataset):
    run = load_run(dataset / "S1_normal_free_01")
    assert run.meta == {"session": 1, "label": "normal_free", "run": 1, "file": "S1_normal_free_01"}
    assert [f.window_id for f in run.frames] == [0, 1, 2, 3]
    assert all(f.accel_g.shape == (4096, 3) and f.fs == FS for f in run.frames)


def test_seven_analysis_windows_per_bluetooth_window():
    spans = list(frame_windows(4096))
    assert len(spans) == 7 and spans[0] == (0, WINDOW) and spans[-1][1] == 4096
    assert all(e - s == WINDOW for s, e in spans)
    assert list(frame_windows(WINDOW - 1)) == []  # a short window gives nothing, not a partial one


def test_windows_never_cross_bluetooth_windows(tmp_path):
    """Two frames with very different offsets: a window spanning the join would show a step."""
    run_dir = tmp_path / "S1_normal_free_01"
    (run_dir / "windows").mkdir(parents=True)
    t = np.arange(4096) / FS
    for i, offset in enumerate((0.0, 0.8)):
        accel = np.column_stack([0.05 * np.sin(2 * np.pi * 30 * t) + offset] * 3)
        np.savez(run_dir / "windows" / f"MG-001_w{i:05d}.npz", accel_g=accel, sample_rate_hz=FS)
    run = load_run(run_dir)
    for _, _, w in iter_windows(run):
        f = window_features(w)
        assert f["x_rms"] == pytest.approx(0.05 / np.sqrt(2), rel=0.05)  # no 0.8 g step inside
    assert len(list(iter_windows(run))) == 14


def test_bad_folder_names_and_empty_runs(tmp_path):
    with pytest.raises(ValueError):
        parse_name(tmp_path / "session1")
    with pytest.raises(ValueError):
        parse_name(tmp_path / "S1_wobble_01")
    (tmp_path / "S1_off_01").mkdir()
    with pytest.raises(ValueError, match="no windows"):
        load_run(tmp_path / "S1_off_01")


def test_warns_on_wrong_sample_rate(tmp_path):
    record_run(tmp_path / "S1_normal_free_01", "normal_free", 1, 30.0, 1.0, seed=1, fs=1500.0)
    with pytest.warns(UserWarning, match="sample rate"):
        load_run(tmp_path / "S1_normal_free_01")


def test_warns_when_board_restart_overwrote_windows(tmp_path):
    run_dir = record_run(tmp_path / "S1_normal_free_01", "normal_free", 2, 30.0, 1.0, seed=1)
    with open(run_dir / "vibration.jsonl", "a") as f:  # third window logged, its file overwritten
        f.write(json.dumps({"window_id": 0}) + "\n")
    with pytest.warns(UserWarning, match="restarted"):
        load_run(run_dir)


def test_half_written_last_line_is_ignored(tmp_path):
    run_dir = record_run(tmp_path / "S1_normal_free_01", "normal_free", 2, 30.0, 1.0, seed=1)
    with open(run_dir / "vibration.jsonl", "a") as f:
        f.write('{"window_id": 9, "temp')
    assert len(load_run(run_dir, use_temp=True).frames) == 2


# ---------- temperature is optional ----------
def test_no_temperature_features_by_default(dataset):
    table = build_feature_table(dataset, verbose=False)
    assert not {"temp_c", "temp_slope_c_per_min"} & set(table.columns)


def test_temperature_comes_from_receiver_files(dataset):
    run = load_run(dataset / "S1_normal_free_01", use_temp=True)
    assert all(f.temp_c == pytest.approx(25.0, abs=0.5) for f in run.frames)


def test_temp_slope_from_timestamps():
    readings = [(t, 20.0 + 0.05 * t) for t in np.arange(0, 60, 0.5)]  # 3 °C per minute
    assert temp_slope(readings, at_s=60.0) == pytest.approx(3.0, rel=1e-3)
    assert temp_slope(readings[:3], at_s=60.0) == 0.0  # too few readings
    assert temp_slope([(59.0, 20.0), (59.5, 25.0)] * 5, at_s=60.0) == 0.0  # too short a span


def test_relative_features_need_running_speed():
    t = np.arange(WINDOW) / FS
    w = np.column_stack([np.sin(2 * np.pi * 30 * t)] * 3)
    raw = pd.DataFrame([window_features(w)])  # no run_hz -> no harmonics
    with pytest.raises(ValueError, match="running speed"):
        relative_features(raw, raw.median())


# ---------- training ----------
def test_training_end_to_end(trained, capsys):
    det = MachineGuardDetector.load(trained / "machineguard.joblib")
    assert not det.uses_temperature
    assert det.meta["sessions"] == [1, 2, 3] and det.meta["fs"] == FS
    cm = pd.read_csv(trained / "confusion_matrix.csv", index_col=0)
    accuracy = np.trace(cm.reindex(columns=cm.index, fill_value=0).to_numpy()) / cm.to_numpy().sum()
    assert accuracy > 0.8  # held-out session, never seen in training


def test_training_needs_two_sessions(tmp_path):
    make_dataset(tmp_path / "raw", sessions=(1,), labels=("normal_free", "imbalance"), runs=1, frames=2)
    with pytest.raises(SystemExit, match="at least 2 sessions"):
        train.main(["--raw", str(tmp_path / "raw"), "--out", str(tmp_path / "m")])


def test_training_with_temperature(tmp_path):
    make_dataset(tmp_path / "raw", sessions=(1, 2), labels=("normal_free", "imbalance"), runs=1, frames=2)
    train.main(["--raw", str(tmp_path / "raw"), "--out", str(tmp_path / "m"), "--use-temp"])
    det = MachineGuardDetector.load(tmp_path / "m" / "machineguard.joblib")
    assert det.uses_temperature
    run = load_run(tmp_path / "raw" / "S2_normal_free_01", use_temp=True)
    with pytest.raises(ValueError, match="uses temperature"):
        det.calibrate([w for _, _, w in iter_windows(run)])
    det.calibrate([(w, f.temp_c, f.temp_slope_c_per_min) for f, _, w in iter_windows(run)])
    with pytest.raises(ValueError, match="uses temperature"):
        det.predict(run.frames[0].accel_g[:WINDOW])
    assert det.predict(run.frames[0].accel_g[:WINDOW], 25.0, 0.0)["status"] in ("ok", "check")


# ---------- live detector ----------
def _calibrated(trained, dataset, session=3):
    det = MachineGuardDetector.load(trained / "machineguard.joblib")
    det.calibrate([w for _, _, w in iter_windows(load_run(dataset / f"S{session}_normal_free_01"))])
    return det


def test_live_detector_normal_and_fault(trained, dataset):
    det = _calibrated(trained, dataset)
    normal = [det.predict(w) for _, _, w in iter_windows(load_run(dataset / "S3_normal_free_02"))]
    assert not any(r["shutoff"] for r in normal)
    det.reset_shutoff()
    fault = [det.predict(w) for _, _, w in iter_windows(load_run(dataset / "S3_imbalance_01"))]
    assert sum(r["status"] == "fault" for r in fault) / len(fault) > 0.8
    assert any(r["shutoff"] for r in fault)
    off = [det.predict(w) for _, _, w in iter_windows(load_run(dataset / "S3_off_01"))]
    assert all(r["status"] == "off" and not r["shutoff"] for r in off)


def test_single_fault_window_does_not_shut_off(trained, dataset):
    det = _calibrated(trained, dataset)
    normal = [w for _, _, w in iter_windows(load_run(dataset / "S3_normal_free_02"))]
    fault = [w for _, _, w in iter_windows(load_run(dataset / "S3_imbalance_01"))]
    results = [det.predict(w) for w in normal[:6]] + [det.predict(fault[0])] + [det.predict(w) for w in normal[6:12]]
    assert results[6]["fault_now"] and not results[6]["shutoff"]
    assert not any(r["shutoff"] for r in results)
    det.reset_shutoff()
    run = [det.predict(w)["shutoff"] for w in fault[:SHUTOFF_K]]
    assert run[-1] and not any(run[:-1])  # cuts power on the k-th fault window, not before


def test_missing_features_raise_instead_of_filling_zero(trained, dataset):
    det = _calibrated(trained, dataset)
    det.feature_columns.append("x_h4x_rel")
    with pytest.raises(ValueError, match="missing"):
        det.predict(load_run(dataset / "S3_normal_free_02").frames[0].accel_g[:WINDOW])


def test_model_trained_at_another_rate_is_rejected(trained, tmp_path):
    import joblib
    d = joblib.load(trained / "machineguard.joblib")
    d["meta"]["fs"] = 1000.0
    joblib.dump(d, tmp_path / "old.joblib")
    with pytest.raises(ValueError, match="retrain"):
        MachineGuardDetector.load(tmp_path / "old.joblib")


def test_shutoff_settings_validated():
    with pytest.raises(ValueError):
        MachineGuardDetector(None, None, [], shutoff_k=5, shutoff_n=3)


def test_live_check_runs(trained, dataset, capsys):
    from ml import live_check
    live_check.main(["--raw", str(dataset), "--session", "3", "--models", str(trained / "machineguard.joblib")])
    out = capsys.readouterr().out
    assert "S3_imbalance_01" in out and "learned normal from S3_normal_free_01" in out


# ---------- recorder ----------
def test_record_names_folder_and_calls_receiver(tmp_path):
    (tmp_path / "S1_imbalance_01").mkdir()
    (tmp_path / "S1_imbalance_02").mkdir()
    calls = []

    def fake_receiver(argv):
        calls.append(argv)
        record_run(Path(argv[argv.index("--out") + 1]), "imbalance", 10, 30.0, 1.0, seed=0)

    code = record.main(["--session", "1", "--label", "imbalance", "--seconds", "100", "--out", str(tmp_path),
                        "--countdown", "0"], receiver_main=fake_receiver)
    assert code == 0
    argv = calls[0]
    assert argv[argv.index("--out") + 1] == str(tmp_path / "S1_imbalance_03")
    assert argv[argv.index("--duration") + 1] == "100.0"


def test_record_warns_when_no_vibration_arrives(tmp_path, capsys):
    def silent_receiver(argv):
        (Path(argv[argv.index("--out") + 1]) / "windows").mkdir(parents=True)

    code = record.main(["--session", "2", "--label", "off", "--out", str(tmp_path), "--countdown", "0"],
                       receiver_main=silent_receiver)
    assert code == 1
    assert "firmware" in capsys.readouterr().out


def test_record_rejects_too_short(tmp_path):
    with pytest.raises(SystemExit):
        record.main(["--session", "1", "--label", "off", "--seconds", "30", "--out", str(tmp_path)])


def test_bearing_resample_ratio():
    from ml.bearing_validation import RATIO
    assert (RATIO.numerator, RATIO.denominator) == (1, 16)
