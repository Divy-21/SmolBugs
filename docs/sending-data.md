# Sending data to the SmolBugs cloud

For anyone writing code that sends readings (the laptop gateway, a test script, or the ESP32 itself).
The cloud stores everything in Postgres; the dashboard reads it from there.

```
ESP32 --BLE or Wi-Fi--> laptop gateway --HTTPS--> SmolBugs cloud (AWS) --> database --> dashboard
                        (Kelvin's receiver,
                         Xavion's FFT, Rimsha's ML)
```

## What you need

| | |
|---|---|
| **URL** | `CLOUD_URL` = `https://sm-db6bb08ece0d477c9e820f118f7e670c.ecs.ca-central-1.on.aws`. Locally: `http://localhost:3100` |
| **Key** | Send it in the header `x-device-key`. Manraj DMs it; never commit it or post it in the channel. Locally: `dev-key-change-me` |
| **AWS account?** | Not needed. The URL and the key are enough |

## Run the board → cloud (on the laptop next to the board)

From the repo root, one command (Linux/macOS: `python3`):

```
python "hardware scripts/machine1/receiver.py"
```

- **The first time**, it asks for the device key (Manraj DMs it). Paste it; it's hidden while you type. It's saved
  in `smolbugs.env` at the repo root, which git ignores, and you're never asked again on that laptop. The cloud
  address is built in. No environment variables needed (`CLOUD_URL` / `DEVICE_KEY` still win if you set them).
- **Every run is saved** to `data/run-<date>-<time>/` (temperature, FFT results, raw windows). Send that folder if
  something looks wrong. `--out <folder>` picks another place.
- `--simulate` runs a fake board and uploads it as `MG-SIM`, so it never mixes with the real `MG-001`.
- `--no-cloud`: print and save only. Other options pass through: `--rpm 3000`, `--duration 60`, `--machine-id MG-002`.

It starts Xavion's `ingest/ble_receiver.py`, which handles everything the `machine1` firmware sends: the status
line every 0.5 s and the vibration windows every 10 s. From `predictive-maintenance/`,
`python -m ingest.ble_receiver --cloud --out data/session1` runs the same program directly (it reads the same
`smolbugs.env`, but won't ask for the key).

How to tell it's working:
- At the start: `Cloud upload: https://…on.aws`. **If the address isn't the `.on.aws` one, it's going to the wrong
  place.** `Cloud upload OFF` means there's no device key (run again and paste it).
- `Vib   window N | dominant … Hz` lines every ~10 s. If none have come after 25 s, the receiver prints
  `WARNING: no vibration data`. The board decides whether to send, and prints why on its USB cable: open the Arduino
  IDE Serial Monitor at 115200 baud and look for `Vibration skipped: MTU` (Bluetooth packets too small) or
  `Vibration capture: …` (accelerometer problem). Also look in the receiver output for `WARNING: MTU … < 200` or
  `Bad vibration frame`.
- At the end (Ctrl+C): `cloud: sent N | waiting 0 | … | rejected (400) 0`. `rejected` above 0 = a format problem
  (the line before it names the field).
- Check from any browser: `{CLOUD_URL}/api/v1/machines` shows the machine with a fresh `lastSeenAt`.

An all-zero accelerometer reading is treated as a dead sensor: three in a row open an alert. That's expected until
the accelerometer is wired.

## The two endpoints you send to

| Send | To | When |
|---|---|---|
| A **status packet**: thermistor and other slow sensors | `POST {CLOUD_URL}/api/v1/status` | every 0.5–1 s |
| A **vibration window**: the dict `analyze_window()` returns | `POST {CLOUD_URL}/api/v1/windows` | once per reassembled window (~1 s) |

Each takes one packet or a JSON list of up to 100 (batching cuts HTTPS overhead). Raw accelerometer chunks do
**not** go to the cloud: they're ~120 KB/s. The gateway reassembles them and sends only the analysis.

### Status packet

```json
{
  "machine_id": "MG-001",
  "timestamp_ms": 96847,
  "boot_id": "a91f",
  "seq": 1234,
  "sensors": [
    { "id": "sensor_0", "type": "thermistor", "voltage_mv": 1582 }
  ]
}
```

- Required: `machine_id` (letters, digits, `_ . -`) and `sensors` (a list, may be empty).
- Optional but useful: `timestamp_ms` (device uptime), `boot_id` (random per power-up), `seq` (counts up).
- `packet_id`: a unique ID for this reading (8–64 letters, digits, `_` or `-`). The gateway makes it in
  `predictive-maintenance/ingest/cloud.py`. If a packet with the same `packet_id` is already stored, the cloud
  skips it, so retrying a send is always safe. Packets without one are always stored. Windows take it too.
- If you converted the thermistor to °C, add `"temperature_c": 31.2` to that sensor. `null` is fine: the cloud then
  converts `voltage_mv` itself. Accelerometer (`x_g`, `y_g`, `z_g`), gyroscope and `valid` flags are accepted too.
- Status packets are written within about 2 s. Each is checked against limits (temperature, shock, vibration
  spread, dead sensor); 3 abnormal in a row open an alert and are written at once.
- Extra fields are fine; they're stored too.

### Vibration window

Post exactly what `processing.analyze_window(window, rpm=...)` returns. Optionally add `"temperature_c"`.
Example: `cloud/fixtures/windows.json`.

## Try it

```bash
curl -X POST "$CLOUD_URL/api/v1/status" \
  -H "content-type: application/json" -H "x-device-key: $DEVICE_KEY" \
  -d '{"machine_id":"MG-TEST","sensors":[{"id":"sensor_0","type":"thermistor","voltage_mv":1582}]}'
```

Then check it arrived: `curl "$CLOUD_URL/api/v1/machines"`.

## From Python (gateway)

```python
import json, os, urllib.request

CLOUD_URL = os.environ.get("CLOUD_URL", "http://localhost:3100")
DEVICE_KEY = os.environ["DEVICE_KEY"]  # set it in your shell, don't hard-code it

def post_to_cloud(path, packet):
    req = urllib.request.Request(
        CLOUD_URL + path,
        data=json.dumps(packet, allow_nan=False).encode(),  # NaN isn't valid JSON
        headers={"content-type": "application/json", "x-device-key": DEVICE_KEY},
    )
    with urllib.request.urlopen(req, timeout=5) as res:
        return json.load(res)

post_to_cloud("/api/v1/status", status_packet)
post_to_cloud("/api/v1/windows", analyze_window(window, rpm=3000))
```

Don't let a failed POST crash the receiver: wrap it in `try/except`, and keep reading from the ESP32.

## What the answers mean

| Status | Meaning | Fix |
|---|---|---|
| `200 {"ok": true, "stored": 1, "duplicates": 0}` | Saved (status: held, written within ~2 s). `duplicates` counts packets skipped because their `packet_id` was already stored. Status answers also carry `abnormal` and `flags` (limits each packet broke) | — |
| `503` (status only) | An abnormal reading couldn't be written yet | Retry with the same `packet_id` (Xavion's sender does) |
| `400` | Not valid JSON, or a field is wrong; `details` names the field | Fix that field |
| `401` | Missing or wrong `x-device-key` | Check the header and the key |
| `500` | Server problem | Tell Manraj |

## Reading data back (dashboard, debugging)

- `GET {CLOUD_URL}/api/v1/machines`: every machine with its latest packet and window
- `GET {CLOUD_URL}/api/v1/machines/MG-001/readings?minutes=10`: recent history, oldest first
- `GET {CLOUD_URL}/api/health`: `{"ok": true}` if the service is up
- `GET {CLOUD_URL}/api/v1/status`: what the status endpoint is holding in memory right now

No key needed for reading (for now).
