"""Compiles machine1.ino on the PC against stand-in Arduino/BLE libraries
(tests/native/arduino_stubs, matching the real library signatures), runs it with a fake
connected receiver, and checks the frames it sends decode and analyse correctly.

This tests the sketch's logic, not the real ESP32 Bluetooth stack.
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


@pytest.fixture(scope="module")
def sketch_binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("sketch") / "sketch"
    subprocess.run([CXX, "-std=c++17", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Werror",
                    "-I", str(HERE / "native" / "arduino_stubs"), "-I", str(SKETCH.parent),
                    "-x", "c++", str(HERE / "native" / "sketch_harness.cpp"), "-o", str(out)], check=True)
    return out


def _run(binary, mtu=247):
    proc = subprocess.run([str(binary), str(mtu)], capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode()
    data, frames, i = proc.stdout, [], 0
    while i < len(data):
        (n,) = struct.unpack_from("<H", data, i)
        frames.append(data[i + 2:i + 2 + n])
        i += 2 + n
    return frames, proc.stderr.decode()


def test_sketch_sends_decodable_windows(sketch_binary):
    frames, log = _run(sketch_binary)
    assert "WINDOWS 2 ERRORS 0" in log
    assert len(frames) == 2 * 129  # START + 128 DATA per window
    assert {parse_frame(f).window_id for f in frames} == {0, 1}

    handler = MachineGuardHandler(rpm=3000, log=lambda *_: None)
    results = [r for r in (handler.on_vibration(f) for f in frames) if r]
    assert len(results) == 2 and handler.bad_frames == 0

    x = results[0]["axes"]["x"]
    assert x["dominant_freq_hz"] == pytest.approx(50.0, abs=0.05)
    h = {e["order"]: e["amp_g"] for e in x["harmonics"]}
    assert h[1] == pytest.approx(0.05, rel=0.03)   # test signal: 0.05 g at 1x
    assert h[2] == pytest.approx(0.01, rel=0.10)   # 0.01 g at 2x
    z = results[0]["axes"]["z"]
    assert z["dominant_amp_g"] == pytest.approx(0.015, rel=0.10)  # 1 g gravity removed
    assert results[0]["warnings"] == []


def test_sketch_temperature_messages_still_sent(sketch_binary):
    _, log = _run(sketch_binary)
    temps = [line for line in log.splitlines() if line.startswith("TEMP ")]
    assert len(temps) > 30
    assert temps[0] == "TEMP 1,1582,69062"


def test_sketch_refuses_vibration_when_mtu_too_small(sketch_binary):
    frames, log = _run(sketch_binary, mtu=185)
    assert frames == []                # nothing sent rather than truncated frames
    assert "TEMP " in log              # temperature unaffected
