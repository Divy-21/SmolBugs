"""FFT settings. Keep one config per machine type (band saw, drill)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FFTConfig:
    sample_rate_hz: float = 4000.0
    # 4096 samples @ 4 kHz = 1.024 s window, ~0.977 Hz resolution.
    window_size: int = 4096
    # "constant" removes the gravity/DC offset. "linear" also removes drift, but on a
    # ~1 s window it partly fits low-frequency vibration and distorts it (~2% at 50 Hz).
    detrend_type: str = "constant"
    # Welch: shorter segments averaged together -> smoother PSD, coarser resolution.
    welch_nperseg: int = 1024
    welch_overlap: float = 0.5
    # Ignore near-DC bins when picking the dominant frequency.
    min_freq_hz: float = 2.0
    # ADXL355 internal low-pass sits at ODR/4, so at 4 kHz ODR the usable band is ~1 kHz.
    max_freq_hz: float = 1000.0
    # Generic starting bands. Tune per machine once you know its RPM/tooth-pass frequencies.
    bands_hz: tuple[tuple[float, float], ...] = (
        (2.0, 50.0),
        (50.0, 200.0),
        (200.0, 500.0),
        (500.0, 1000.0),
    )

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.window_size < 16:
            raise ValueError("window_size must be >= 16")
        if not 0.0 <= self.welch_overlap < 1.0:
            raise ValueError("welch_overlap must be in [0, 1)")
        if self.detrend_type not in ("linear", "constant"):
            raise ValueError("detrend_type must be 'linear' or 'constant'")
        for lo, hi in self.bands_hz:
            if not 0 <= lo < hi:
                raise ValueError(f"invalid band ({lo}, {hi})")

    @property
    def resolution_hz(self) -> float:
        return self.sample_rate_hz / self.window_size

    @property
    def window_duration_s(self) -> float:
        return self.window_size / self.sample_rate_hz
