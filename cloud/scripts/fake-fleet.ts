// Pretends to be a small workshop of six machines, so the dashboard can be
// demonstrated without hardware. ALL DATA IS SIMULATED: say so when presenting.
//
//   npm run fake-fleet                       -> local dev server, every status on show
//   npm run fake-fleet -- --all-running      -> every machine starts running normally
//   CLOUD_URL=https://... DEVICE_KEY=... npm run fake-fleet
//
// Starting states (so each dashboard status is visible straight away):
//   SIM-001 Drill press      running          -> Online
//   SIM-002 Band saw         vibration fault  -> Broken (abnormal vibration), within a few seconds
//   SIM-003 Lathe            switched off     -> Idle (still sends data)
//   SIM-004 Air compressor   sends 5 s, stops -> Offline after 15 s more
//   SIM-005 Milling machine  running          -> Online
//   SIM-006 Bench grinder    dead accelerometer -> Broken (sensor not responding)
//
// Every 500 ms each machine posts a status packet in the gateway's format
// (thermistor voltage + accelerometer snapshot) to /api/v1/status.
//
// Keys (one per machine, left to right):
//         1-6          start / stop a fault (shaking and overheating -> Broken)
//         q w e r t y  cut / restore the signal (-> Offline after 15 s)
//         a s d f g h  stop / start running (-> Idle; it still sends data)
//         z x c v b n  break / fix the accelerometer (-> Broken, sensor not responding)
//         Ctrl+C       stop

const url = process.env.CLOUD_URL ?? "http://localhost:3100";
const key = process.env.DEVICE_KEY ?? "dev-key-change-me";

type Sim = {
  id: string;
  name: string;
  kind: string;
  rpm: number;
  baseMv: number; // divider voltage when healthy; lower = hotter
  shakeG: number; // vibration while running normally: above the 0.05 g idle cutoff, below the 0.15 g limit
  idle: boolean; // switched off but still powered: sends data, barely moves
  faultSince: number | null;
  heatMv: number; // how far the fault has pulled the voltage down (i.e. heated it)
  silent: boolean;
  goSilentAt: number | null; // stop sending at this time (to show Offline)
  sensorDead: boolean; // accelerometer reads 0,0,0 with valid=false
  seq: number;
  bootId: string;
  started: number;
};

type Start = "running" | "fault" | "idle" | "offline" | "sensor_dead";
const allRunning = process.argv.includes("--all-running");
const startedAt = Date.now();

const fleet: Sim[] = (
  [
    { id: "SIM-001", name: "Drill press", kind: "drill", rpm: 1800, baseMv: 1580, shakeG: 0.07, start: "running" },
    { id: "SIM-002", name: "Band saw", kind: "band_saw", rpm: 1450, baseMv: 1500, shakeG: 0.085, start: "fault" },
    { id: "SIM-003", name: "Lathe", kind: "lathe", rpm: 1200, baseMv: 1620, shakeG: 0.06, start: "idle" },
    { id: "SIM-004", name: "Air compressor", kind: "compressor", rpm: 3000, baseMv: 1350, shakeG: 0.1, start: "offline" },
    { id: "SIM-005", name: "Milling machine", kind: "mill", rpm: 2400, baseMv: 1560, shakeG: 0.08, start: "running" },
    { id: "SIM-006", name: "Bench grinder", kind: "grinder", rpm: 3450, baseMv: 1600, shakeG: 0.08, start: "sensor_dead" },
  ] as const
).map(({ start: s0, ...m }) => {
  const start: Start = allRunning ? "running" : s0;
  return {
    ...m,
    faultSince: start === "fault" ? startedAt : null,
    heatMv: 0,
    silent: false,
    goSilentAt: start === "offline" ? startedAt + 5000 : null,
    sensorDead: start === "sensor_dead",
    idle: start === "idle",
    seq: 0,
    bootId: Math.random().toString(16).slice(2, 10),
    started: startedAt,
  };
});

async function call(method: string, path: string, body: unknown) {
  try {
    const res = await fetch(`${url}${path}`, {
      method,
      headers: { "content-type": "application/json", "x-device-key": key },
      body: JSON.stringify(body),
    });
    if (!res.ok && res.status !== 503) console.error(`${method} ${path} -> ${res.status} ${await res.text()}`);
  } catch (err) {
    console.error(`${method} ${path} -> ${(err as Error).message}`);
  }
}

const jitter = (g: number) => (Math.random() * 2 - 1) * g;

function packet(m: Sim) {
  const now = Date.now();
  const t = (now - m.started) / 1000;
  // Healthy: slow drift of under a degree. Fault: heats ~1 °C every 3 s, then
  // cools at half that rate once the fault is cleared (called every 0.5 s).
  m.heatMv = m.faultSince === null ? Math.max(0, m.heatMv - 1.5) : Math.min(900, m.heatMv + 3); // tops out near 70 °C
  const voltage = Math.max(200, Math.round(m.baseMv + 15 * Math.sin(t / 40) - m.heatMv));
  const shake = m.faultSince !== null ? 0.3 : m.idle ? 0.004 : m.shakeG;
  return {
    machine_id: m.id,
    timestamp_ms: now - m.started,
    boot_id: m.bootId,
    seq: m.seq++,
    sensors: [
      { id: "sensor_0", type: "thermistor", voltage_mv: voltage, valid: true },
      {
        id: "sensor_1",
        type: "accelerometer",
        x_g: m.sensorDead ? 0 : +jitter(shake).toFixed(3),
        y_g: m.sensorDead ? 0 : +jitter(shake).toFixed(3),
        z_g: m.sensorDead ? 0 : +(1 + jitter(shake)).toFixed(3),
        valid: !m.sensorDead,
      },
    ],
  };
}

async function main() {
  // Give each machine a name on the dashboard.
  for (const m of fleet) {
    await call("PUT", `/api/v1/machines/${m.id}`, { name: m.name, kind: m.kind, rpm: m.rpm });
  }

  setInterval(() => {
    for (const m of fleet) {
      if (m.goSilentAt !== null && Date.now() >= m.goSilentAt) {
        m.silent = true;
        m.goSilentAt = null;
        console.log(`${m.id} ${m.name}: signal LOST (scripted, shows Offline in ~15 s)`);
      }
    }
    const live = fleet.filter((m) => !m.silent).map(packet);
    if (live.length) void call("POST", "/api/v1/status", live);
  }, 500);

  console.log(`Simulating ${fleet.length} machines -> ${url}`);
  console.log("Keys per machine: fault / signal / idle / sensor.  Ctrl+C: stop");
  fleet.forEach((m, i) => {
    const now = m.faultSince !== null ? "fault" : m.sensorDead ? "sensor broken" : m.idle ? "idle" : m.goSilentAt ? "goes offline in 5 s" : "running";
    console.log(`  ${"123456"[i]} ${"qwerty"[i]} ${"asdfgh"[i]} ${"zxcvbn"[i]}  ${m.id}  ${m.name.padEnd(16)} ${now}`);
  });

  if (process.stdin.isTTY) {
    process.stdin.setRawMode(true);
    process.stdin.on("data", (b) => {
      const k = b.toString();
      if (k === "\u0003") process.exit(0);
      const fi = "123456".indexOf(k);
      if (fi >= 0) {
        const m = fleet[fi];
        m.faultSince = m.faultSince === null ? Date.now() : null;
        console.log(`${m.id} ${m.name}: ${m.faultSince === null ? "healthy" : "FAULT (shaking + overheating)"}`);
      }
      const si = "qwerty".indexOf(k);
      if (si >= 0) {
        const m = fleet[si];
        m.silent = !m.silent;
        m.goSilentAt = null;
        console.log(`${m.id} ${m.name}: ${m.silent ? "signal LOST" : "signal restored"}`);
      }
      const di = "zxcvbn".indexOf(k);
      if (di >= 0) {
        const m = fleet[di];
        m.sensorDead = !m.sensorDead;
        console.log(`${m.id} ${m.name}: accelerometer ${m.sensorDead ? "BROKEN (reads 0, valid=false)" : "fixed"}`);
      }
      const ii = "asdfgh".indexOf(k);
      if (ii >= 0) {
        const m = fleet[ii];
        m.idle = !m.idle;
        console.log(`${m.id} ${m.name}: ${m.idle ? "IDLE (switched off, still sending)" : "running"}`);
      }
    });
  }
}

void main();
