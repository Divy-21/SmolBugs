"""Test helper: makes run folders by feeding synthetic Bluetooth frames through the real
receiver (ingest.ble_receiver.MachineGuardHandler), so the files are exactly what a
real recording writes. For tests only, never for training."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ingest.ble_receiver import MachineGuardHandler
from processing.ble_protocol import window_to_frames

FS = 1600.0
N = 4096

# (1x, 2x, 3x amplitude in g, extra broadband noise in g)
VIBRATION = {
    "off": (0.0, 0.0, 0.0, 0.0),
    "normal_free": (0.05, 0.010, 0.003, 0.0),
    "normal_load": (0.05, 0.012, 0.004, 0.03),
    "imbalance": (0.25, 0.015, 0.004, 0.0),
    "bent_bit": (0.12, 0.120, 0.010, 0.0),
    "loose_bit": (0.08, 0.040, 0.030, 0.0),
}


def frame_signal(label: str, run_hz: float, gain: float, rng: np.random.Generator, fs: float = FS) -> np.ndarray:
    t = np.arange(N) / fs
    f1 = run_hz + rng.uniform(-0.3, 0.3)
    one, two, three, broad = VIBRATION[label]
    noise = 0.003 if label == "off" else 0.01 + broad
    out = np.empty((N, 3))
    for ax, scale in enumerate((1.0, 0.7, 0.3)):
        ph = rng.uniform(0, 2 * np.pi, 3)
        sig = sum(a * scale * np.sin(2 * np.pi * k * f1 * t + p)
                  for k, a, p in zip((1, 2, 3), (one, two, three), ph))
        out[:, ax] = sig + rng.normal(0, noise, N)
    if label == "loose_bit":  # rattling knocks ringing at ~180 Hz
        ring = np.exp(-np.arange(40) / 6.0) * np.sin(2 * np.pi * 180 * np.arange(40) / fs)
        for s in rng.choice(N - 40, size=int(15 * N / fs), replace=False):
            out[s:s + 40, 0] += 0.6 * rng.uniform(0.5, 1.0) * ring
            out[s:s + 40, 1] += 0.36 * rng.uniform(0.5, 1.0) * ring
    out[:, 2] += 1.0  # gravity
    return out * gain


def record_run(folder: Path, label: str, n_frames: int, run_hz: float, gain: float, seed: int,
               fs: float = FS, temp_mv: int | None = 1886) -> Path:
    rng = np.random.default_rng(seed)
    h = MachineGuardHandler("MG-001", out_dir=folder, log=lambda _m: None)
    for i in range(n_frames):
        if temp_mv is not None:
            h.on_temperature(f"{i},{temp_mv},0.01,0.02,1.00".encode())
        for frame in window_to_frames(frame_signal(label, run_hz, gain, rng, fs), window_id=i,
                                      sample_rate_hz=fs, range_g=4, first_sample_ms=i * 10_000):
            h.on_vibration(frame)
    return folder


def make_dataset(raw: Path, sessions=(1, 2, 3), labels=tuple(VIBRATION), runs: int = 2, frames: int = 4,
                 seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    for s in sessions:
        run_hz, gain = 30.0 + rng.uniform(-1.5, 1.5), rng.uniform(0.8, 1.2)
        for li, label in enumerate(labels):
            for r in range(1, runs + 1):
                record_run(raw / f"S{s}_{label}_{r:02d}", label, frames, run_hz, gain,
                           seed=s * 1000 + li * 10 + r)
    return raw
