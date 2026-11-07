# MachineGuard Dashboard

One page that shows a shop owner, at a glance: is the drill OK, what is wrong, what to do about it, and the proof (live vibration, spectrum, temperature).

It must work **without the IMU** too: a replay mode feeds fake recordings through the real ML detector, so the dashboard can be built now and is also a backup for the pitch if the hardware acts up.

## How data flows

```
ESP32 (USB serial)  --or--  replay of data/fake/raw/*.csv
          |
          v
dashboard/server  (Python, FastAPI)
  - cuts the stream into 1 s windows (50% overlap, so a new one every 0.5 s)
  - runs ml.detector: status, fault name, recommendation
  - runs the team FFT code for the spectrum chart
  - pushes one JSON message per window over a WebSocket
          |
          v
dashboard/web  (React + Vite + TypeScript, Recharts)
```

Detection only: the system warns and recommends, it does not switch the machine off.

## What is on the screen

1. **Status card (biggest thing on the page)**
   - Machine name ("Drill 1") and a status word with a colour:
     - Learning normal (blue, with a 60 s countdown)
     - Running normally (green)
     - Machine off (grey)
     - Unusual, check it (yellow)
     - Fault (red), with the fault name, for example "Imbalance"
   - When the fault started: "Since 2:14 PM".
   - Confidence, small text, for example "92% sure".

2. **What to do (recommendation box)**
   - The recommendation text from the detector, for example "Tighten the chuck and check that the bit is seated all the way in."
   - Button: **Fixed it**. Clears the fault from the screen and logs it.

3. **Live charts**
   - Vibration level (RMS) over the last 60 s, one line per axis.
   - Frequency spectrum (0 to 260 Hz) of the strongest axis, with dotted lines at 1x, 2x and 3x running speed. This is where judges see the FFT working: the 1x peak jumps for imbalance, 2x for a bent bit.
   - Temperature over the last 10 minutes.

4. **Machine health (wear)**
   - Hours of use and wear status: OK / Bearing wear likely / Critical, with its recommendation.
   - Link or small image: the bearing run-to-failure chart (`predictive-maintenance/reports/bearing_wear_warning.png`) as proof it works on real failure data.

5. **Event log**
   - Table: time, what happened, recommendation. Example: "2:14 PM, Imbalance detected, Check the chuck and bit for anything stuck to them."

6. **Controls**
   - **Learn normal**: records 60 s of normal running and calibrates (use right after clipping the sensor on).
   - Source switch for testing: Live (serial) or Replay (pick a fake recording, for example `S3_imbalance_01.csv`).

## Message format (server to web, over WebSocket `/ws`)

One message per window, about twice a second:

```json
{
  "type": "window",
  "machine_id": "drill-1",
  "time": "2026-11-08T14:14:03.512Z",
  "status": "fault",
  "label": "imbalance",
  "message": "Imbalance: something on the chuck or bit is off-centre",
  "recommendation": "Stop and check the chuck and bit for anything stuck to them or mounted off-centre.",
  "confidence": 0.92,
  "rms_g": {"x": 0.21, "y": 0.15, "z": 0.06},
  "temp_c": 31.4,
  "temp_slope_c_per_min": 0.4,
  "run_hz": 30.1,
  "spectrum": {"axis": "x", "freqs_hz": [0.0, 0.98, 1.95], "amp_g": [0.0, 0.001, 0.002]}
}
```

- `status` is one of `learning`, `ok`, `off`, `check`, `fault`.
- While learning, `status` is `learning` and the message also has `"learning_seconds_left": 42`.
- `spectrum` only goes up to 260 Hz (about 266 points), so messages stay small.

Event messages, sent when something changes (fault starts, fault cleared, fixed, calibrated):

```json
{"type": "event", "time": "2026-11-08T14:14:03Z", "text": "Imbalance detected",
 "recommendation": "Stop and check the chuck and bit for anything stuck to them or mounted off-centre."}
```

## Commands (web to server, HTTP)

| Endpoint | What it does |
|---|---|
| `POST /api/calibrate` | Start 60 s of "learn normal" |
| `POST /api/reset` | "Fixed it": clear the fault from the screen and log it |
| `POST /api/source` | `{"mode": "replay", "file": "S3_imbalance_01.csv"}` or `{"mode": "serial", "port": "COM5"}` |
| `GET /api/recordings` | List of files available for replay |

## Folder layout

```
dashboard/
  server/   FastAPI app, reuses predictive-maintenance/ml and processing
  web/      React + Vite + TypeScript front end
```

## Running it

```powershell
# 1. Python server (from the SmolBugs folder)
python -m pip install numpy scipy pandas scikit-learn joblib pyserial fastapi "uvicorn[standard]"
cd dashboard\server
uvicorn app:app --port 8000

# 2. Web app, in a second terminal
cd dashboard\web
npm install
npm run dev
```

Open http://localhost:5173.

- The first start takes about a minute: if no model exists yet, the server makes fake drill recordings and trains a model on them.
- It starts in **Demo** mode: learns normal for 15 s, then plays normal running, imbalance, bent bit, loose bit, overheating and off, one after another.
- Once real recordings are trained (`python -m ml.train --raw data/raw --out models`), the server uses `predictive-maintenance/models/machineguard.joblib` instead.
- For a single-terminal demo: `npm run build` once, then only run the server and open http://localhost:8000.
