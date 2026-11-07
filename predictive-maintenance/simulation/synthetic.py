"""Synthetic vibration signals with known ground truth, for tests and demos."""

from __future__ import annotations

import numpy as np


def synth_signal(
    fs: float = 4000.0,
    n: int = 4096,
    tones: list[tuple[float, float]] | None = None,  # (freq_hz, peak_amplitude)
    noise_std: float = 0.0,
    dc: float = 0.0,
    impulse_rate_hz: float | None = None,  # periodic impacts, e.g. a bearing defect
    impulse_amp: float = 0.0,
    seed: int | None = 0,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    x = np.full(n, dc, dtype=float)
    for freq, amp in tones or []:
        x += amp * np.sin(2 * np.pi * freq * t)
    if noise_std:
        x += rng.normal(0.0, noise_std, n)
    if impulse_rate_hz:
        period = int(round(fs / impulse_rate_hz))
        decay = np.exp(-np.arange(40) / 6.0) * np.sin(2 * np.pi * 800 * np.arange(40) / fs)
        for start in range(period // 2, n - decay.size, period):
            x[start : start + decay.size] += impulse_amp * decay
    return x
