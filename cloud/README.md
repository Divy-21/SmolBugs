# SmolBugs cloud

The part that runs on AWS: it receives packets from the gateway (or a device), stores them in Postgres, and will
serve the dashboard and send phone alerts. Next.js (TypeScript) with Prisma 7 and zod.

```
ESP32 → gateway (laptop: Kelvin's receiver / Xavion's FFT pipeline) → POST here → Postgres → dashboard
```

## Run it locally

You need Node 20+ and Docker Desktop (running).

```bash
cd cloud
npm install                 # also generates the Prisma client
cp .env.example .env        # local-only values; real ones live in AWS
npm run db:up               # local Postgres in Docker, on port 5433
npm run db:migrate          # create the tables
npm run dev                 # http://localhost:3100
```

In a second terminal, pretend to be a machine (press `f` to toggle an imbalance fault):

```bash
npm run fake-device
```

Look at the data: `npm run db:studio` opens a table browser, or `curl localhost:3100/api/v1/machines`.

## Dashboard

Open `http://localhost:3100`. One page: every machine with a health status (Healthy, Warning, Critical,
No signal), and for the selected machine its temperature and vibration with limit lines, the latest FFT
window if any, the alert history, and a data-quality panel (last reading, sensor validity, readings waiting to
be stored, last database write). "No signal" means readings stopped arriving, which is different from a healthy
machine.

The live tiles come from `GET /api/v1/live` (memory, updated every packet); the 10-minute trend and the history
come from the database, so they lag by a few seconds.

To demo without hardware, run `npm run fake-fleet`: four simulated machines (drill press, band saw, lathe, air
compressor) as `SIM-001`…`SIM-004`, so they never mix with the real board's `MG-001`. Press `1`-`4` to start or stop a fault on one (shaking and overheating; it cools down afterwards) and
`q` `w` `e` `r` to cut or restore its signal. `npm run fake-device` is a single machine (`f` toggles the fault).
All of this data is simulated: say so when presenting it.

## API

Ingest routes need the header `x-device-key` (local value: `dev-key-change-me`). Each takes one packet or a JSON
array of up to 100. Extra fields are allowed and kept: every row stores the packet exactly as received in
`payload`, so a format change doesn't lose data. Invalid packets get a 400 that names the wrong field.

| Route | What |
|---|---|
| `POST /api/v1/status` | Status packet every ~0.5 s: held in memory, written every 2 s; abnormal ones written at once |
| `GET /api/v1/status` | What the status endpoint is holding in memory right now |
| `POST /api/v1/windows` | One analysed vibration window: the output of Xavion's `analyze_window()` |
| `GET /api/v1/machines` | Every machine, with its latest status packet and window |
| `GET /api/v1/machines/{id}/readings?minutes=10` | Status packets and windows from the last N minutes, oldest first |
| `GET /api/v1/live` | Last ~70 s of status packets per machine from memory, plus the limits in use (the dashboard's live view) |
| `GET /api/v1/alerts?machine_id=&limit=50` | Alerts, newest first |
| `PUT /api/v1/machines/{id}` | Register a machine or set its `name`, `kind`, `rpm` (needs `x-device-key`) |
| `GET /api/health` | `{"ok": true}`, for the load balancer; doesn't touch the database |

Packets carrying a `packet_id` already in the database are skipped (unique index, `ON CONFLICT DO NOTHING`)
and counted in the response's `duplicates`, so the gateway can retry safely.

Machines are created automatically the first time a packet names them. Field-by-field formats are in
`src/lib/packets.ts`.

### Status packet

What `predictive-maintenance/ingest/cloud.py` sends (it batches up to 50 per request):

```json
{
  "machine_id": "MG-001",
  "timestamp_ms": 96847,
  "sensors": [
    { "id": "sensor_0", "type": "thermistor", "voltage_mv": 1582, "valid": true, "temperature_c": 33.6 },
    { "id": "sensor_1", "type": "accelerometer", "x_g": 0.01, "y_g": -0.02, "z_g": 1.0, "valid": true },
    { "id": "sensor_2", "type": "gyroscope", "x_dps": 0.1, "y_dps": 0.0, "z_dps": 0.2, "valid": true }
  ],
  "boot_id": "a91f3c02",
  "seq": 1234,
  "packet_id": "3f2a…",
  "received_at_utc": "2026-11-07T17:58:52.177715+00:00"
}
```

Only `machine_id` and `sensors` are required. `temperature_c` may be `null` (the gateway couldn't convert it);
then the cloud converts `voltage_mv` itself with the same constants.

**Buffering.** Packets are held in memory and written to `StatusPacket` every 2 s, each with its own
`received_at_utc` as `receivedAt` (server clock if missing). A packet seen before (same `packet_id`, or same
`boot_id` + `seq`) is skipped, in memory and by a unique index, so gateway retries aren't stored twice.

**Abnormal readings.** Each packet is checked for: temperature too high or rising too fast, one accelerometer
reading far from 1 g (shock), recent accelerometer readings spread too wide (vibration), or a dead sensor
(`valid: false`, all-zero accelerometer, thermistor at 0 V or 3.3 V). 3 abnormal packets in a row open an `Alert`,
and that machine's held packets are written before the answer goes back; while the alert is open every
abnormal packet is written at once. 6 normal packets in a row close it. If that immediate write fails the answer
is a 503, so the gateway retries.

**Limits** are env vars (defaults are placeholders, set them from test runs): `TEMP_MAX_C` (60),
`TEMP_RISE_C_PER_MIN` (5), `SHOCK_TOLERANCE_G` (0.5), `VIBRATION_STD_MAX_G` (0.15), `STREAM_CONFIRM_COUNT` (3),
`STREAM_CLEAR_COUNT` (6), `STREAM_FLUSH_MS` (2000), and `THERMISTOR_R0_OHM`, `THERMISTOR_BETA`,
`THERMISTOR_FIXED_OHM`, `THERMISTOR_SUPPLY_MV`.

**Per-machine limits** override the shared ones for one machine, because a running drill shakes far more than an
idle lathe. Built in: `MG-001` (the real drill) allows a vibration spread of 2.0 g and a shock of 3.0 g, about 30%
above what it reached while running normally on Sat Nov 7 (the shared 0.15 g flagged 62 of those 150 readings).
Add or change machines with `MACHINE_LIMITS='{"MG-002": {"vibrationStdMaxG": 0.3, "tempMaxC": 70}}'` (keys:
`tempMaxC`, `tempRiseCPerMin`, `shockToleranceG`, `vibrationStdMaxG`). The dashboard draws each machine's own limits.

**Phone alerts** go through [ntfy](https://ntfy.sh) when `NTFY_TOPIC` is set: every alert that opens or closes is
pushed to that topic, after it's stored. Install the ntfy app and subscribe to the topic. On AWS the topic is the
secret `smolbugs/ntfy-topic` (anyone who knows it can read the alerts). `APP_URL` makes a tap open the dashboard.

**Run one server process only** (the AWS service is pinned to one task; see `deploy/aws-deploy.sh`). The buffer is in memory: two would each hold half the packets and miss
"3 in a row". A restart loses up to 2 s of held packets, and the gateway can't resend them (it deletes packets
once it gets a 200). One snapshot every 0.5 s can't show vibration frequency (nothing above 1 Hz); that needs
`/api/v1/windows`.

Tests for the buffering, limits and phone alerts: `npm test` (plain `node --test`, no extra install).

### Vibration window

Exactly what `analyze_window(window, rpm=...)` returns, plus `temperature_c` (may be `null`) and the gateway's
`boot_id`, `seq`, `packet_id` and `received_at_utc`. Written straight away; a retry with the same `packet_id` is skipped. See
`fixtures/windows.json` for four real examples from Xavion's simulator.

### Sending from Python (the gateway)

```python
import json, urllib.request

def post_to_cloud(path, packet, url="http://localhost:3100", key="dev-key-change-me"):
    req = urllib.request.Request(
        url + path,
        data=json.dumps(packet, allow_nan=False).encode(),  # NaN isn't valid JSON
        headers={"content-type": "application/json", "x-device-key": key},
    )
    with urllib.request.urlopen(req, timeout=5) as res:
        return json.load(res)

post_to_cloud("/api/v1/windows", analyze_window(window, rpm=3000))
```

## Notes

- **Prisma is pinned to 7.10.1.** `npm install prisma` currently pulls an 8.0 release candidate; don't upgrade
  mid-hackathon.
- npm 11 blocks install scripts unless approved; `package.json` → `allowScripts` approves Prisma's and esbuild's.
- Port 5433, not 5432, so it doesn't clash with another Postgres on the same laptop.
- `GET` routes have no key yet: anyone with the URL can read the data. Fine for the demo; revisit before real use.

## Credits

Next.js, React, Prisma, zod, node-postgres, Tailwind CSS, tsx. Phone alerts: ntfy (ntfy.sh).
