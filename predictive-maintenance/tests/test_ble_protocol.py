import numpy as np
import pytest

from processing import PacketError
from processing.ble_protocol import (
    MIN_MTU,
    BleVibrationDecoder,
    DataFrame,
    StartFrame,
    encode_data,
    encode_start,
    g_to_counts,
    parse_frame,
    window_to_frames,
)
from simulation.packet_simulator import simulate_window_signals


def test_frame_sizes():
    frames = window_to_frames(simulate_window_signals())
    assert len(frames[0]) == 17
    assert len(frames) == 129 and all(len(f) == 197 for f in frames[1:])
    assert MIN_MTU == 200


def test_start_round_trip():
    s = StartFrame(65535, 3998.5, 4096, 32, 4, 8000, 4_000_000_000)
    assert parse_frame(encode_start(s)) == s
    assert s.chunks == 128


def test_data_round_trip_keeps_negative_and_extreme_values():
    counts = np.array([[-32768, 32767, 0], [-1, 1, -12345]], dtype=np.int16)
    f = parse_frame(encode_data(3, 9, counts))
    assert isinstance(f, DataFrame) and (f.window_id, f.chunk) == (3, 9)
    np.testing.assert_array_equal(f.counts, counts)


def test_quantisation_and_saturation():
    np.testing.assert_array_equal(g_to_counts(np.array([1.0, -0.5, 3.0, -3.0]), 16000),
                                  [16000, -8000, 32767, -32768])


def test_decoder_reassembles_window_within_quantisation():
    accel = simulate_window_signals("imbalance")
    dec = BleVibrationDecoder("MG-001")
    windows = [w for w in (dec.feed(f) for f in window_to_frames(accel, window_id=5)) if w]
    assert len(windows) == 1
    w = windows[0]
    assert (w.machine_id, w.window_id, w.n_samples) == ("MG-001", 5, 4096)
    np.testing.assert_allclose(w.accel_g, accel, atol=0.5 / 16000 + 1e-12)  # half an LSB


def test_decoder_handles_uneven_last_chunk():
    accel = simulate_window_signals(n=1000)
    dec = BleVibrationDecoder("m")
    frames = window_to_frames(accel)
    assert len(frames[-1]) == 5 + 8 * 6
    assert [dec.feed(f) for f in frames][-1].n_samples == 1000


def test_lost_start_frame_drops_window_quietly():
    frames = window_to_frames(simulate_window_signals())
    dec = BleVibrationDecoder("m")
    assert all(dec.feed(f) is None for f in frames[1:])
    assert dec.stats.orphan_data_frames == 128


def test_lost_data_frame_window_never_completes_but_next_one_does():
    dec = BleVibrationDecoder("m")
    first = window_to_frames(simulate_window_signals(), window_id=0)
    del first[50]
    second = window_to_frames(simulate_window_signals(), window_id=1)
    results = [dec.feed(f) for f in first + second]
    done = [w.window_id for w in results if w]
    assert done == [1]


def test_truncated_frame_reports_mtu_hint():
    frames = window_to_frames(simulate_window_signals())
    dec = BleVibrationDecoder("m")
    dec.feed(frames[0])
    with pytest.raises(PacketError, match="MTU"):
        dec.feed(frames[1][:182])  # what a 185-byte MTU would deliver


@pytest.mark.parametrize("bad", [
    b"",
    b"\x09abc",
    b"\x01" + b"\x00" * 10,                       # short START
    b"\x02\x00\x00\x00\x00" + b"\x00" * 7,        # DATA body not whole samples
    b"\x02\x00\x00\x00\x00",                      # DATA with no samples
])
def test_rejects_malformed_frames(bad):
    with pytest.raises(PacketError):
        parse_frame(bad)


def test_rejects_bad_start_values():
    good = StartFrame(0, 4000.0, 4096, 32, 2, 16000, 0)
    for change in ({"sample_rate_hz": 0.0}, {"range_g": 3}, {"n_samples": 0}, {"samples_per_chunk": 0}):
        with pytest.raises(PacketError):
            parse_frame(encode_start(StartFrame(**{**good.__dict__, **change})))


def test_data_chunk_out_of_range():
    dec = BleVibrationDecoder("m")
    dec.feed(encode_start(StartFrame(0, 4000.0, 64, 32, 2, 16000, 0)))
    with pytest.raises(PacketError, match="out of range"):
        dec.feed(encode_data(0, 2, np.zeros((32, 3))))
