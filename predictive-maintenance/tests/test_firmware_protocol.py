"""Cross-checks the firmware's C++ frame encoder against the Python decoder, byte for byte.

Compiles tests/native/protocol_check.cpp with the header from the machine1 sketch.
Skipped automatically if no C++ compiler or the sketch folder isn't found.
"""

import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest

from processing.ble_protocol import (
    LSB_PER_G_INT16,
    SAMPLES_PER_CHUNK,
    BleVibrationDecoder,
    StartFrame,
    encode_data,
    encode_start,
    parse_frame,
)

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]  # E:\SmolBugs
CXX = shutil.which("g++") or shutil.which("clang++")


def _find_header() -> Path | None:
    hits = sorted(REPO_ROOT.glob("*/machine1/machine1/smolbugs_protocol.h"))
    return hits[0] if hits else None


HEADER = _find_header()
pytestmark = pytest.mark.skipif(CXX is None or HEADER is None,
                                reason="needs g++/clang++ and the machine1 sketch folder")


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("native") / "protocol_check"
    subprocess.run([CXX, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-I", str(HEADER.parent),
                    str(HERE / "native" / "protocol_check.cpp"), "-o", str(out)], check=True)
    return out


def _run(binary, n, window_id):
    proc = subprocess.run([str(binary), str(n), str(window_id)], capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode()
    data, frames, i = proc.stdout, [], 0
    while i < len(data):
        (length,) = struct.unpack_from("<H", data, i)
        frames.append(data[i + 2:i + 2 + length])
        i += 2 + length
    return frames


def _pattern(n):
    i = np.arange(n)[:, None]
    a = np.arange(3)[None, :]
    return (((i * 2711 + a * 9973) % 65536) - 32768).astype(np.int16)


@pytest.mark.parametrize("n,window_id", [(4096, 7), (1000, 65535), (32, 0)])
def test_cpp_frames_match_python_byte_for_byte(binary, n, window_id):
    cpp = _run(binary, n, window_id)
    counts = _pattern(n)
    start = StartFrame(window_id, 3998.5, n, SAMPLES_PER_CHUNK, 2, LSB_PER_G_INT16[2], 123456789)
    py = [encode_start(start)] + [
        encode_data(window_id, c, counts[c * SAMPLES_PER_CHUNK:(c + 1) * SAMPLES_PER_CHUNK])
        for c in range(start.chunks)
    ]
    assert len(cpp) == len(py) == 1 + start.chunks
    for idx, (a, b) in enumerate(zip(cpp, py)):
        assert a == b, f"frame {idx} differs"


def test_cpp_frames_decode_to_the_right_window(binary):
    frames = _run(binary, 4096, 42)
    s = parse_frame(frames[0])
    assert (s.window_id, s.sample_rate_hz, s.n_samples, s.first_sample_ms) == (42, 3998.5, 4096, 123456789)
    assert max(len(f) for f in frames) == 197
    dec = BleVibrationDecoder("MG-001")
    window = [dec.feed(f) for f in frames][-1]
    assert window is not None and window.n_samples == 4096
    np.testing.assert_allclose(window.accel_g, _pattern(4096) / 16000.0)
