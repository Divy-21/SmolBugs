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

## API

Ingest routes need the header `x-device-key` (local value: `dev-key-change-me`). Each takes one packet or a JSON
array of up to 100. Extra fields are allowed and kept: every row stores the packet exactly as received in
`payload`, so a format change doesn't lose data. Invalid packets get a 400 that names the wrong field.

| Route | What |
|---|---|
| `POST /api/v1/status` | Status packet (Kelvin's format): thermistor and other slow sensors |
| `POST /api/v1/windows` | One analysed vibration window: the output of Xavion's `analyze_window()` |
| `GET /api/v1/machines` | Every machine, with its latest status packet and window |
| `GET /api/v1/machines/{id}/readings?minutes=10` | Status packets and windows from the last N minutes, oldest first |
| `GET /api/health` | `{"ok": true}`, for the load balancer; doesn't touch the database |

Packets carrying a `packet_id` already in the database are skipped (unique index, `ON CONFLICT DO NOTHING`)
and counted in the response's `duplicates`, so the gateway can retry safely.

Machines are created automatically the first time a packet names them. Field-by-field formats are in
`src/lib/packets.ts`.

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

`boot_id` and `seq` are optional but recommended: they let us spot reboots and dropped packets. If the gateway
converts the thermistor to °C, add `"temperature_c"` to that sensor.

### Vibration window

Exactly what `analyze_window(window, rpm=...)` returns, plus an optional `temperature_c`. See
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

Next.js, React, Prisma, zod, node-postgres, Tailwind CSS, tsx.
