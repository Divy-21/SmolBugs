import asyncio
import json

import pytest

import ingest.ble_receiver as receiver
from ingest.ble_receiver import MachineGuardHandler, main
from ingest.fft_report import build_report, main as report_main, write_report
from processing.ble_protocol import window_to_frames
from simulation.packet_simulator import simulate_window_signals


def _record(tmp_path, windows=("healthy", "imbalance"), live=False):
    h = MachineGuardHandler(rpm=3000, out_dir=tmp_path, log=lambda *_: None, live_html=live)
    for i in range(3):
        h.on_temperature(f"{i},1582,69062".encode())
    for wid, cond in enumerate(windows):
        for f in window_to_frames(simulate_window_signals(cond, seed=wid), window_id=wid):
            h.on_vibration(f)
    return h


def _traces(fig, name_prefix):
    return [t for t in fig.data if (t.name or "").startswith(name_prefix)]


def test_report_shows_latest_window_by_default(tmp_path):
    _record(tmp_path)
    fig = build_report(tmp_path, rpm=3000)
    assert "window 1" in fig.layout.title.text
    assert "0.300 g" in fig.layout.title.text            # window 1 is the imbalance one
    assert len(_traces(fig, "x axis")) == 4              # time, FFT, PSD, trend
    assert len(fig.layout.shapes) == 3                   # 1x/2x/3x markers


def test_report_specific_window(tmp_path):
    _record(tmp_path)
    fig = build_report(tmp_path, window_id=0)
    assert "window 0" in fig.layout.title.text and "0.050 g" in fig.layout.title.text
    with pytest.raises(FileNotFoundError):
        build_report(tmp_path, window_id=99)


def test_fft_trace_peaks_at_running_speed(tmp_path):
    _record(tmp_path)
    fig = build_report(tmp_path)
    fft_x = [t for t in fig.data if t.name == "x axis" and t.xaxis == "x2"][0]
    peak = max(range(len(fft_x.y)), key=lambda i: fft_x.y[i])
    assert fft_x.x[peak] == pytest.approx(50.0, abs=1.0)


def test_trend_and_temperature_series(tmp_path):
    _record(tmp_path)
    fig = build_report(tmp_path)
    trend = [t for t in fig.data if t.xaxis == "x4"]
    assert len(trend) == 3 and all(len(t.y) == 2 for t in trend)
    temp = [t for t in fig.data if t.name == "temperature"][0]
    assert len(temp.y) == 3 and temp.y[0] == pytest.approx(33.57, abs=0.05)


def test_report_with_temperature_only(tmp_path):
    _record(tmp_path, windows=())
    fig = build_report(tmp_path)
    assert any("No vibration windows" in a.text for a in fig.layout.annotations)


def test_report_survives_half_written_line(tmp_path):
    _record(tmp_path)
    with open(tmp_path / "vibration.jsonl", "a") as f:
        f.write('{"machine_id": "MG-0')
    assert "window 1" in build_report(tmp_path).layout.title.text


def test_write_report_files_and_refresh(tmp_path):
    _record(tmp_path)
    out = write_report(tmp_path, rpm=3000, refresh_s=5)
    html = out.read_text(encoding="utf-8")
    assert out.name == "fft_report.html"
    assert 'http-equiv="refresh" content="5"' in html
    assert 'src="plotly.min.js"' in html
    assert (tmp_path / "plotly.min.js").stat().st_size > 1_000_000
    assert len(html) < 2_000_000                         # library not repeated in every update
    assert "refresh" not in write_report(tmp_path).read_text(encoding="utf-8")


def test_live_html_updates_after_each_window(tmp_path):
    _record(tmp_path, windows=("healthy",), live=True)
    first = (tmp_path / "fft_report.html").read_text(encoding="utf-8")
    assert "window 0" in first
    h = MachineGuardHandler(rpm=3000, out_dir=tmp_path, log=lambda *_: None, live_html=True)
    for f in window_to_frames(simulate_window_signals("bearing"), window_id=1):
        h.on_vibration(f)
    assert "window 1" in (tmp_path / "fft_report.html").read_text(encoding="utf-8")


def test_live_html_requires_out_dir():
    with pytest.raises(ValueError):
        MachineGuardHandler(live_html=True)
    with pytest.raises(SystemExit):
        main(["--simulate", "--live-html"])


def test_report_cli(tmp_path, capsys):
    _record(tmp_path)
    report_main([str(tmp_path), "--rpm", "3000", "--window", "0"])
    assert "fft_report.html" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        report_main([str(tmp_path / "missing")])


def test_bluetooth_off_gives_friendly_message(monkeypatch, capsys):
    class BleakBluetoothNotAvailableError(Exception):
        pass

    async def fake_run(*args, **kwargs):
        raise BleakBluetoothNotAvailableError("Bluetooth radio is not powered on")

    monkeypatch.setattr(receiver, "run", fake_run)
    main([])
    assert "Bluetooth is off" in capsys.readouterr().out


def test_other_errors_still_raise(monkeypatch):
    async def fake_run(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(receiver, "run", fake_run)
    with pytest.raises(RuntimeError):
        main([])
