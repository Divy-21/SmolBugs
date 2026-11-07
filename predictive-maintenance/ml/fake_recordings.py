"""Makes fake drill recordings in the exact format the real recorder saves.

Lets the whole ML pipeline run before the IMU is wired up. When real recordings
exist, point train.py at data/raw instead of data/fake/raw.

    python -m ml.fake_recordings --out data/fake/raw

Each session gets a slightly different running speed and sensor mount sensitivity,
like taking the sensor off and clipping it back on.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from simulation.synthetic import synth_signal

from .settings import FS, LABELS

# (1x, 2x, 3x amplitude in g, extra broadband noise in g)
VIBRATION = {
    "off": (0.0, 0.0, 0.0, 0.0),
    "normal_free": (0.05, 0.010, 0.003, 0.0),
    "normal_load": (0.05, 0.012, 0.004, 0.03),
    "imbalance": (0.25, 0.015, 0.004, 0.0),
    "bent_bit": (0.12, 0.120, 0.010, 0.0),
    "loose_bit": (0.08, 0.040, 0.030, 0.0),
    "overheat": (0.05, 0.012, 0.004, 0.03),
}
TEMP_RISE_C_PER_S = {"overheat": 0.15, "normal_load": 0.02}


def impacts(n: int, fs: float, rng: np.random.Generator, rate_hz: float = 15.0, amp: float = 0.6) -> np.ndarray:
    """Random rattling knocks, like a loose bit, ringing at about 180 Hz."""
    x = np.zeros(n)
    ring = np.exp(-np.arange(30) / 5.0) * np.sin(2 * np.pi * 180 * np.arange(30) / fs)
    for start in rng.choice(n - 30, size=int(rate_hz * n / fs), replace=False):
        x[start : start + 30] += amp * rng.uniform(0.5, 1.0) * ring
    return x


def fake_run(label: str, seconds: float, run_hz: float, gain: float, ambient_c: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = int(seconds * FS)
    f1 = run_hz + rng.uniform(-0.3, 0.3)
    one, two, three, broadband = VIBRATION[label]
    tones = [(f1, one), (2 * f1, two), (3 * f1, three)]
    if label == "loose_bit":
        tones += [(k * f1, 0.03 / k) for k in range(4, 8)]
    noise = 0.005 if label == "off" else 0.01 + broadband
    x = synth_signal(FS, n, tones, noise_std=noise, seed=seed)
    y = synth_signal(FS, n, [(f, a * 0.7) for f, a in tones], noise_std=noise, seed=seed + 1)
    z = synth_signal(FS, n, [(f, a * 0.3) for f, a in tones], noise_std=noise, dc=1.0, seed=seed + 2)
    if label == "loose_bit":
        x += impacts(n, FS, rng)
        y += 0.6 * impacts(n, FS, rng)
    accel = np.column_stack([x, y, z]) * gain

    # Thermistor updates twice a second, so the value is held between readings
    t_s = np.arange(n) / FS
    rise = TEMP_RISE_C_PER_S.get(label, 0.0)
    temp = ambient_c + rise * t_s + 0.01 * t_s
    temp = np.repeat(temp[::500], 500)[:n] + np.repeat(rng.normal(0, 0.05, n // 500 + 1), 500)[:n]

    t_us = np.arange(n) * 1000
    df = pd.DataFrame({"t_us": t_us, "ax": accel[:, 0], "ay": accel[:, 1], "az": accel[:, 2], "temp_c": temp})
    if rng.random() < 0.1:  # occasionally drop a few samples, like a real serial link
        df = df.drop(index=rng.choice(n, size=3, replace=False)).reset_index(drop=True)
    return df


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="data/fake/raw")
    p.add_argument("--sessions", type=int, default=3)
    p.add_argument("--runs", type=int, default=3, help="runs per label per session")
    p.add_argument("--seconds", type=float, default=20.0, help="length of each run")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    count = 0
    for session in range(1, args.sessions + 1):
        run_hz = 30.0 + rng.uniform(-1.5, 1.5)     # about 1800 RPM
        gain = rng.uniform(0.8, 1.2)               # mount is never exactly the same
        ambient = 21.0 + rng.uniform(-1.0, 3.0)
        for label in LABELS:
            for run in range(1, args.runs + 1):
                seed = session * 10_000 + LABELS.index(label) * 100 + run
                df = fake_run(label, args.seconds, run_hz, gain, ambient, seed)
                df.to_csv(out / f"S{session}_{label}_{run:02d}.csv", index=False, float_format="%.5f")
                count += 1
    print(f"wrote {count} fake runs to {out}")


if __name__ == "__main__":
    main()
