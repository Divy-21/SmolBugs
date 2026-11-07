import asyncio
import json

import numpy as np
import pytest

from ingest.ble_receiver import MachineGuardHandler, main, run
from processing.ble_protocol import window_to_frames
from simulation.fake_ble_board import FakeBoardClient, FakeBoardScanner
from simulation.packet_simulator import simulate_window_signals


def _quiet(**kw):
    lines = []
    return MachineGuardHandler(log=lines.append, **kw), lines


def test_temperature_message():
    h, lines = _quiet()
    r = h.on_temperature(bytearray(b"12,1582,69062"))
    assert r["sequence"] == 12 and r["temperature_c"] == pytest.approx(33.57, abs=0.05)
    assert "33.57 °C" in lines[-1]


def test_bad_temperature_message_logged_not_raised():
    h, lines = _quiet()
    assert h.on_temperature(bytearray(b"\xff\xfe")) is None
    assert h.on_temperature(bytearray(b"hello")) is None
    assert sum("Invalid temperature" in l for l in lines) == 2


def test_vibration_window_analysed_and_saved(tmp_path):
    h, lines = _quiet(rpm=3000, out_dir=tmp_path)
    h.on_temperature(b"1,1582,69062")
    results = [h.on_vibration(f) for f in window_to_frames(simulate_window_signals("imbalance"), window_id=3)]
    result = results[-1]
    assert all(r is None for r in results[:-1])
    assert result["axes"]["x"]["harmonics"][0]["amp_g"] == pytest.approx(0.30, rel=0.02)
    assert "1x=0.300" in lines[-1]

    vib = [json.loads(l) for l in (tmp_path / "vibration.jsonl").read_text().splitlines()]
    temp = [json.loads(l) for l in (tmp_path / "temperature.jsonl").read_text().splitlines()]
    assert vib[0]["window_id"] == 3 and temp[0]["sequence"] == 1
    saved = np.load(tmp_path / "windows" / "MG-001_w00003.npz")
    assert saved["accel_g"].shape == (4096, 3) and float(saved["sample_rate_hz"]) == 4000.0


def test_bad_vibration_frame_counted_not_raised():
    h, lines = _quiet()
    assert h.on_vibration(b"\x07") is None
    assert h.bad_frames == 1 and "Bad vibration frame" in lines[-1]
    assert "bad frames: 1" in h.summary()


def test_run_with_fake_board(monkeypatch):
    monkeypatch.setattr(FakeBoardClient, "window_interval_s", 0.05)
    monkeypatch.setattr(FakeBoardClient, "frame_gap_s", 0.0)
    h, lines = _quiet(rpm=3000)
    assert asyncio.run(run(h, FakeBoardScanner, FakeBoardClient, duration_s=1.2))
    assert h.window_count >= 4 and h.temperature_count >= 2
    assert h.bad_frames == 0 and h.decoder.stats.orphan_data_frames == 0
    assert any("MTU: 247" in l for l in lines)


def test_run_warns_on_small_mtu(monkeypatch):
    monkeypatch.setattr(FakeBoardClient, "mtu_size", 23)
    h, lines = _quiet()
    asyncio.run(run(h, FakeBoardScanner, FakeBoardClient, duration_s=0.2))
    assert any("WARNING: MTU 23" in l for l in lines)


def test_run_when_board_not_found():
    class NoScanner:
        @staticmethod
        async def find_device_by_name(name, timeout=15.0):
            return None

    h, lines = _quiet()
    assert asyncio.run(run(h, NoScanner, FakeBoardClient)) is False
    assert "not found" in lines[-1]


def test_cli_simulate(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(FakeBoardClient, "window_interval_s", 0.05)
    monkeypatch.setattr(FakeBoardClient, "frame_gap_s", 0.0)
    main(["--simulate", "--duration", "0.8", "--rpm", "3000", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert "SIMULATION MODE" in out and "Vib   window" in out
    assert (tmp_path / "vibration.jsonl").exists()
