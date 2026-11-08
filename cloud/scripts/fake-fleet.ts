// Pretends to be a small workshop of four machines, so the dashboard can be
// demonstrated without hardware. ALL DATA IS SIMULATED: say so when presenting.
//
//   npm run fake-fleet                       -> local dev server
//   CLOUD_URL=https://... DEVICE_KEY=... npm run fake-fleet
//
// Every 500 ms each machine posts a status packet in the gateway's format
// (thermistor voltage + accelerometer snapshot) to /api/v1/status.
//
// Keys:   1-4  start / stop a fault on that machine (shaking and overheating)
//         q w e r  cut / restore that machine's signal (shows "No signal")
//         Ctrl+C   stop

const url = process.env.CLOUD_URL ?? "http://localhost:3100";
const key = process.env.DEVICE_KEY ?? "dev-key-change-me";

type Sim = {
  id: string;
  name: string;
  kind: string;
  rpm: number;
  baseMv: number; // divider voltage when healthy; lower = hotter
  shakeG: number; // healthy vibration level
  faultSince: number | null;
  heatMv: number; // how far the fault has pulled the voltage down (i.e. heated it)
  silent: boolean;
  seq: number;
  bootId: string;
  started: number;
};

const fleet: Sim[] = [
  { id: "SIM-001", name: "Drill press", kind: "drill", rpm: 1800, baseMv: 1580, shakeG: 0.01 },
  { id: "SIM-002", name: "Band saw", kind: "band_saw", rpm: 1450, baseMv: 1500, shakeG: 0.025 },
  { id: "SIM-003", name: "Lathe", kind: "lathe", rpm: 1200, baseMv: 1620, shakeG: 0.015 },
  { id: "SIM-004", name: "Air compressor", kind: "compressor", rpm: 3000, baseMv: 1350, shakeG: 0.04 },
].map((m) => ({
  ...m,
  faultSince: null,
  heatMv: 0,
  silent: false,
  seq: 0,
  bootId: Math.random().toString(16).slice(2, 10),
  started: Date.now(),
}));

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
  m.heatMv = m.faultSince === null ? Math.max(0, m.heatMv - 1.5) : m.heatMv + 3;
  const voltage = Math.max(200, Math.round(m.baseMv + 15 * Math.sin(t / 40) - m.heatMv));
  const shake = m.faultSince === null ? m.shakeG : 0.3;
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
        x_g: +jitter(shake).toFixed(3),
        y_g: +jitter(shake).toFixed(3),
        z_g: +(1 + jitter(shake)).toFixed(3),
        valid: true,
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
    const live = fleet.filter((m) => !m.silent).map(packet);
    if (live.length) void call("POST", "/api/v1/status", live);
  }, 500);

  console.log(`Simulating ${fleet.length} machines -> ${url}`);
  console.log("1-4: toggle a fault   q w e r: toggle signal loss   Ctrl+C: stop");
  fleet.forEach((m, i) => console.log(`  ${i + 1} / ${"qwer"[i]}  ${m.id}  ${m.name}`));

  if (process.stdin.isTTY) {
    process.stdin.setRawMode(true);
    process.stdin.on("data", (b) => {
      const k = b.toString();
      if (k === "\u0003") process.exit(0);
      const fi = "1234".indexOf(k);
      if (fi >= 0) {
        const m = fleet[fi];
        m.faultSince = m.faultSince === null ? Date.now() : null;
        console.log(`${m.id} ${m.name}: ${m.faultSince === null ? "healthy" : "FAULT (shaking + overheating)"}`);
      }
      const si = "qwer".indexOf(k);
      if (si >= 0) {
        const m = fleet[si];
        m.silent = !m.silent;
        console.log(`${m.id} ${m.name}: ${m.silent ? "signal LOST" : "signal restored"}`);
      }
    });
  }
}

void main();
