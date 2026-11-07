"""Generates fake accelerometer packets in the chunked format the ESP32 will send.

Lets the reassembly + FFT pipeline run end to end before the ADXL355 arrives.
Run from the project root:  python -m simulation.packet_simulator
"""

from __future__ import annotations

import json
import random

import numpy as np

from simulation.synthetic import synth_signal

CONDITIONS = {
    # (1x imbalance g, 2x misalignment g, bearing impact amplitude g)
    "healthy": (0.05, 0.01, 0.0),
    "imbalance": (0.30, 0.02, 0.0),
    "misalignment": (0.08, 0.15, 0.0),
    "bearing": (0.05, 0.01, 0.8),
}


def simulate_window_signals(
    condition: str = "healthy", rpm: float = 3000.0, fs: float = 4000.0, n: int = 4096, seed: int = 0
) -> np.ndarray:
    """(n, 3) array in g for a machine in the given condition. Gravity on z."""
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {list(CONDITIONS)}")
    one_x, two_x, impact = CONDITIONS[condition]
    f1 = rpm / 60.0
    x = synth_signal(fs, n, [(f1, one_x), (2 * f1, two_x)], noise_std=0.01, seed=seed,
                     impulse_rate_hz=87.0 if impact else None, impulse_amp=impact)
    y = synth_signal(fs, n, [(f1, one_x * 0.7), (2 * f1, two_x * 0.7)], noise_std=0.01, seed=seed + 1)
    z = synth_signal(fs, n, [(f1, one_x * 0.3)], noise_std=0.01, dc=1.0, seed=seed + 2)
    return np.column_stack([x, y, z])


def window_to_packets(
    accel_g: np.ndarray,
    machine_id: str = "MG-001",
    window_id: int = 0,
    fs: float = 4000.0,
    chunk_size: int = 512,
    first_sample_ms: int = 0,
    range_g: int = 2,
) -> list[dict]:
    """Split one window into chunk packets, exactly as the firmware should."""
    n = accel_g.shape[0]
    chunks = -(-n // chunk_size)  # ceiling division
    packets = []
    for i in range(chunks):
        part = accel_g[i * chunk_size : (i + 1) * chunk_size]
        packets.append({
            "machine_id": machine_id,
            "window_id": window_id,
            "chunk": i,
            "chunks": chunks,
            "sample_rate_hz": fs,
            "range_g": range_g,
            "first_sample_ms": first_sample_ms,
            "units": "g",
            "x": [round(v, 6) for v in part[:, 0]],
            "y": [round(v, 6) for v in part[:, 1]],
            "z": [round(v, 6) for v in part[:, 2]],
        })
    return packets


def main() -> None:
    from processing import WindowAssembler, analyze_window

    rpm = 3000.0
    assembler = WindowAssembler()
    rng = random.Random(1)

    packets = []
    for wid, condition in enumerate(CONDITIONS):
        packets += window_to_packets(simulate_window_signals(condition, rpm, seed=wid), window_id=wid,
                                     first_sample_ms=wid * 1024)
    rng.shuffle(packets)  # network delivery order isn't guaranteed

    size_kb = len(json.dumps(packets[0]).encode()) / 1024
    print(f"Sending {len(packets)} packets (~{size_kb:.1f} KB each), shuffled order\n")

    names = list(CONDITIONS)
    for p in packets:
        window = assembler.add(p)
        if window is None:
            continue
        r = analyze_window(window, rpm=rpm)
        ax = r["axes"]["x"]
        h = {e["order"]: e["amp_g"] for e in ax["harmonics"]}
        high = ax["band_power_g2"]["500-1000"]
        print(f"window {r['window_id']} ({names[r['window_id']]:<12}) "
              f"1x={h[1]:.3f} g  2x={h[2]:.3f} g  500-1000 Hz={high:.1e} g²  "
              f"velocity={ax['velocity_rms_mm_s']:.2f} mm/s")
    print(f"\n{assembler.stats}")


if __name__ == "__main__":
    main()
