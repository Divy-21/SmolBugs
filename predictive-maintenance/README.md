# SmolBugs: FFT Processing

SmolBugs is a predictive-maintenance system that listens to industrial machines (band saws and power drills) through a vibration sensor and flags abnormal behaviour before it turns into a breakdown.

This folder holds the **FFT stage**: the part that takes raw vibration readings and works out *which frequencies* the machine is vibrating at, and how strongly. Everything later in the system (anomaly detection, maintenance recommendations, the dashboard) is built on top of this.

```
ESP32 sensor  →  MQTT  →  [ FFT processing ]  →  features  →  ML anomaly detection  →  dashboard
                            ▲ you are here
```

## Why an FFT?

The accelerometer records how the machine shakes over time, 4,000 times per second. As a raw waveform that's hard to interpret: everything is mixed together.

A **Fast Fourier Transform (FFT)** separates that waveform into its individual frequencies, the way a prism splits light into colours. Different mechanical problems show up at different frequencies:

| What the spectrum shows | What it often means |
|---|---|
| Big peak at **1× running speed** | Imbalance (e.g. a worn or unevenly loaded blade/wheel) |
| Strong peak at **2× running speed** | Misalignment or looseness |
| Raised energy at **high frequencies** (500–1000 Hz) | Impacts, often early bearing or gearbox damage |
| A **new peak** that wasn't there before | Something in a rotating component has changed |

Running speed in Hz is simply `RPM / 60`, so a motor at 3,000 RPM has its 1× peak at 50 Hz.

These are *possible* causes to investigate, not diagnoses. They still need to be confirmed against real machine data.

## Folder layout

```
predictive-maintenance/
├── processing/
│   ├── fft.py          FFT, power spectrum, peaks, harmonics, band energy
│   ├── config.py       Settings (sample rate, window size, frequency bands)
│   └── __init__.py     Lets you `from processing import ...`
├── simulation/
│   ├── synthetic.py    Fake vibration signals with known answers, for testing
│   └── fft_demo.py     Interactive demo: healthy vs faulty machine
├── tests/
│   └── test_fft.py     26 automated tests
└── pytest.ini          Test configuration
```

## Setup (Windows / PowerShell)

From the `predictive-maintenance` folder:

```powershell
# 1. Activate the project's virtual environment
E:\SmolBugs\.venv\Scripts\Activate.ps1
#    Your prompt should now start with (.venv).
#    If you get "running scripts is disabled", run this first:
#    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 2. Install dependencies (first time only)
python -m pip install numpy scipy plotly pytest
```

If the `.venv` folder doesn't exist yet, create it from `E:\SmolBugs` with `python -m venv .venv`.

## Run the tests

```powershell
python -m pytest -q
```

Expected result: `26 passed`.

The tests feed in signals where the right answer is already known (for example, "a 0.5 g vibration at exactly 123.37 Hz") and check that the FFT gets it back. They confirm that frequencies come out within 0.01 Hz and amplitudes within 0.5%.

## See it in action

```powershell
python -m simulation.fft_demo
start smolbugs_fft_demo.html
```

This simulates two machines running at 3,000 RPM and opens an interactive chart in your browser:

- **Healthy (teal):** a small 1× vibration and very little else.
- **Faulty (orange):** a large 1× peak (imbalance), a strong 2× peak (misalignment), and repeated impacts like a damaged bearing.

The page has three charts:

1. **Time domain:** the raw vibration over 0.2 seconds. The faulty machine is visibly rougher, with spikes from the impacts.
2. **FFT amplitude spectrum:** the same data split into frequencies. Dotted lines mark 1×, 2× and 3× running speed. The faulty machine's 1× and 2× peaks tower over the healthy one.
3. **Welch PSD:** a smoothed view of how much energy sits at each frequency (log scale). The faulty machine shows a broad raised hump around 800 Hz from the impacts.

Hover over a line to see exact values, and drag a box to zoom in.

The terminal also prints the numbers behind the charts, for example:

```
--- healthy ---
  dominant: 50.004 Hz @ 0.0498 g
  harmonics: 1x=0.050 g, 2x=0.010 g, 3x=0.001 g
--- faulty ---
  dominant: 50.001 Hz @ 0.2499 g
  harmonics: 1x=0.250 g, 2x=0.120 g, 3x=0.000 g
```

Those match the values the simulation put in, which is how we know the processing is accurate.

## Using it in code

```python
import numpy as np
from processing import (
    FFTConfig, amplitude_spectrum, dominant_frequency, harmonic_amplitudes,
    welch_psd, band_power, velocity_rms_mm_s, rpm_to_hz,
)

cfg = FFTConfig()

# x: one axis of accelerometer data in g, 4096 samples at 4000 Hz
t = np.arange(cfg.window_size) / cfg.sample_rate_hz
x = 1.0 + 0.2 * np.sin(2 * np.pi * 50 * t)   # gravity + a 50 Hz vibration

# 1. Spectrum and its strongest peak
spec = amplitude_spectrum(x, cfg.sample_rate_hz)
freq, amp = dominant_frequency(spec, cfg.min_freq_hz, cfg.max_freq_hz)
print(f"Dominant: {freq:.2f} Hz at {amp:.3f} g")

# 2. Running-speed harmonics (machine at 3000 RPM)
for h in harmonic_amplitudes(spec, rpm_to_hz(3000), n_harmonics=3):
    print(f"{int(h['order'])}x: {h['amplitude']:.3f} g at {h['freq_hz']:.2f} Hz")

# 3. Energy per frequency band, and overall vibration severity
f, psd = welch_psd(x, cfg.sample_rate_hz)
for lo, hi in cfg.bands_hz:
    print(f"{lo:g}-{hi:g} Hz: {band_power(f, psd, lo, hi):.2e} g^2")
print(f"Velocity RMS: {velocity_rms_mm_s(f, psd):.2f} mm/s")
```

Output:

```
Dominant: 50.00 Hz at 0.200 g
1x: 0.200 g at 50.00 Hz
2x: 0.000 g at 98.14 Hz
3x: 0.000 g at 147.95 Hz
...
Velocity RMS: 4.43 mm/s
```

### Main functions

| Function | What it returns |
|---|---|
| `amplitude_spectrum(x, fs)` | A `Spectrum` with `.freqs_hz` and `.amplitude` (peak g at each frequency) |
| `dominant_frequency(spec, min_hz, max_hz)` | `(frequency_hz, amplitude_g)` of the strongest peak in that range |
| `harmonic_amplitudes(spec, fundamental_hz, n_harmonics)` | A list of the peaks at 1×, 2×, 3×… running speed |
| `welch_psd(x, fs)` | `(freqs, psd)`: a smoothed power spectrum in g²/Hz |
| `band_power(freqs, psd, low_hz, high_hz)` | Total vibration energy between two frequencies |
| `velocity_rms_mm_s(freqs, psd)` | Overall vibration severity in mm/s, the industry-standard measure (ISO 10816) |
| `rpm_to_hz(rpm)` | Running speed in Hz |

## Settings

All in `FFTConfig` (`processing/config.py`):

| Setting | Default | Meaning |
|---|---|---|
| `sample_rate_hz` | 4000 | Readings per second from the sensor |
| `window_size` | 4096 | Readings per FFT (≈1.024 s of data) |
| frequency resolution | ≈0.977 Hz | How finely frequencies can be told apart (`sample_rate / window_size`) |
| `max_freq_hz` | 1000 | Highest trustworthy frequency; the ADXL355 sensor filters out anything above about a quarter of its sample rate |
| `bands_hz` | 2–50, 50–200, 200–500, 500–1000 | Frequency ranges used for band energy |

## Design decisions

- **Gravity is removed by subtracting the average.** A sensor sitting still reads 1 g, which would otherwise swamp the spectrum. We deliberately don't remove a straight-line trend as well: on a 1-second window that also strips out part of real low-frequency vibration (about 2% error at 50 Hz).
- **A Hann window is applied before the FFT.** This stops energy from smearing across the whole spectrum because the recording starts and stops mid-vibration.
- **Peak amplitudes are corrected between frequency bins.** A vibration that falls between two FFT bins can read up to 15% too low. A correction specific to the Hann window recovers the true value.

## Known limitations and next steps

- **Frequency bands are generic placeholders.** The 50 Hz band edge lands exactly on the 1× peak of a 3,000 RPM motor, splitting its energy across two bands (visible in the example output above). Bands should be tuned per machine once the real RPMs of the band saw and drill are measured, so each harmonic sits inside a single band.
- **Tested on simulated data only so far.** The next milestone is recording real healthy-machine data from the ESP32 + ADXL355 sensor and checking the spectra look as expected.
- Feature extraction (RMS, kurtosis, crest factor), the MQTT pipeline, and anomaly detection are planned but not part of this module yet.