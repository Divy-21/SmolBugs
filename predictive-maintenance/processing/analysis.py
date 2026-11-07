"""Runs a complete accelerometer window through the FFT and summarises the result."""

from __future__ import annotations

from typing import Any

from .assembler import AXES, AssembledWindow
from .config import FFTConfig
from .fft import (
    amplitude_spectrum,
    band_power,
    dominant_frequency,
    harmonic_amplitudes,
    rpm_to_hz,
    velocity_rms_mm_s,
    welch_psd,
)


def analyze_axis(x, fs: float, cfg: FFTConfig = FFTConfig(), rpm: float | None = None) -> dict[str, Any]:
    spec = amplitude_spectrum(x, fs, cfg.detrend_type)
    max_f = min(cfg.max_freq_hz, fs / 2)
    freq, amp = dominant_frequency(spec, cfg.min_freq_hz, max_f)
    f, psd = welch_psd(x, fs, cfg.welch_nperseg, cfg.welch_overlap, cfg.detrend_type)

    result: dict[str, Any] = {
        "dominant_freq_hz": freq,
        "dominant_amp_g": amp,
        "velocity_rms_mm_s": velocity_rms_mm_s(f, psd, (10.0, max_f)),
        "band_power_g2": {f"{lo:g}-{hi:g}": band_power(f, psd, lo, min(hi, fs / 2)) for lo, hi in cfg.bands_hz},
    }
    if rpm:
        result["harmonics"] = [
            {"order": int(h["order"]), "freq_hz": h["freq_hz"], "amp_g": h["amplitude"]}
            for h in harmonic_amplitudes(spec, rpm_to_hz(rpm), n_harmonics=3)
        ]
    return result


def analyze_window(window: AssembledWindow, cfg: FFTConfig = FFTConfig(), rpm: float | None = None) -> dict[str, Any]:
    """FFT results for all three axes, plus data-quality flags.

    Uses the window's own sample rate, not the config default, so a sensor
    running at a slightly different ODR is still analysed correctly.
    """
    warnings = []
    if window.n_samples != cfg.window_size:
        warnings.append(f"expected {cfg.window_size} samples, got {window.n_samples}")
    clipped = window.clipped_fraction()
    if clipped > 0:
        warnings.append(f"{clipped:.1%} of samples near full scale; consider a larger range_g")

    return {
        "machine_id": window.machine_id,
        "window_id": window.window_id,
        "first_sample_ms": window.first_sample_ms,
        "sample_rate_hz": window.sample_rate_hz,
        "n_samples": window.n_samples,
        "resolution_hz": window.sample_rate_hz / window.n_samples,
        "clipped_fraction": clipped,
        "warnings": warnings,
        "axes": {ax: analyze_axis(window.axis(ax), window.sample_rate_hz, cfg, rpm) for ax in AXES},
    }
