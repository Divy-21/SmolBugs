// Pretends to be a machine, so the dashboard can be built without hardware.
//
//   npm run fake-device                        -> local dev server, machine SIM-DEV
//   CLOUD_URL=https://... DEVICE_KEY=... MACHINE_ID=MG-002 npm run fake-device
//
// Every 500 ms: a status packet in Kelvin's format, thermistor voltage drifting.
// Every second: one vibration window, replayed from fixtures/windows.json (real
// output of Xavion's analyze_window() on his simulator: healthy, imbalance,
// misalignment, bearing). It sends "healthy" until you press f in the
// terminal, then "imbalance"; press f again to go back.

import { readFileSync } from "node:fs";

const url = process.env.CLOUD_URL ?? "http://localhost:3100";
const key = process.env.DEVICE_KEY ?? "dev-key-change-me";
const machineId = process.env.MACHINE_ID ?? "SIM-DEV";

type Window = Record<string, unknown> & { window_id: number };
const fixtures = JSON.parse(
  readFileSync(new URL("../fixtures/windows.json", import.meta.url), "utf8"),
) as Window[];
const [healthy, imbalance] = fixtures;

let faulty = false;
let seq = 0;
let windowId = 0;
const bootId = Math.random().toString(16).slice(2, 10);
const started = Date.now();

async function post(path: string, body: unknown) {
  try {
    const res = await fetch(`${url}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-device-key": key },
      body: JSON.stringify(body),
    });
    if (!res.ok) console.error(`${path} -> ${res.status} ${await res.text()}`);
  } catch (err) {
    console.error(`${path} -> ${(err as Error).message}`);
  }
}

// SIMULATED data: healthy is ~34 °C and a still accelerometer (1 g on z). The
// fault shakes the accelerometer (±0.3 g) and drops the divider voltage 6 mV/s,
// i.e. the temperature climbs about 1 °C every 3 s.
let faultSince = 0;
const jitter = (g: number) => (Math.random() * 2 - 1) * g;

setInterval(() => {
  const t = (Date.now() - started) / 1000;
  const faultS = faulty ? (Date.now() - faultSince) / 1000 : 0;
  const voltage = Math.max(200, Math.round(1580 + 20 * Math.sin(t / 30) - 6 * faultS));
  const shake = faulty ? 0.3 : 0.01;
  void post("/api/v1/status", {
    machine_id: machineId,
    timestamp_ms: Date.now() - started,
    boot_id: bootId,
    seq: seq++,
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
  });
}, 500);

setInterval(() => {
  const base = faulty ? imbalance : healthy;
  void post("/api/v1/windows", { ...base, machine_id: machineId, window_id: windowId++ });
}, 1000);

console.log(`Sending as ${machineId} to ${url}. Press f to toggle the imbalance fault, Ctrl+C to stop.`);
if (process.stdin.isTTY) {
  process.stdin.setRawMode(true);
  process.stdin.on("data", (b) => {
    const k = b.toString();
    if (k === "\u0003") process.exit(0);
    if (k === "f") {
      faulty = !faulty;
      faultSince = Date.now();
      console.log(faulty ? "-> sending FAULT (shaking + overheating)" : "-> sending healthy");
    }
  });
}
