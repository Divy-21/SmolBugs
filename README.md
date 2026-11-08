# SmolBugs

SmolBugs is a prototype for monitoring machine condition. An ESP32 reads temperature and vibration sensors, a Python gateway receives the readings over Bluetooth and analyses vibration windows, and a cloud service accepts readings for storage and inspection.

The repository also contains a separate MachineGuard machine-learning prototype for classifying drill conditions and tracking bearing wear. Its data collection and model workflow is documented separately from the Bluetooth gateway pipeline.

## System overview

```text
Heltec ESP32 + sensors
        │ Bluetooth Low Energy
        ▼
Python receiver ──► reassemble vibration window ──► FFT metrics
        │                                              │
        ├── save local session                         └──► cloud API
        └── upload status and window results                  │
                                                              ▼
                                                       PostgreSQL
```

The machine1 firmware sends thermistor status readings about twice per second and vibration windows in compact binary BLE packets. The receiver reconstructs each window, calculates frequency and severity metrics, and can save the session or queue uploads to the cloud. The cloud stores status packets and analysed windows and exposes API routes for reading them. The FFT output can help identify changes that merit investigation; it is not a diagnosis of a specific mechanical fault.

The current firmware is configured for a Heltec ESP32, a Pololu MinIMU-9 v3 (LSM303D accelerometer), and an NTC thermistor. The vibration path currently has a synthetic test-signal option; check the firmware configuration and processing guide for hardware status and limitations before relying on measurements.

## Repository layout

| Path | Contents |
|---|---|
| [`hardware scripts/machine1/`](hardware%20scripts/machine1/) | ESP32 machine1 firmware and shared BLE packet protocol header |
| [`predictive-maintenance/processing/`](predictive-maintenance/processing/) | BLE protocol decoding, window assembly, FFT analysis, and thermistor conversion |
| [`predictive-maintenance/ingest/`](predictive-maintenance/ingest/) | BLE receiver, session recording, and optional cloud uploader |
| [`predictive-maintenance/simulation/`](predictive-maintenance/simulation/) | Synthetic vibration, BLE device, and packet simulators |
| [`predictive-maintenance/ml/`](predictive-maintenance/ml/) | Separate MachineGuard recording, training, live-check, and wear-tracking tools |
| [`predictive-maintenance/tests/`](predictive-maintenance/tests/) | Python and firmware protocol tests |
| [`cloud/`](cloud/) | Next.js API service and Prisma/PostgreSQL schema |
| [`docs/sending-data.md`](docs/sending-data.md) | Cloud packet formats and sender examples |

## Run the Python receiver

From `predictive-maintenance/`, create and activate a virtual environment, then install the receiver dependencies:

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
python -m pip install numpy scipy plotly pytest bleak
```

Run a simulated receiver session without hardware:

```bash
python -m ingest.ble_receiver --simulate
```

Connect to a BLE board, save a session, or generate an FFT report:

```bash
python -m ingest.ble_receiver --rpm 3000 --out data/session1
python -m ingest.fft_report data/session1 --rpm 3000
```

For receiver options, BLE protocol details, and known measurement limitations, see [`predictive-maintenance/processing/README.md`](predictive-maintenance/processing/README.md). For the independent ML workflow, see [`predictive-maintenance/ml/README.md`](predictive-maintenance/ml/README.md).

## Run the cloud service

The cloud app requires Node.js 20 or later and Docker with Compose. Follow [`cloud/README.md`](cloud/README.md) for environment setup and database migrations. In brief:

```bash
cd cloud
npm install
cp .env.example .env
npm run db:up
npm run db:migrate
npm run dev
```

The local service listens at `http://localhost:3100`. Use `npm run fake-device` in another terminal to send simulated packets. API routes, authentication, and packet schemas are documented in the cloud guide and [`docs/sending-data.md`](docs/sending-data.md).

## Machine-learning prototype

The `ml` package is a separate drill monitoring workflow. It can generate fake recorder-format data, train models, replay sessions through the detector, and evaluate gradual bearing wear. Start with [`predictive-maintenance/ml/README.md`](predictive-maintenance/ml/README.md); its data and model files are generated locally and are not checked into the repository.

## Scope

This is a prototype for demonstrating sensor acquisition, signal processing, anomaly detection experiments, and data transport. Sensor calibration, machine-specific baselines, fault labels, and operating thresholds need validation on the target equipment before operational use.
