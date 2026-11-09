# SmolBugs: FFT Processing

SmolBugs is a predictive-maintenance system that listens to industrial machines (band saws and power drills) through a vibration sensor and flags abnormal behaviour before it turns into a breakdown.

This folder holds the **processing stage**: it takes raw vibration readings and works out *which frequencies* the machine is vibrating at, and how strongly, and converts the thermistor reading to a temperature. Everything later in the system (anomaly detection, maintenance recommendations, the dashboard) is built on top of this.

```
ESP32 (machine1)  →  Bluetooth  →  [ receiver → reassemble → FFT analysis ]  →  ML anomaly detection  →  dashboard
                                     ▲ you are here
```
<img width="1494" height="881" alt="Screenshot 2026-11-08 223954" src="https://github.com/user-attachments/assets/b64a65cb-f4fc-4784-828e-4a6571aa1e3e" />


<img width="1466" height="631" alt="Screenshot 2026-11-08 233034" src="https://github.com/user-attachments/assets/437e0632-e7f4-421b-9093-80a8f97b9155" />


**Current status:** running on real hardware. A Pololu MinIMU-9 (LSM303D accelerometer) samples vibration at about 1,600 Hz, and a thermistor measures temperature. Both travel over Bluetooth to the receiver, which runs the FFT analysis.

## Why an FFT?

The accelerometer records how the machine shakes over time, about 1,600 times per second. As a raw waveform that's hard to interpret: everything is mixed together.

A **Fast Fourier Transform (FFT)** separates that waveform into its individual frequencies, the way a prism splits light into colours. Different mechanical problems show up at different frequencies:

| What the spectrum shows | What it often means |
|---|---|
| Big peak at **1× running speed** | Imbalance (e.g. a worn or unevenly loaded blade/wheel) |
| Strong peak at **2× running speed** | Misalignment or looseness |
| Raised energy at **high frequencies** (120–360 Hz with the LSM303D) | Impacts, often early bearing or gearbox damage |
| A **new peak** that wasn't there before | Something in a rotating component has changed |

Running speed in Hz is simply `RPM / 60`, so a motor at 3,000 RPM has its 1× peak at 50 Hz.

These are *possible* causes to investigate, not diagnoses. The ML stage (`ml/`, see `ml/README.md`) learns what each fault actually looks like on our drill from real recordings.

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
│   ├── cloud.py            Reading IDs and background upload to the SmolBugs cloud
│   └── fft_report.py       Interactive FFT report from a recorded session
├── simulation/
│   ├── synthetic.py        Fake vibration signals with known answers
│   ├── packet_simulator.py Fake healthy/faulty machine windows
│   ├── fake_ble_board.py   Pretend Bluetooth board, for running without hardware
│   └── fft_demo.py         Interactive demo: healthy vs faulty machine
├── ml/                     Features, training and the live detector (see ml/README.md)
├── tests/                  Automated tests
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

# 2. Install dependencies (first time only; pandas, scikit-learn, joblib and matplotlib are for ml/)
python -m pip install numpy scipy pandas scikit-learn joblib plotly matplotlib bleak pytest
```

If the `.venv` folder doesn't exist yet, create it from `E:\SmolBugs` with `python -m venv .venv`. A virtual environment only works on the computer that created it: if activating it gives `No Python at '...'`, it was made on another computer, so delete `.venv` and create it again.

## Run the tests

```powershell
python -m pytest -q
```

Everything should pass. Some tests compile the ESP32 firmware on the PC and are skipped unless a C++ compiler (`g++`) is installed. `tests/test_ml.py` tests an old serial recorder that no longer exists and fails to import; run `python -m pytest -q --ignore=tests/test_ml.py` until it is removed.

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

Run it from the `predictive-maintenance` folder with the virtual environment active; from anywhere else Python reports `No module named 'ingest'`.

The receiver connects to the board (Bluetooth name `MachineGuard`), prints every temperature reading and every vibration window's FFT results as they arrive, and can save everything to a folder.

```powershell
python -m ingest.ble_receiver                          # real board
python -m ingest.ble_receiver --rpm 3000               # also show 1x/2x/3x harmonics
python -m ingest.ble_receiver --out data/session1      # save readings and raw windows
python -m ingest.ble_receiver --simulate               # fake board, no hardware needed
```

Example output from the real board, sitting still:

```
Connected. MTU: 247
Temp  #   61 | 1337 mV |   51.08 kΩ |  40.92 °C | accel -0.366 -0.149 -0.819 g (total 0.91)
Vib   window     4 | dominant    4.86 Hz @ 0.007 g | velocity 0.44 mm/s
```

At rest the dominant peak is just sensor noise (a few thousandths of a g), and the total acceleration should be about 1 g (this sensor reads about 0.9 g). If every axis reads ±4 g, the sensor wiring is loose or the sensor is in a bad state: reseat SDA (GPIO2), SCL (GPIO4), VIN and GND and power-cycle the board.

With `--out`, the folder gets `temperature.jsonl` and `vibration.jsonl` (one result per line) and a `windows/` folder of raw data (`.npz`), which will become training data for anomaly detection. Press Ctrl+C to stop; a summary of anything lost or rejected is printed at the end.

## Sending data to the cloud

Add `--cloud` to send every status reading and analysed window to the SmolBugs cloud as well. The address defaults to the team's AWS deployment. Save the device key once per laptop, from the repo root (`E:\SmolBugs`):

```powershell
Set-Content smolbugs.env "DEVICE_KEY=paste-the-key-here"
```

The file must contain `DEVICE_KEY=` in front of the key; a bare key is ignored. `.gitignore` keeps `smolbugs.env` out of git. The `DEVICE_KEY` and `CLOUD_URL` environment variables override the file. Never paste the key into chats or commits.

```powershell
python -m ingest.ble_receiver --cloud
python -m ingest.ble_receiver --cloud --simulate    # try it without the board
```

At the start it prints `Cloud upload: https://…on.aws`; the summary at the end should show `waiting 0` and `rejected (400) 0`.

Status readings go to `POST /api/v1/status` and windows (the `analyze_window` results, plus the latest `temperature_c`) to `POST /api/v1/windows`, with the key in the `x-device-key` header.

**Reading IDs.** The board's `timestamp_ms` is time since power-on: it restarts at 0 on every reboot, and two machines can have the same value. So every reading gets three extra fields:

| Field | Where it comes from |
|---|---|
| `boot_id` | The firmware's `boot_id` if it sends one. Otherwise the receiver makes one (8 hex characters) when it connects, and a new one whenever that machine's clock goes backwards, which means the board rebooted. |
| `seq` | The firmware's `seq`/`sequence` if present, and `window_id` for windows. Otherwise a counter per machine and boot, starting at 0. |
| `packet_id` | `sha256("machine_id|boot_id|kind|seq")`, first 32 characters, where kind is `status` or `window`. |

Sensor values are deliberately not part of the ID: two real readings can have identical values, and a retried upload must keep the same ID so the cloud can recognise duplicates. `timestamp_ms` and `received_at_utc` are kept as they were. The same fields are written to the `--out` files.

**When the internet drops.** Uploading runs in the background, so Bluetooth handling never waits on the network. Packets are sent in batches of up to 50 at least once a second.
- **Failed upload** (timeout, server error, no connection): the packets are kept and retried with growing pauses (1 s, 2 s, 4 s … up to 60 s), with the same `packet_id`s.
- **Backlog limit:** about 10 minutes of data (2,000 packets). Beyond that, the oldest are dropped and the number dropped is printed.
- **400 Bad Request:** the server's `details` are printed and that packet is dropped, since retrying won't fix it. The rest of its batch is still sent.
- **401 Unauthorized:** the key is wrong. A clear message is printed and packets are kept. Fix `DEVICE_KEY` and restart.
- On Ctrl+C the receiver tries for a few seconds to upload what's left, then prints a summary.

## Viewing the FFT from the board

Build a report for any saved folder, including the training runs recorded by `ml.record`:

```powershell
python -m ingest.fft_report data\raw\S1_loose_bit_01
start data\raw\S1_loose_bit_01\fft_report.html
```

Or watch it live. Save to a folder outside `data\raw` (such as `data\live`) so this data never mixes with training data:

```powershell
python -m ingest.ble_receiver --out data\live --live-html          # terminal 1, keep it running
start data\live\fft_report.html                                  # terminal 2, after the first "Vib window" line
```

The page only exists once the first window has arrived (10–20 s), and it reloads itself every 5 seconds. It needs its own terminal because the receiver keeps the first one busy until Ctrl+C. Add `--cloud` to upload at the same time.

The report shows the latest window (or `--window N` for a specific one):

1. **Raw vibration:** the first 0.2 s of each axis, with gravity removed.
2. **FFT amplitude spectrum:** with dotted 1×/2×/3× running-speed lines when `--rpm` is given (the drill's RPM). The axis goes up to 800 Hz, but above about 360 Hz the sensor's filter removes almost everything.
3. **Welch PSD:** the smoothed energy spectrum, on a log scale.
4. **Velocity RMS per window:** how overall vibration changes across the session.
5. **Temperature:** every reading in the session.

Keep `plotly.min.js` next to `fft_report.html` in the session folder; the report needs it to draw the charts.

## How vibration travels over Bluetooth

The FFT needs 4,096 readings taken at a steady rate, which is about 24 KB of data. Streaming that continuously is more than a Bluetooth link to a laptop can reliably carry, so the board works in **capture-then-send** cycles:

1. Record one window (4,096 readings at about 1,600 per second, so 2.56 seconds) into memory.
2. Send it as 1 START message followed by 128 DATA messages of 32 readings each (about 1.3 seconds).
3. Wait, then repeat every 10 seconds.

Messages are raw bytes rather than text, which is three to four times more compact:

```
START (17 bytes, once per window):
  type=1 | window_id | sample rate | number of readings | readings per message | range | scale | start time

DATA (197 bytes):
  type=2 | window_id | message number (0–127) | 32 × (x, y, z) as 2-byte integers
```

Each reading is the LSM303D's own 16-bit value (0.122 mg per step at ±4 g). The START message carries the scale, range and the measured sample rate, so the receiver converts correctly. The full byte layout is documented at the top of `processing/ble_protocol.py` and in `smolbugs_protocol.h` in the firmware folder. Tests check that both sides produce identical bytes.

**Bluetooth MTU:** one 197-byte message needs the link's MTU (maximum message size) to be at least 200. The default is only 23, so the board requests 247 and Windows normally agrees. The receiver prints the MTU when it connects. If it's too small, the board skips vibration rather than sending cut-off messages, and the receiver prints a warning.

**Lost messages:** if a message goes missing, that window is dropped after 10 seconds and the next window arrives normally. The summary at the end shows how many were lost. If it's more than a few, increase `VIB_FRAME_GAP_MS` in the firmware.

## Firmware (machine1)

`hardware scripts/machine1/machine1/` contains `machine1.ino` and `smolbugs_protocol.h`, which must stay in the same folder. The board is a Heltec ESP32 with a Pololu MinIMU-9 v3 (LSM303D accelerometer) on its second I2C bus (`Wire1`, SDA GPIO2, SCL GPIO4). The OLED uses the first bus (`Wire`, GPIO17/18); starting `Wire` again on other pins is silently ignored by the ESP32, which is why the accelerometer needs `Wire1`.

What the firmware does:

- **Status message every 500 ms** on characteristic `6E400003-…`: `sequence,voltage_mV,x_g,y_g,z_g` (thermistor voltage plus a single accelerometer reading).
- **A vibration window every 10 s** on characteristic `6E400004-…`, only while a receiver is subscribed. The LSM303D runs at 1,600 Hz with its 362 Hz anti-alias filter, and readings are collected through its 32-reading FIFO buffer. That keeps their spacing exact even if the ESP32 is briefly busy. If it's busy for more than about 20 ms the buffer overflows; that window is discarded rather than sent with a gap, and the next one is captured normally.
- **The sensor's real sample rate is measured** for every window and sent with it, so frequencies stay accurate even though the LSM303D's clock can be a few percent off.
- The window count on the OLED; send time, rate and error counts on the serial monitor.

What the 1,600 Hz rate means for analysis: frequency resolution is 0.39 Hz, and vibration up to about **360 Hz** is captured accurately (the anti-alias filter). Above that, readings are increasingly filtered out, so the 500–1000 Hz band in the results will stay near zero.

Settings near the top of the sketch:

| Setting | Default | Meaning |
|---|---|---|
| `VIB_INTERVAL_MS` | 10000 | Time between windows |
| `VIB_FRAME_GAP_MS` | 10 | Pause between messages; lower is faster but risks drops |
| `VIB_WINDOW_SAMPLES` | 4096 | Readings per window (2.56 s at 1,600 Hz) |
| `VIB_RANGE_G` / `VIB_LSB_PER_G` | 4 / 8197 | Must match CTRL2's full-scale setting |

## Temperature

The board sends `sequence,voltage_mV,x_g,y_g,z_g` twice a second (older firmware sent `sequence,voltage_mV,resistance_ohms`; both are understood). The receiver converts the voltage to °C with the Beta equation for an NTC thermistor.

**The reading is not trusted yet.** It shows about 41 °C at room temperature. GPIO1 is also the Heltec WiFi LoRa 32 V3's battery-voltage pin, which most likely explains it. Move the thermistor to a free ADC1 pin and check it against a thermometer before using temperature for anything.

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

All in `FFTConfig` (`processing/config.py`). **The defaults were written for the ADXL355 at 4,000 Hz**, and the receiver's live printout and `fft_report` still use them. They analyse each window at its own measured sample rate (about 1,600 Hz), so frequencies are right, but the bands above 360 Hz stay near zero with the LSM303D. The ML stage uses `DRILL_FFT` in `ml/settings.py` instead: 1,600 Hz, 1,024-sample windows, bands 2–40, 40–120 and 120–360 Hz.

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
- **The receiver and report use the generic ADXL355 defaults** (see Settings). Switching them to the drill's 1,600 Hz settings would make the printed bands match what the sensor can see.
- **The drill's running speed is not confirmed.** The ML stage estimates about 110 Hz (6,600 RPM), which is high for a drill; it may be locking onto the motor or gears. Check it against the drill's rated RPM.
- **Firmware testing on the PC uses stand-ins for the Arduino and Bluetooth libraries.** The real check is uploading to the board and running the receiver.
- **Thermistor values are assumed** until confirmed from the part's datasheet.
- Feature extraction, training and anomaly detection now live in `ml/` (see `ml/README.md`). The trained model does not run on live board data yet; `ml.live_check` replays recordings through it.
