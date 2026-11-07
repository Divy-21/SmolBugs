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


# --- JSON status messages, as sent by the board's current (older) firmware ----------------

def _json_message(ts, mv):
    return ('{"machine_id":"MG-001","timestamp_ms":%d,"sensors":[{"id":"sensor_0","type":"thermistor",'
            '"voltage_mv":%d},{"id":"sensor_1","type":"accelerometer","x_g":0.00,"y_g":0.00,"z_g":0.00}]}\n'
            % (ts, mv)).encode()


def _pieces(data, size=20):
    return [data[i:i + size] for i in range(0, len(data), size)]


def test_json_split_into_20_byte_pieces_like_real_board(tmp_path):
    h, lines = _quiet(out_dir=tmp_path)
    stream = _json_message(30547, 1726) + _json_message(31048, 1728)
    pieces = _pieces(stream)
    assert pieces[0] == b'{"machine_id":"MG-00'      # exactly what the real log showed
    results = [h.on_temperature(bytearray(p)) for p in pieces]
    readings = [r for r in results if r]
    assert [r["sequence"] for r in readings] == [30547, 31048]
    assert readings[0]["voltage_mv"] == 1726
    assert readings[0]["resistance_ohm"] == pytest.approx(75_000 * 1726 / (3300 - 1726), rel=1e-9)
    from processing import voltage_to_celsius
    assert readings[0]["temperature_c"] == pytest.approx(voltage_to_celsius(1726), abs=1e-9)
    assert "1726 mV" in lines[1] and "29.47 °C" in lines[1]
    assert h.temperature_count == 2
    assert not any("Invalid" in l for l in lines)
    assert sum("older firmware" in l for l in lines) == 1
    saved = [json.loads(l) for l in (tmp_path / "temperature.jsonl").read_text().splitlines()]
    assert len(saved) == 2 and saved[1]["machine_id"] == "MG-001"


def test_json_pieces_of_any_size(tmp_path):
    h, _ = _quiet()
    stream = b"".join(_json_message(1000 + i, 1700 + i) for i in range(5))
    for size in (1, 7, 20, 244, len(stream)):
        h.temperature_count = 0
        for p in _pieces(stream, size):
            h.on_temperature(p)
        assert h.temperature_count == 5


def test_json_message_with_wiring_fault():
    h, lines = _quiet()
    r = h.on_temperature(_json_message(5, 0))
    assert r["temperature_c"] is None and "wiring?" in lines[-1]


def test_bad_json_line_logged_and_recovers():
    h, lines = _quiet()
    h.on_temperature(b'{"oops": \n')
    assert "Invalid JSON" in lines[-1]
    assert h.on_temperature(_json_message(9, 1730))["sequence"] == 9


def test_unterminated_json_buffer_is_capped():
    h, lines = _quiet()
    h.on_temperature(b"{" + b"x" * 9000)
    assert "Discarding" in lines[-1]
    assert h.on_temperature(b"7,1582,69062")["sequence"] == 7   # CSV still works afterwards


def test_report_reads_json_temperatures(tmp_path):
    from ingest.fft_report import build_report
    h, _ = _quiet(out_dir=tmp_path)
    for p in _pieces(_json_message(1, 1726) + _json_message(2, 1730)):
        h.on_temperature(p)
    temp = [t for t in build_report(tmp_path).data if t.name == "temperature"][0]
    assert len(temp.y) == 2
