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
- If you converted the thermistor to °C, add `"temperature_c": 31.2` to that sensor.
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
| `200 {"ok": true, "stored": 1, "duplicates": 0}` | Saved. `duplicates` counts packets skipped because their `packet_id` was already stored | — |
| `400` | Not valid JSON, or a field is wrong; `details` names the field | Fix that field |
| `401` | Missing or wrong `x-device-key` | Check the header and the key |
| `500` | Server problem | Tell Manraj |

## Reading data back (dashboard, debugging)

- `GET {CLOUD_URL}/api/v1/machines`: every machine with its latest packet and window
- `GET {CLOUD_URL}/api/v1/machines/MG-001/readings?minutes=10`: recent history, oldest first
- `GET {CLOUD_URL}/api/health`: `{"ok": true}` if the service is up

No key needed for reading (for now).
