import numpy as np
import pytest

from processing import (
    FFTConfig,
    amplitude_spectrum,
    band_power,
    dominant_frequency,
    harmonic_amplitudes,
    preprocess,
    rpm_to_hz,
    velocity_rms_mm_s,
    welch_psd,
)
from simulation.synthetic import synth_signal

FS, N = 4000.0, 4096
DF = FS / N


def test_resolution_matches_spec():
    spec = amplitude_spectrum(np.random.default_rng(0).normal(size=N), FS)
    assert spec.resolution_hz == pytest.approx(0.9765625)
    assert spec.freqs_hz[-1] == pytest.approx(FS / 2)
    assert spec.freqs_hz.size == N // 2 + 1


def test_rpm_to_hz():
    assert rpm_to_hz(3000) == 50.0


def test_bin_centred_tone_reads_exact_amplitude():
    f = 50 * DF  # exactly on bin 50
    spec = amplitude_spectrum(synth_signal(FS, N, [(f, 0.3)]), FS)
    assert spec.amplitude[50] == pytest.approx(0.3, rel=1e-9)
    # Hann main lobe: adjacent bins are exactly half
    assert spec.amplitude[49] == pytest.approx(0.15, rel=1e-6)


def test_gravity_removed_by_default():
    t = np.arange(N) / FS
    x = 1.0 + 0.1 * np.sin(2 * np.pi * 100 * DF * t)  # gravity + tone
    spec = amplitude_spectrum(x, FS)
    assert spec.amplitude[0] < 1e-12
    assert np.argmax(spec.amplitude) == 100


def test_linear_detrend_option_removes_drift():
    t = np.arange(N) / FS
    x = 1.0 + 0.05 * t + 0.1 * np.sin(2 * np.pi * 100 * DF * t)
    spec = amplitude_spectrum(x, FS, detrend_type="linear")
    assert spec.amplitude[0] < 1e-6
    assert np.argmax(spec.amplitude) == 100


@pytest.mark.parametrize("freq", [50.0, 50.4, 123.37, 487.9])
def test_off_bin_tone_interpolation(freq):
    spec = amplitude_spectrum(synth_signal(FS, N, [(freq, 0.5)]), FS)
    f_est, a_est = dominant_frequency(spec)
    assert f_est == pytest.approx(freq, abs=0.01)
    assert a_est == pytest.approx(0.5, rel=0.005)


def test_dominant_frequency_respects_range():
    x = synth_signal(FS, N, [(1.0, 2.0), (60.0, 0.1), (1500.0, 1.0)])
    f, _ = dominant_frequency(amplitude_spectrum(x, FS), min_freq_hz=2.0, max_freq_hz=1000.0)
    assert f == pytest.approx(60.0, abs=0.05)


def test_welch_parseval_power_equals_variance():
    x = synth_signal(FS, N, [(75.0, 0.2), (310.0, 0.1)], noise_std=0.05, seed=3)
    f, psd = welch_psd(x, FS)
    total = band_power(f, psd, 0.0, FS / 2 + 1)
    assert total == pytest.approx(np.var(preprocess(x)), rel=0.03)


def test_band_power_isolates_tone():
    x = synth_signal(FS, N, [(120.0, 0.4)], noise_std=0.001)
    f, psd = welch_psd(x, FS)
    in_band = band_power(f, psd, 50, 200)
    out_band = band_power(f, psd, 200, 2000)
    assert in_band == pytest.approx(0.4**2 / 2, rel=0.03)  # sine power = A^2/2
    assert out_band < in_band * 1e-3


def test_velocity_rms_of_known_sine():
    # 0.5 g at 50 Hz -> v_peak = 0.5*9.80665/(2*pi*50) m/s, v_rms = v_peak/sqrt(2)
    x = synth_signal(FS, N, [(50.0, 0.5)])
    f, psd = welch_psd(x, FS)
    expected = 0.5 * 9.80665 / (2 * np.pi * 50) / np.sqrt(2) * 1000
    assert velocity_rms_mm_s(f, psd) == pytest.approx(expected, rel=0.03)


def test_scalloping_corrected_at_worst_case():
    f = 50.5 * DF  # halfway between bins: raw bin reads ~15% low
    spec = amplitude_spectrum(synth_signal(FS, N, [(f, 1.0)]), FS)
    assert spec.amplitude.max() < 0.86
    assert dominant_frequency(spec)[1] == pytest.approx(1.0, rel=0.005)


def test_harmonics_found():
    x = synth_signal(FS, N, [(49.7, 0.2), (99.4, 0.08), (149.1, 0.03)], noise_std=0.002)
    h = harmonic_amplitudes(amplitude_spectrum(x, FS), fundamental_hz=50.0, n_harmonics=3)
    assert [e["amplitude"] for e in h] == pytest.approx([0.2, 0.08, 0.03], rel=0.05)
    assert h[1]["freq_hz"] == pytest.approx(99.4, abs=0.1)


def test_harmonics_stop_at_nyquist():
    spec = amplitude_spectrum(synth_signal(FS, N, [(900.0, 0.1)]), FS)
    assert len(harmonic_amplitudes(spec, 900.0, n_harmonics=5)) == 2


@pytest.mark.parametrize("bad", [np.zeros((10, 2)), np.zeros(5), np.array([np.nan] * 64)])
def test_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        amplitude_spectrum(bad, FS)


def test_rejects_bad_fs():
    with pytest.raises(ValueError):
        amplitude_spectrum(np.zeros(64), 0)


def test_config_matches_project_spec():
    cfg = FFTConfig()
    assert cfg.resolution_hz == pytest.approx(0.9765625)
    assert cfg.window_duration_s == pytest.approx(1.024)


@pytest.mark.parametrize(
    "kwargs", [{"welch_overlap": 1.0}, {"bands_hz": ((100, 50),)}, {"sample_rate_hz": 0}, {"detrend_type": "x"}]
)
def test_config_validation(kwargs):
    with pytest.raises(ValueError):
        FFTConfig(**kwargs)


def test_impacts_show_up_as_broadband_energy():
    healthy = synth_signal(FS, N, [(50.0, 0.2)], noise_std=0.02)
    faulty = synth_signal(FS, N, [(50.0, 0.2)], noise_std=0.02, impulse_rate_hz=87.0, impulse_amp=1.5)
    fh, ph = welch_psd(healthy, FS)
    ff, pf = welch_psd(faulty, FS)
    assert band_power(ff, pf, 500, 1000) > 5 * band_power(fh, ph, 500, 1000)
