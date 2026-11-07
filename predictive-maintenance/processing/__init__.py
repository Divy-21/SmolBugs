"""SmolBugs signal processing: FFT, chunk reassembly, window analysis and temperature."""

from .analysis import analyze_axis, analyze_window
from .assembler import AssembledWindow, PacketError, WindowAssembler
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
from .temperature import ThermistorConfig, temperatures_from_packet, voltage_to_celsius

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
    "WindowAssembler",
    "AssembledWindow",
    "PacketError",
    "analyze_window",
    "analyze_axis",
    "ThermistorConfig",
    "voltage_to_celsius",
    "temperatures_from_packet",
]
