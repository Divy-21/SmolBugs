import random

import numpy as np
import pytest

from processing import PacketError, WindowAssembler
from simulation.packet_simulator import simulate_window_signals, window_to_packets


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _packets(window_id=0, n=4096, chunk_size=512, seed=0):
    data = simulate_window_signals("healthy", n=n, seed=seed)
    return data, window_to_packets(data, window_id=window_id, chunk_size=chunk_size, first_sample_ms=500)


def test_reassembles_in_order():
    data, packets = _packets()
    asm = WindowAssembler()
    results = [asm.add(p) for p in packets]
    assert all(r is None for r in results[:-1])
    w = results[-1]
    assert w.n_samples == 4096 and w.window_id == 0 and w.first_sample_ms == 500
    np.testing.assert_allclose(w.accel_g, data, atol=1e-6)
    assert asm.stats.completed == 1 and asm.pending_windows == 0


def test_reassembles_out_of_order_and_interleaved():
    d0, p0 = _packets(window_id=0, seed=0)
    d1, p1 = _packets(window_id=1, seed=5)
    packets = p0 + p1
    random.Random(3).shuffle(packets)
    asm = WindowAssembler()
    done = {w.window_id: w for w in (asm.add(p) for p in packets) if w is not None}
    assert set(done) == {0, 1}
    np.testing.assert_allclose(done[0].accel_g, d0, atol=1e-6)
    np.testing.assert_allclose(done[1].accel_g, d1, atol=1e-6)
    assert done[0].first_sample_ms == 500


def test_same_window_id_different_machines_kept_apart():
    _, p = _packets()
    other = [dict(pk, machine_id="DRILL-01") for pk in p]
    asm = WindowAssembler()
    done = [w for w in (asm.add(x) for pair in zip(p, other) for x in pair) if w]
    assert sorted(w.machine_id for w in done) == ["DRILL-01", "MG-001"]


def test_uneven_last_chunk():
    data, packets = _packets(n=1000, chunk_size=300)
    assert len(packets) == 4 and len(packets[-1]["x"]) == 100
    asm = WindowAssembler()
    w = [asm.add(p) for p in packets][-1]
    assert w.n_samples == 1000


def test_duplicate_chunk_ignored():
    _, packets = _packets()
    asm = WindowAssembler()
    asm.add(packets[0])
    assert asm.add(packets[0]) is None
    assert asm.stats.duplicate_chunks == 1
    w = [asm.add(p) for p in packets[1:]][-1]
    assert w.n_samples == 4096


def test_lost_chunk_window_expires():
    clock = FakeClock()
    _, packets = _packets()
    asm = WindowAssembler(timeout_s=5.0, clock=clock)
    for p in packets[:-1]:  # last chunk never arrives
        asm.add(p)
    assert asm.pending_windows == 1
    clock.t = 6.0
    assert asm.expire() == [("MG-001", 0)]
    assert asm.pending_windows == 0 and asm.stats.dropped_incomplete == 1


def test_counts_converted_to_g():
    asm = WindowAssembler()
    w = asm.add({"machine_id": "m", "window_id": 0, "chunk": 0, "chunks": 1, "sample_rate_hz": 4000,
                 "range_g": 4, "units": "counts", "x": [128_000] * 16, "y": [0] * 16, "z": [-64_000] * 16})
    assert w.axis("x")[0] == 1.0 and w.axis("z")[0] == -0.5


def test_clipped_fraction():
    asm = WindowAssembler()
    w = asm.add({"machine_id": "m", "window_id": 0, "chunk": 0, "chunks": 1, "sample_rate_hz": 4000,
                 "range_g": 2, "x": [1.99] * 4 + [0.0] * 12, "y": [0.0] * 16, "z": [0.0] * 16})
    assert w.clipped_fraction() == pytest.approx(4 / 48)


def _good():
    return {"machine_id": "m", "window_id": 0, "chunk": 0, "chunks": 2, "sample_rate_hz": 4000,
            "x": [0.0] * 8, "y": [0.0] * 8, "z": [0.0] * 8}


@pytest.mark.parametrize("change", [
    {"chunk": 2},
    {"chunks": 0},
    {"sample_rate_hz": 0},
    {"range_g": 16},
    {"units": "volts"},
    {"y": [0.0] * 7},
    {"x": [], "y": [], "z": []},
    {"x": [float("nan")] * 8},
    {"window_id": "abc"},
])
def test_rejects_bad_packets(change):
    asm = WindowAssembler()
    with pytest.raises(PacketError):
        asm.add({**_good(), **change})
    assert asm.stats.rejected_packets == 1


def test_rejects_missing_keys():
    p = _good()
    del p["z"]
    with pytest.raises(PacketError, match="missing"):
        WindowAssembler().add(p)


def test_rejects_inconsistent_chunks_in_same_window():
    asm = WindowAssembler()
    asm.add(_good())
    with pytest.raises(PacketError, match="inconsistent"):
        asm.add({**_good(), "chunk": 1, "sample_rate_hz": 3990})


def test_current_single_sample_packets_are_rejected():
    """The packet format the ESP32 sends today can't feed the FFT; make that loud."""
    current = {"machine_id": "MG-001", "timestamp_ms": 96847,
               "sensors": [{"id": "sensor_1", "type": "accelerometer", "x_g": 0.0, "y_g": 0.0, "z_g": 0.0}]}
    with pytest.raises(PacketError, match="missing"):
        WindowAssembler().add(current)
