# SmolBugs: FFT Processing

SmolBugs is a predictive-maintenance system that listens to industrial machines (band saws and power drills) through a vibration sensor and flags abnormal behaviour before it turns into a breakdown.

This folder holds the **processing stage**: it takes raw vibration readings and works out *which frequencies* the machine is vibrating at, and how strongly, and converts the thermistor reading to a temperature. Everything later in the system (anomaly detection, maintenance recommendations, the dashboard) is built on top of this.

```
ESP32 sensor  →  MQTT  →  [ reassemble chunks → FFT analysis ]  →  ML anomaly detection  →  dashboard
                            ▲ you are here
```

**Current status:** the accelerometer (ADXL355) hasn't arrived yet, so the vibration side runs on simulated packets for now. The thermistor is connected and its readings can be converted today.

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
│   ├── fft.py              FFT, power spectrum, peaks, harmonics, band energy
│   ├── config.py           Settings (sample rate, window size, frequency bands)
│   ├── assembler.py        Rebuilds full windows from chunked MQTT packets
│   ├── analysis.py         Runs a full window through the FFT, returns results
│   ├── temperature.py      Thermistor voltage → °C
│   └── __init__.py         Lets you `from processing import ...`
├── simulation/
│   ├── synthetic.py        Fake vibration signals with known answers
│   ├── packet_simulator.py Fake ESP32 packets in the chunked format
│   └── fft_demo.py         Interactive demo: healthy vs faulty machine
├── tests/                  69 automated tests
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

Expected result: `69 passed`.

The FFT tests feed in signals where the right answer is already known (for example, "a 0.5 g vibration at exactly 123.37 Hz") and check that the FFT gets it back. They confirm that frequencies come out within 0.01 Hz and amplitudes within 0.5%.

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

## Packet format for vibration data

The FFT needs 4,096 readings taken at a steady 4,000 per second. Sending one MQTT message per reading isn't workable, so the ESP32 buffers readings (timed by the sensor's own clock) and sends each window as 8 chunks of 512 samples:

```json
{
  "machine_id": "MG-001",
  "window_id": 1234,
  "chunk": 0,
  "chunks": 8,
  "sample_rate_hz": 4000,
  "range_g": 2,
  "first_sample_ms": 96847,
  "units": "g",
  "x": [512 values],
  "y": [512 values],
  "z": [512 values]
}
```

- `window_id` increases by one per window; `chunk` runs from 0 to `chunks - 1`.
- `units` can be `"g"` or `"counts"` (raw ADXL355 values, converted using `range_g`).
- Each chunk is about 15 KB as JSON.

The current single-reading packets (`x_g`, `y_g`, `z_g`) are rejected by the assembler on purpose: they can't produce a spectrum.

## Reassembling and analysing windows

`WindowAssembler` collects chunks and returns a complete window once the last one arrives. It copes with chunks arriving out of order, several machines sending at once, and duplicate chunks. Windows missing a chunk for more than 5 seconds are dropped.

```python
from processing import WindowAssembler, analyze_window, PacketError

assembler = WindowAssembler()

def on_vibration_packet(packet: dict):          # call this from the MQTT subscriber
    try:
        window = assembler.add(packet)
    except PacketError as err:
        print(f"bad packet: {err}")
        return
    if window is not None:                       # all chunks arrived
        result = analyze_window(window, rpm=3000)
        x = result["axes"]["x"]
        print(x["dominant_freq_hz"], x["velocity_rms_mm_s"], result["warnings"])
```

`analyze_window` returns, for each axis: dominant frequency and amplitude, velocity RMS, energy per frequency band, and (if `rpm` is given) the 1×/2×/3× harmonics. It also flags problems in `warnings`, such as a window of the wrong length or readings hitting the sensor's limit.

### Try it without hardware

```powershell
python -m simulation.packet_simulator
```

This simulates four machines (healthy, imbalance, misalignment, bearing damage), splits each into packets, shuffles them to mimic network delivery, then reassembles and analyses them:

```
window 0 (healthy     ) 1x=0.050 g  2x=0.010 g  500-1000 Hz=2.3e-05 g²  velocity=1.12 mm/s
window 3 (bearing     ) 1x=0.050 g  2x=0.010 g  500-1000 Hz=1.6e-02 g²  velocity=1.21 mm/s
window 2 (misalignment) 1x=0.080 g  2x=0.150 g  500-1000 Hz=2.4e-05 g²  velocity=2.43 mm/s
window 1 (imbalance   ) 1x=0.300 g  2x=0.020 g  500-1000 Hz=2.5e-05 g²  velocity=6.65 mm/s
```

Each fault stands out in a different number: imbalance in 1×, misalignment in 2×, and bearing damage in the 500–1000 Hz band. Bearing damage barely changes the overall velocity, which is why the frequency breakdown matters.

## Temperature

The thermistor packets already coming in report a divider voltage. `temperatures_from_packet` converts every thermistor in a packet to °C:

```python
from processing import temperatures_from_packet

temperatures_from_packet(packet)   # {"sensor_0": 26.87}
```

A reading of 0 mV or the full supply voltage means a wiring fault (short or open circuit) and comes back as `None`.

**The defaults are assumptions and must be checked against the real circuit:** 10 kΩ NTC thermistor with Beta 3950, a 10 kΩ fixed resistor, 3.3 V supply, thermistor between the ADC pin and ground. Change them with `ThermistorConfig`:

```python
from processing import ThermistorConfig, temperatures_from_packet

cfg = ThermistorConfig(beta=3435, ntc_on_low_side=False)
temperatures_from_packet(packet, cfg)
```

A wrong Beta value or divider orientation still gives a believable-looking temperature, so it's worth verifying once against a reference thermometer.

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
- **Vibration is tested on simulated data only.** Once the ADXL355 arrives: update the ESP32 firmware to send chunked packets, connect `WindowAssembler` to the MQTT subscriber, and record healthy baselines from each machine.
- **Thermistor settings are assumed** until checked against the circuit and datasheet.
- Feature extraction (RMS, kurtosis, crest factor), storage, and anomaly detection are planned but not part of this module yet.
