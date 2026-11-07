"""FFT amplitude spectrum, Welch PSD, band power, peak and harmonic analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as sps

G = 9.80665  # m/s^2 per g


def rpm_to_hz(rpm: float) -> float:
    return rpm / 60.0


def _as_1d(x) -> np.ndarray:
    arr = np.asarray(x, dtype=float)
    if arr.ndim != 1:
        raise ValueError("signal must be 1-D")
    if arr.size < 16:
        raise ValueError("signal needs at least 16 samples")
    if not np.all(np.isfinite(arr)):
        raise ValueError("signal contains NaN or inf")
    return arr


def preprocess(x, detrend_type: str = "constant") -> np.ndarray:
    """Remove DC (gravity). 'linear' also removes drift but distorts low-frequency
    content on short windows, so it is not the default."""
    return sps.detrend(_as_1d(x), type=detrend_type)


@dataclass(frozen=True)
class Spectrum:
    freqs_hz: np.ndarray
    amplitude: np.ndarray  # single-sided PEAK amplitude, same units as the input

    @property
    def resolution_hz(self) -> float:
        return float(self.freqs_hz[1] - self.freqs_hz[0])


def amplitude_spectrum(x, fs: float, detrend_type: str = "constant") -> Spectrum:
    """Hann-windowed single-sided amplitude spectrum.

    Scaled so a sinusoid of peak amplitude A landing exactly on a bin reads A.
    Off-bin tones read up to ~15% low in the raw bins (Hann scalloping);
    dominant_frequency() and harmonic_amplitudes() correct for this.
    """
    if fs <= 0:
        raise ValueError("fs must be positive")
    xd = preprocess(x, detrend_type)
    n = xd.size
    w = sps.get_window("hann", n)  # periodic (DFT-even), correct for spectral analysis
    amp = np.abs(np.fft.rfft(xd * w)) / w.sum()
    amp[1:] *= 2.0  # fold negative frequencies in
    if n % 2 == 0:
        amp[-1] /= 2.0  # Nyquist bin has no mirror
    return Spectrum(np.fft.rfftfreq(n, d=1.0 / fs), amp)


def welch_psd(
    x,
    fs: float,
    nperseg: int = 1024,
    overlap: float = 0.5,
    detrend_type: str = "constant",
) -> tuple[np.ndarray, np.ndarray]:
    """Welch PSD in (input units)^2 / Hz. Integrating it over all freqs ~= signal variance."""
    if fs <= 0:
        raise ValueError("fs must be positive")
    xa = _as_1d(x)
    nperseg = min(nperseg, xa.size)
    return sps.welch(
        xa,
        fs=fs,
        window="hann",
        nperseg=nperseg,
        noverlap=int(nperseg * overlap),
        detrend=detrend_type,
        scaling="density",
    )


def band_power(freqs: np.ndarray, psd: np.ndarray, low_hz: float, high_hz: float) -> float:
    """Integrated power in [low_hz, high_hz) from a PSD (units^2)."""
    if not low_hz < high_hz:
        raise ValueError("low_hz must be < high_hz")
    df = float(freqs[1] - freqs[0])
    mask = (freqs >= low_hz) & (freqs < high_hz)
    return float(psd[mask].sum() * df)


def velocity_rms_mm_s(
    freqs: np.ndarray, psd_g2_hz: np.ndarray, band_hz: tuple[float, float] = (10.0, 1000.0)
) -> float:
    """RMS velocity (mm/s) from an acceleration PSD in g^2/Hz, integrated in the frequency domain.

    Velocity is the standard machinery severity measure (ISO 10816/20816) and is far
    more sensitive to low-frequency faults like imbalance than raw acceleration.
    """
    lo, hi = band_hz
    mask = (freqs >= max(lo, 1e-9)) & (freqs < hi)
    if not mask.any():
        return 0.0
    f = freqs[mask]
    vel_psd = psd_g2_hz[mask] * G**2 / (2.0 * np.pi * f) ** 2  # (m/s)^2/Hz
    df = float(freqs[1] - freqs[0])
    return float(np.sqrt(vel_psd.sum() * df) * 1000.0)


def _hann_gain(delta: float) -> float:
    """Normalised Hann main-lobe response at an offset of `delta` bins (1.0 at delta=0)."""
    if abs(delta) < 1e-12:
        return 1.0
    if abs(abs(delta) - 1.0) < 1e-12:
        return 0.5
    return float(np.sinc(delta) / (1.0 - delta**2))


def _interpolate_peak(amp: np.ndarray, k: int, df: float) -> tuple[float, float]:
    """Hann bin-ratio interpolation: recovers frequency and amplitude of an off-bin tone.

    For a Hann-windowed tone the magnitude ratio of the two largest bins gives the
    fractional offset exactly; dividing by the window response undoes scalloping loss.
    """
    if k <= 0 or k >= amp.size - 1:
        return k * df, float(amp[k])
    left, peak, right = amp[k - 1], amp[k], amp[k + 1]
    if peak <= 0:
        return k * df, 0.0
    if right >= left:
        delta = (2.0 * right - peak) / (peak + right)
    else:
        delta = -(2.0 * left - peak) / (peak + left)
    delta = float(np.clip(delta, -0.5, 0.5))
    return (k + delta) * df, float(peak / _hann_gain(delta))


def dominant_frequency(
    spec: Spectrum, min_freq_hz: float = 2.0, max_freq_hz: float | None = None
) -> tuple[float, float]:
    """Return (frequency_hz, peak_amplitude) of the strongest component in range."""
    f, amp = spec.freqs_hz, spec.amplitude
    mask = f >= min_freq_hz
    if max_freq_hz is not None:
        mask &= f <= max_freq_hz
    if not mask.any():
        raise ValueError("no bins in the requested frequency range")
    idx = np.flatnonzero(mask)
    k = int(idx[np.argmax(amp[idx])])
    return _interpolate_peak(amp, k, spec.resolution_hz)


def harmonic_amplitudes(
    spec: Spectrum,
    fundamental_hz: float,
    n_harmonics: int = 3,
    tolerance_hz: float | None = None,
) -> list[dict[str, float]]:
    """Peak near each multiple of the running speed (1x, 2x, 3x ...).

    1x dominant -> often imbalance; strong 2x -> often misalignment/looseness.
    Tolerance absorbs small RPM variation under load.
    """
    if fundamental_hz <= 0:
        raise ValueError("fundamental_hz must be positive")
    df = spec.resolution_hz
    tol = 2.0 * df if tolerance_hz is None else tolerance_hz
    nyquist = spec.freqs_hz[-1]
    out = []
    for order in range(1, n_harmonics + 1):
        target = order * fundamental_hz
        if target + tol > nyquist:
            break
        mask = np.abs(spec.freqs_hz - target) <= tol
        idx = np.flatnonzero(mask)
        k = int(idx[np.argmax(spec.amplitude[idx])])
        freq, amp = _interpolate_peak(spec.amplitude, k, df)
        out.append({"order": float(order), "target_hz": target, "freq_hz": freq, "amplitude": amp})
    return out
