import json

import pytest

from processing import FFTConfig, WindowAssembler, analyze_window
from simulation.packet_simulator import CONDITIONS, simulate_window_signals, window_to_packets

RPM = 3000.0


def _analyze(condition, seed=0, rpm=RPM, **kw):
    asm = WindowAssembler()
    packets = window_to_packets(simulate_window_signals(condition, rpm=rpm, seed=seed), **kw)
    window = [asm.add(p) for p in packets][-1]
    return analyze_window(window, rpm=rpm)


def test_healthy_window_end_to_end():
    r = _analyze("healthy")
    assert r["n_samples"] == 4096 and r["warnings"] == []
    assert r["resolution_hz"] == pytest.approx(0.9765625)
    x = r["axes"]["x"]
    assert x["dominant_freq_hz"] == pytest.approx(50.0, abs=0.05)
    assert x["harmonics"][0]["amp_g"] == pytest.approx(0.05, rel=0.05)
    assert set(x["band_power_g2"]) == {"2-50", "50-200", "200-500", "500-1000"}


def test_gravity_does_not_leak_into_z():
    z = _analyze("healthy")["axes"]["z"]
    assert z["dominant_amp_g"] < 0.05  # z carries 1 g of gravity, removed before FFT


def test_conditions_are_distinguishable():
    r = {c: _analyze(c)["axes"]["x"] for c in CONDITIONS}
    h = {c: {e["order"]: e["amp_g"] for e in r[c]["harmonics"]} for c in CONDITIONS}
    assert h["imbalance"][1] > 4 * h["healthy"][1]
    assert h["misalignment"][2] > 10 * h["healthy"][2]
    assert r["bearing"]["band_power_g2"]["500-1000"] > 20 * r["healthy"]["band_power_g2"]["500-1000"]
    assert r["imbalance"]["velocity_rms_mm_s"] > 4 * r["healthy"]["velocity_rms_mm_s"]


def test_uses_window_sample_rate_not_config_default():
    asm = WindowAssembler()
    fs = 3200.0
    packets = window_to_packets(simulate_window_signals("healthy", fs=fs), fs=fs)
    r = analyze_window([asm.add(p) for p in packets][-1], rpm=RPM)
    assert r["axes"]["x"]["dominant_freq_hz"] == pytest.approx(50.0, abs=0.05)


def test_wrong_window_size_flagged():
    asm = WindowAssembler()
    packets = window_to_packets(simulate_window_signals("healthy", n=2048))
    r = analyze_window([asm.add(p) for p in packets][-1])
    assert any("expected 4096" in w for w in r["warnings"])
    assert "harmonics" not in r["axes"]["x"]  # no rpm given


def test_result_is_json_serialisable():
    json.dumps(_analyze("bearing"))


def test_packet_size_reasonable():
    p = window_to_packets(simulate_window_signals("healthy"))[0]
    assert len(json.dumps(p).encode()) < 20_000
