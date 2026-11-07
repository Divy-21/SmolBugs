"""Compiles machine1.ino on the PC against stand-in Arduino/BLE/Wire libraries
(tests/native/arduino_stubs, matching the real library signatures), with a simulated
LSM303D (its own sample clock, 32-sample FIFO, overrun) and a fake connected receiver.
Checks the frames the sketch sends decode and analyse correctly.

This tests the sketch's logic, not the real ESP32 Bluetooth/I2C hardware.
Skipped automatically if no C++ compiler or the sketch folder isn't found.
"""

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from ingest.ble_receiver import MachineGuardHandler
from processing.ble_protocol import parse_frame

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
CXX = shutil.which("g++") or shutil.which("clang++")
SKETCH = next(iter(sorted(REPO_ROOT.glob("*/machine1/machine1/machine1.ino"))), None)
pytestmark = pytest.mark.skipif(CXX is None or SKETCH is None,
                                reason="needs g++/clang++ and the machine1 sketch folder")

TONE_HZ, TONE_G = 49.6, 0.10  # what the simulated LSM303D vibrates at (see Wire.h stub)


@pytest.fixture(scope="module")
def sketch_binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("sketch") / "sketch"
    subprocess.run([CXX, "-std=c++17", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Werror",
                    "-I", str(HERE / "native" / "arduino_stubs"), "-I", str(SKETCH.parent),
                    "-x", "c++", str(HERE / "native" / "sketch_harness.cpp"), "-o", str(out)], check=True)
    return out


def _run(binary, mtu=247, odr=1600.0, stall_ms=0, no_sensor=0):
    proc = subprocess.run([str(binary), str(mtu), str(odr), str(stall_ms), str(no_sensor)],
                          capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode()
    data, frames, i = proc.stdout, [], 0
    while i < len(data):
        (n,) = struct.unpack_from("<H", data, i)
        frames.append(data[i + 2:i + 2 + n])
        i += 2 + n
    return frames, proc.stderr.decode()


def _analyse(frames):
    handler = MachineGuardHandler(rpm=2976, log=lambda *_: None)
    results = [r for r in (handler.on_vibration(f) for f in frames) if r]
    assert handler.bad_frames == 0
    return results


def test_sketch_captures_and_sends_correct_vibration(sketch_binary):
    frames, log = _run(sketch_binary)
    assert "INIT 1 WINDOWS 2 FAILURES 0 ERRORS 0" in log
    assert len(frames) == 2 * 129
    start = parse_frame(frames[0])
    assert (start.n_samples, start.range_g, start.lsb_per_g) == (4096, 4, 8197)
    assert start.sample_rate_hz == pytest.approx(1600, rel=0.002)

    results = _analyse(frames)
    assert len(results) == 2
    for r in results:
        x = r["axes"]["x"]
        assert x["dominant_freq_hz"] == pytest.approx(TONE_HZ, abs=0.02)
        assert x["dominant_amp_g"] == pytest.approx(TONE_G, rel=0.01)
        assert x["harmonics"][1]["amp_g"] == pytest.approx(0.02, rel=0.05)
        assert r["resolution_hz"] == pytest.approx(1600 / 4096, rel=0.002)
        assert r["warnings"] == []


def test_sketch_measures_real_sensor_rate(sketch_binary):
    frames, _ = _run(sketch_binary, odr=1563.0)  # sensor clock 2.3% slow
    assert parse_frame(frames[0]).sample_rate_hz == pytest.approx(1563, rel=0.002)
    for r in _analyse(frames):
        # with the nominal 1600 Hz this would read ~50.8 Hz
        assert r["axes"]["x"]["dominant_freq_hz"] == pytest.approx(TONE_HZ, abs=0.05)


def test_fifo_absorbs_short_stall(sketch_binary):
    _, log = _run(sketch_binary, stall_ms=15)
    assert "WINDOWS 2 FAILURES 0" in log


def test_long_stall_discards_window_instead_of_sending_bad_data(sketch_binary):
    frames, log = _run(sketch_binary, stall_ms=40)
    assert "WINDOWS 1 FAILURES 1" in log
    results = _analyse(frames)
    assert len(results) == 1
    assert results[0]["axes"]["x"]["dominant_freq_hz"] == pytest.approx(TONE_HZ, abs=0.02)


def test_status_messages_carry_accelerometer(sketch_binary):
    _, log = _run(sketch_binary)
    status = [l[len("STATUS "):] for l in log.splitlines() if l.startswith("STATUS ")]
    assert len(status) > 25
    fields = status[0].split(",")
    assert len(fields) == 5 and fields[0] == "1"
    assert float(fields[4]) == pytest.approx(-0.80, abs=0.01)


def test_no_vibration_when_mtu_too_small(sketch_binary):
    frames, log = _run(sketch_binary, mtu=185)
    assert frames == [] and "STATUS " in log


def test_no_vibration_without_sensor(sketch_binary):
    frames, log = _run(sketch_binary, no_sensor=1)
    assert frames == [] and "INIT 0" in log and "STATUS " in log
