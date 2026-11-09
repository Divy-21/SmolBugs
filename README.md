# MachineGuard by SmolBugs

**A clip-on monitor that listens to a machine's vibration and temperature, tells you when something is wrong, and names the likely fault before the machine breaks.**

natHacks 2026, Standard Division, Construction and Manufacturing theme.

## The problem

Small shops run drills, saws and lathes until they fail. In Canada, 19% of businesses still run machines to failure, and the average unplanned outage costs about $242,000 an hour (ABB "Value of Reliability" survey, 2023). Large plants pay for condition monitoring. Small shops and makerspaces cannot, so they find out when the tool stops, the part is ruined or someone gets hurt.

## What MachineGuard does

1. A sensor node clips onto the machine and measures vibration (about 1,600 samples per second) and temperature.
2. It sends full vibration windows over Bluetooth to a gateway laptop.
3. The gateway runs an FFT on each window, and a machine learning model compares it to that machine's healthy baseline. It flags abnormal behaviour, names the likely fault (for example imbalance, a bent bit or a loose bit) and gives a recommended fix.
4. Everything is stored in the cloud and shown on a live dashboard: each machine is online, idle, offline or broken, with temperature and vibration trends, an event history and phone alerts.

```text
Heltec ESP32 + LSM303D accelerometer + thermistor (on the machine)
        │ Bluetooth Low Energy: status every 0.5 s, a 4,096-sample vibration window every 10 s
        ▼
Python receiver ──► reassemble vibration window ──► FFT metrics ──► ML detector
        │                                              │
        ├── save local session                         └──► cloud API (Next.js)
        └── upload status and window results                  │
                                                              ▼
                                                PostgreSQL ──► dashboard + phone alerts
```

The machine1 firmware sends thermistor status readings about twice per second and vibration windows in compact binary BLE packets. The receiver reconstructs each window, calculates frequency and severity metrics, and can save the session or queue uploads to the cloud. The cloud stores status packets and analysed windows, raises alerts, and serves the dashboard.

## Links

| What | Where |
|---|---|
| Slides | `docs/MachineGuard_Pitch.pdf` |
| Next version concept renders (3D) | `docs/renders/` |
| Repository | https://github.com/Divy-21/SmolBugs |

## Hardware requirements

| Part | Notes |
|---|---|
| Heltec WiFi LoRa 32 V3 (ESP32-S3) | Built-in 0.96 in OLED on the first I2C bus (`Wire`, GPIO17/18) |
| Pololu MinIMU-9 v3 | LSM303D accelerometer on the second I2C bus (`Wire1`, SDA GPIO2, SCL GPIO4). Starting `Wire` again on other pins is silently ignored by the ESP32, which is why it needs `Wire1`. |
| NTC thermistor | 3.3 V → 75 kΩ fixed resistor → GPIO1 → thermistor → ground |
| USB cable or LiPo battery | Power for the board |
| Laptop with Bluetooth | Runs the Python receiver (gateway) |

## Repository layout

| Path | Contents |
|---|---|
| [`hardware scripts/machine1/`](hardware%20scripts/machine1/) | ESP32 machine1 firmware and shared BLE packet protocol header |
| [`predictive-maintenance/processing/`](predictive-maintenance/processing/) | BLE protocol decoding, window assembly, FFT analysis, and thermistor conversion |
| [`predictive-maintenance/ingest/`](predictive-maintenance/ingest/) | BLE receiver, session recording, FFT report, and cloud uploader |
| [`predictive-maintenance/simulation/`](predictive-maintenance/simulation/) | Synthetic vibration, BLE device, and packet simulators |
| [`predictive-maintenance/ml/`](predictive-maintenance/ml/) | MachineGuard recording, training, live check, and bearing wear tracking |
| [`predictive-maintenance/tests/`](predictive-maintenance/tests/) | Python and firmware protocol tests |
| [`cloud/`](cloud/) | Next.js API service, Prisma/PostgreSQL schema, and dashboard |
| [`docs/sending-data.md`](docs/sending-data.md) | Cloud packet formats and sender examples |

## 1. Flash the firmware

`hardware scripts/machine1/machine1/` contains `machine1.ino` and `smolbugs_protocol.h`, which must stay in the same folder.

1. Open `machine1.ino` in the Arduino IDE.
2. Select the **Heltec WiFi LoRa 32 V3** board and its port.
3. Upload. The board advertises over Bluetooth as `MachineGuard`.

Settings near the top of the sketch:

| Setting | Default | Meaning |
|---|---|---|
| `VIB_INTERVAL_MS` | 10000 | Time between vibration windows |
| `VIB_FRAME_GAP_MS` | 10 | Pause between messages; lower is faster but risks drops |
| `VIB_WINDOW_SAMPLES` | 4096 | Readings per window (2.56 s at 1,600 Hz) |
| `VIB_RANGE_G` / `VIB_LSB_PER_G` | 4 / 8197 | Must match the LSM303D full-scale setting |

## 2. Set up the Python gateway

From `predictive-maintenance/`, create and activate a virtual environment, then install the dependencies:

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
# (if you get "running scripts is disabled", run: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass)
python -m pip install numpy scipy plotly pytest bleak scikit-learn joblib
```

### Run the receiver

```bash
python -m ingest.ble_receiver --simulate                 # fake board, no hardware needed
python -m ingest.ble_receiver                            # real board
python -m ingest.ble_receiver --rpm 1200                 # also show 1x/2x/3x harmonics (drill at 1200 rpm)
python -m ingest.ble_receiver --rpm 1200 --out data/session1   # save readings and raw windows
python -m ingest.ble_receiver --duration 60              # run for 60 s, then stop
```

Example output:

```
Connected. MTU: 247
Temp  #   12 | 1582 mV |   69.06 kΩ |  33.57 °C
Vib   window     0 | dominant   20.00 Hz @ 0.050 g | velocity 1.12 mm/s | 1x=0.050 2x=0.010 3x=0.001
```

With `--out`, the folder gets `temperature.jsonl`, `vibration.jsonl` and a `windows/` folder of raw data (`.npz`). Press Ctrl+C to stop; a summary of anything lost or rejected is printed at the end.

### View the FFT

```bash
python -m ingest.fft_report data/session1 --rpm 1200
start data/session1/fft_report.html          # Windows; use "open" on macOS

# or keep the report updating live while recording:
python -m ingest.ble_receiver --rpm 1200 --out data/session1 --live-html
```

The report shows the raw vibration, the FFT amplitude spectrum with 1×/2×/3× running-speed lines, the Welch PSD, velocity RMS per window and temperature. Keep `plotly.min.js` next to `fft_report.html`.

To see a healthy machine and a faulty machine side by side without hardware:

```bash
python -m simulation.fft_demo
start smolbugs_fft_demo.html
```

### Send data to the cloud

Add `--cloud` to send every status reading and analysed window to the SmolBugs cloud. The address and key come from environment variables, so the key never goes into a file or into git:

```powershell
$env:CLOUD_URL  = "http://localhost:3100"      # or the deployed cloud URL
$env:DEVICE_KEY = "dev-key-change-me"          # the local key; ask the team for the deployed one
python -m ingest.ble_receiver --cloud --rpm 1200
python -m ingest.ble_receiver --cloud --simulate
```

Runs recorded without `--cloud` can be uploaded afterwards:

```bash
python -m ingest.upload_run data/raw
```

Uploads run in the background, retry with growing pauses if the internet drops, and keep about 10 minutes of backlog. Full details are in `predictive-maintenance/processing/README.md`.

## 3. Machine learning

Labels: `off`, `normal_free`, `normal_load`, `imbalance`, `bent_bit`, `loose_bit`, `overheat`.

**Record labelled runs** (one run per command; the run number is picked automatically):

```bash
python -m ml.record --session 1 --label normal_free --seconds 300
python -m ml.record --session 1 --label imbalance --seconds 300
python -m ml.record --session 1 --label normal_free --seconds 300 --cloud   # also uploads while recording
```

A session is one mounting of the sensor. Record `normal_free` first in every session (normal is learned from it), use at least 2 sessions (3 or more is better), and re-clip the sensor between sessions. 300 s gives about 30 Bluetooth windows (210 analysis windows).

**Train and test:**

```bash
python -m ml.train --raw data/raw --out models
python -m ml.live_check --raw data/raw --session 3
```

`train.py` always tests on a session it never trained on, never on a random split of windows, because windows from the same run look almost identical and would inflate the score. Add `--use-temp` once the thermistor is checked against a thermometer.

**How the detector decides** (each window):

1. Main axis much quieter than normal, or the classifier says `off`: **Machine off**.
2. Classifier says normal and the isolation forest agrees: **Running normally**.
3. Classifier names a fault with at least 60% confidence, and the isolation forest or the FFT rules agree: **Fault**, with a recommendation (for example "Tighten the chuck and check that the bit is seated all the way in." for a loose bit).
4. Anything else: **Unusual vibration, type unknown**.

Features are measured relative to each session's own `normal_free` runs, so the model works whether the sensor sits closer to or further from the chuck.

**Bearing wear tracker** (slow faults that build over days), checked on the Ball Bearing Run-to-Failure dataset (Kaggle, Jung et al. 2023), cut down to 1,600 Hz to match our sensor:

```bash
python -m ml.bearing_validation extract --zip "path/to/archive.zip"
python -m ml.bearing_validation analyze
```

The chart is saved to `reports/bearing_wear_warning.png`. Data and model files are generated locally and are not checked into the repository. Full details: [`predictive-maintenance/ml/README.md`](predictive-maintenance/ml/README.md).

## 4. Cloud and dashboard

Requires Node.js 20 or later and Docker Desktop (running). Follow [`cloud/README.md`](cloud/README.md) for full details. In brief:

```bash
cd cloud
npm install                 # also generates the Prisma client
cp .env.example .env        # Windows cmd: copy .env.example .env
npm run db:up               # local Postgres in Docker, on port 5433
npm run db:migrate          # create the tables
npm run dev                 # http://localhost:3100
```

In a second terminal, try it without hardware:

```bash
npm run fake-device         # one simulated machine; press f to toggle a fault
npm run fake-fleet          # six simulated machines in different states
```

`fake-fleet` keys: `1` to `6` fault, `q` to `y` signal loss, `a` to `h` idle, `z` to `n` dead accelerometer. All of this data is simulated.

Look at the stored data with `npm run db:studio`, or `curl localhost:3100/api/v1/machines`.

**Dashboard** (`http://localhost:3100`): every machine is **Online**, **Idle** or **Offline** based on what the database has stored, and machines with an open alert are listed as **Broken** at the top. The selected machine shows temperature and vibration with limit lines, a 10-minute temperature trend, the latest FFT window, the alert history, and a monitoring panel (last reading, sensor health, readings waiting to be stored).

**Alerts:** 3 abnormal packets in a row open an alert and 6 normal ones close it. Set `NTFY_TOPIC` to push alerts to a phone through [ntfy](https://ntfy.sh). Limits are environment variables (`TEMP_MAX_C`, `VIBRATION_STD_MAX_G`, and others), and `MG-001` (the real drill) has its own limits because a running drill shakes far more than an idle machine.

API routes, authentication and packet formats are documented in [`cloud/README.md`](cloud/README.md) and [`docs/sending-data.md`](docs/sending-data.md).

## 5. Tests

```bash
cd predictive-maintenance
python -m pytest -q          # firmware protocol tests are skipped unless g++ is installed

cd cloud
npm test
```

The FFT tests feed in signals with known answers (for example "0.5 g at exactly 123.37 Hz") and check frequencies within 0.01 Hz and amplitudes within 0.5%. Protocol tests check that the firmware and the Python decoder produce identical bytes.

## Results

- Working prototype mounted on a real drill, streaming 1,600 Hz vibration windows and temperature end to end into the cloud dashboard.
- FFT pipeline verified against known test signals.
- Machine learning pipeline (record, train on held-out sessions, live check) running on recorded drill data, with recommendations for each fault.
- Bearing wear tracker on the public run-to-failure dataset: sustained wear warning 86 hours before failure.

## Limitations and next steps

- Thresholds and fault labels come from one drill over one weekend. Each new machine needs its own healthy baseline recording.
- The accelerometer is not calibrated, so vibration is compared to the machine's own baseline rather than an absolute standard.
- The thermistor part values are assumptions until confirmed from its datasheet, so temperature is off in the ML model by default.
- The LSM303D captures vibration accurately up to about 360 Hz, so high-frequency bearing impacts need a faster sensor.
- Next: one small custom board (a hub with a screen and battery, sensors on short cables) that clips onto any machine, then pilots with local shops and makerspaces.

## Credits and sources

- Built by team SmolBugs: Divy, Kelvin, Manraj, Rimsha and Xavion.
- Libraries: Arduino ESP32 core, NumPy, SciPy, scikit-learn, joblib, Plotly, Bleak, pytest, Next.js, React, Prisma, PostgreSQL, node-postgres, zod, Tailwind CSS, tsx, ntfy (phone alerts).
- Statistics: ABB "Value of Reliability" survey (July 2023), Canadian results, via Electrical Business.
- Bearing data: Ball Bearing Run-to-Failure dataset (Kaggle, Jung et al. 2023).
- All code in this repository was written during natHacks 2026 (November 6 to 9, 2026).
