import numpy as np
import pandas as pd
import pytest

from ml.fake_recordings import fake_run
from ml.features import relative_features, time_features, window_features
from ml.record import parse_line
from ml.recordings import check_timing, parse_name
from ml.settings import FS, WINDOW


def _window(label, seed=0):
    df = fake_run(label, 1.1, 30.0, 1.0, 22.0, seed)
    return df[["ax", "ay", "az"]].to_numpy()[:WINDOW]


def test_kurtosis_of_sine_is_low_and_impacts_high():
    t = np.arange(WINDOW) / FS
    assert time_features(np.sin(2 * np.pi * 30 * t))["kurtosis"] == pytest.approx(1.5, abs=0.05)
    assert time_features(_window("loose_bit")[:, 0])["kurtosis"] > 4


def test_flat_signal_does_not_crash():
    f = time_features(np.ones(WINDOW))
    assert f["rms"] == 0 and f["crest"] == 0


def test_relative_features_are_zero_at_baseline():
    raw = pd.DataFrame([window_features(_window("normal_free"), 22.0, 0.0, 30.0)])
    rel = relative_features(raw, raw.iloc[0])
    assert rel["x_rms_rel"].iloc[0] == pytest.approx(0.0, abs=1e-6)
    assert rel["temp_rise_c"].iloc[0] == 0.0


def test_imbalance_shows_up_in_1x():
    base = pd.DataFrame([window_features(_window("normal_free"), 22.0, 0.0, 30.0)])
    imb = pd.DataFrame([window_features(_window("imbalance", 1), 22.0, 0.0, 30.0)])
    assert relative_features(imb, base.iloc[0])["x_h1x_rel"].iloc[0] > 1.5


def test_file_names_and_lines():
    from pathlib import Path
    assert parse_name(Path("S2_bent_bit_04.csv")) == {"session": 2, "label": "bent_bit", "run": 4, "file": "S2_bent_bit_04.csv"}
    with pytest.raises(ValueError):
        parse_name(Path("S1_wobbly_01.csv"))
    assert parse_line(b"1000,0.1,0.2,1.0,24.5\r\n") == [1000, 0.1, 0.2, 1.0, 24.5]
    assert parse_line(b"rst:0x1 (POWERON_RESET)\n") is None


def test_dropped_samples_are_counted():
    t = np.delete(np.arange(1000) * 1000, [100, 101, 500])
    assert check_timing(t)["dropped"] == 3
