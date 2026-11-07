# SmolBugs: FFT Processing

SmolBugs is a predictive-maintenance system that listens to industrial machines (band saws and power drills) through a vibration sensor and flags abnormal behaviour before it turns into a breakdown.

This folder holds the **processing stage**: it takes raw vibration readings and works out *which frequencies* the machine is vibrating at, and how strongly, and converts the thermistor reading to a temperature. Everything later in the system (anomaly detection, maintenance recommendations, the dashboard) is built on top of this.

```
ESP32 (machine1)  →  Bluetooth  →  [ receiver → reassemble → FFT analysis ]  →  ML anomaly detection  →  dashboard
                                     ▲ you are here
```

**Current status:** the accelerometer (ADXL355) hasn't arrived yet, so the firmware sends a generated test vibration signal over Bluetooth instead. Everything else runs for real: the thermistor readings, the Bluetooth link, decoding, and FFT analysis.

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
│   ├── ble_protocol.py     Decodes the binary Bluetooth vibration messages
│   ├── assembler.py        Rebuilds full windows from chunks
│   ├── analysis.py         Runs a full window through the FFT, returns results
│   ├── temperature.py      Thermistor voltage → °C
│   └── __init__.py         Lets you `from processing import ...`
├── ingest/
│   ├── ble_receiver.py     Connects to the board over Bluetooth, runs everything
│   └── fft_report.py       Interactive FFT report from a recorded session
├── simulation/
│   ├── synthetic.py        Fake vibration signals with known answers
│   ├── packet_simulator.py Fake healthy/faulty machine windows
│   ├── fake_ble_board.py   Pretend Bluetooth board, for running without hardware
│   └── fft_demo.py         Interactive demo: healthy vs faulty machine
├── tests/                  126 automated tests
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
python -m pip install numpy scipy plotly pytest bleak
```

If the `.venv` folder doesn't exist yet, create it from `E:\SmolBugs` with `python -m venv .venv`.

## Run the tests

```powershell
python -m pytest -q
```

Expected result: `119 passed, 7 skipped` on Windows. The 7 skipped tests compile the ESP32 firmware on the PC and only run where a C++ compiler (`g++`) is installed; with one, all 126 run.

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

## Running the receiver

The receiver connects to the board (Bluetooth name `MachineGuard`), prints every temperature reading and every vibration window's FFT results as they arrive, and can save everything to a folder.

```powershell
python -m ingest.ble_receiver                          # real board
python -m ingest.ble_receiver --rpm 3000               # also show 1x/2x/3x harmonics
python -m ingest.ble_receiver --out data/session1      # save readings and raw windows
python -m ingest.ble_receiver --simulate               # fake board, no hardware needed
```

Example output:

```
Connected. MTU: 247
Temp  #   12 | 1582 mV |   69.06 kΩ |  33.57 °C
Vib   window     0 | dominant   50.00 Hz @ 0.050 g | velocity 1.12 mm/s | 1x=0.050 2x=0.010 3x=0.001
```

With `--out`, the folder gets `temperature.jsonl` and `vibration.jsonl` (one result per line) and a `windows/` folder of raw data (`.npz`), which will become training data for anomaly detection. Press Ctrl+C to stop; a summary of anything lost or rejected is printed at the end.

## Viewing the FFT from the board

Record with `--out`, then build an interactive report from what was saved:

```powershell
python -m ingest.ble_receiver --rpm 3000 --out data/session1      # Ctrl+C when done
python -m ingest.fft_report data/session1 --rpm 3000
start data/session1/fft_report.html
```

Or keep the report updating live while recording. Open it once and it reloads itself every 5 seconds:

```powershell
python -m ingest.ble_receiver --rpm 3000 --out data/session1 --live-html
start data/session1/fft_report.html          # in a second terminal
```

The report shows the latest window (or `--window N` for a specific one):

1. **Raw vibration:** the first 0.2 s of each axis, with gravity removed.
2. **FFT amplitude spectrum:** with dotted 1×/2×/3× running-speed lines when `--rpm` is given.
3. **Welch PSD:** the smoothed energy spectrum, on a log scale.
4. **Velocity RMS per window:** how overall vibration changes across the session.
5. **Temperature:** every reading in the session.

Keep `plotly.min.js` next to `fft_report.html` in the session folder; the report needs it to draw the charts.

## How vibration travels over Bluetooth

The FFT needs 4,096 readings taken at a steady 4,000 per second, which is about 24 KB of data. Streaming that continuously is more than a Bluetooth link to a laptop can reliably carry, so the board works in **capture-then-send** cycles:

1. Record one window (4,096 readings, about 1 second) into memory.
2. Send it as 1 START message followed by 128 DATA messages of 32 readings each (about 1.3 seconds).
3. Wait, then repeat every 10 seconds.

Messages are raw bytes rather than text, which is three to four times more compact:

```
START (17 bytes, once per window):
  type=1 | window_id | sample rate | number of readings | readings per message | range | scale | start time

DATA (197 bytes):
  type=2 | window_id | message number (0–127) | 32 × (x, y, z) as 2-byte integers
```

Each reading is stored as a whole number of 1/16,000 g (62.5 µg), far finer than machinery vibration needs. The full byte layout is documented at the top of `processing/ble_protocol.py` and in `smolbugs_protocol.h` in the firmware folder. Tests check that both sides produce identical bytes.

**Bluetooth MTU:** one 197-byte message needs the link's MTU (maximum message size) to be at least 200. The default is only 23, so the board requests 247 and Windows normally agrees. The receiver prints the MTU when it connects. If it's too small, the board skips vibration rather than sending cut-off messages, and the receiver prints a warning.

**Lost messages:** if a message goes missing, that window is dropped after 10 seconds and the next window arrives normally. The summary at the end shows how many were lost. If it's more than a few, increase `VIB_FRAME_GAP_MS` in the firmware.

## Firmware (machine1)

`hardware scripts/machine1/machine1/` contains `machine1.ino` and `smolbugs_protocol.h`, which must stay in the same folder. On top of the existing thermistor, OLED and Bluetooth code, it adds:

- A second Bluetooth characteristic (`6E400004-…`) for vibration. Temperature keeps its original one (`6E400003-…`) and message format.
- Capture-then-send, every `VIB_INTERVAL_MS` (10 s), only while a receiver is subscribed.
- `USE_TEST_SIGNAL 1`: generates a 3,000 RPM machine signal (0.05 g at 50 Hz, 0.01 g at 100 Hz, noise, 1 g gravity on z) until the ADXL355 arrives. When it does, set this to `0` and fill in `captureWindow()`.
- The window count on the OLED, and send time and error counts on the serial monitor.

Settings near the top of the sketch:

| Setting | Default | Meaning |
|---|---|---|
| `VIB_INTERVAL_MS` | 10000 | Time between windows |
| `VIB_FRAME_GAP_MS` | 10 | Pause between messages; lower is faster but risks drops |
| `VIB_SAMPLE_RATE_HZ` | 4000 | Replace with the sensor's measured rate once it's fitted |
| `VIB_RANGE_G` | 2 | Accelerometer range (2, 4 or 8 g) |

## Temperature

The board sends `sequence,voltage_mV,resistance_ohms` twice a second. The receiver converts the voltage to °C with the Beta equation for an NTC thermistor.

The circuit is 3.3 V → 75 kΩ fixed resistor → ADC pin (GPIO1) → thermistor → ground, taken from `machine1.ino`. **The thermistor's own values are still assumptions:** 100 kΩ at 25 °C with Beta 3950. A 10 kΩ part can't explain the ~69 kΩ being measured at room temperature, but the exact part should be confirmed from its datasheet. Change the values with `ThermistorConfig`:

```python
from processing import ThermistorConfig, parse_thermistor_message

cfg = ThermistorConfig(r0_ohm=100_000, beta=4250)
parse_thermistor_message("12,1582,69062", cfg)   # {'sequence': 12, ..., 'temperature_c': ...}
```

The receiver also understands the JSON status messages sent by earlier firmware (`{"machine_id": ..., "sensors": [...]}`, newline-terminated and split across Bluetooth messages), so temperature works with either firmware. Vibration needs the updated `machine1.ino`.

A wrong Beta value still gives a believable-looking temperature, so it's worth checking once against a regular thermometer.

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
- **Vibration uses a test signal until the ADXL355 arrives.** Then: write the sensor driver in `captureWindow()`, measure its real sample rate, and record healthy baselines from each machine.
- **Firmware testing on the PC uses stand-ins for the Arduino and Bluetooth libraries.** The real check is uploading to the board and running the receiver.
- **Thermistor values are assumed** until confirmed from the part's datasheet.
- Feature extraction (RMS, kurtosis, crest factor), storage, and anomaly detection are planned but not part of this module yet.
