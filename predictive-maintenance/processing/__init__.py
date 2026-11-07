"""SmolBugs signal processing: calibrated amplitude spectrum, Welch PSD, band power and harmonic analysis."""

from .config import FFTConfig
from .fft import (
    Spectrum,
    amplitude_spectrum,
    band_power,
    dominant_frequency,
    harmonic_amplitudes,
    preprocess,
    rpm_to_hz,
    velocity_rms_mm_s,
    welch_psd,
)

__all__ = [
    "FFTConfig",
    "Spectrum",
    "amplitude_spectrum",
    "band_power",
    "dominant_frequency",
    "harmonic_amplitudes",
    "preprocess",
    "rpm_to_hz",
    "velocity_rms_mm_s",
    "welch_psd",
]
