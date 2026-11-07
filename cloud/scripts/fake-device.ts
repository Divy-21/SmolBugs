// Pretends to be a machine, so the dashboard can be built without hardware.
//
//   npm run fake-device                        -> local dev server, machine MG-001
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
const machineId = process.env.MACHINE_ID ?? "MG-001";

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

setInterval(() => {
  const t = (Date.now() - started) / 1000;
  const voltage = Math.round(1580 + 20 * Math.sin(t / 30) + (faulty ? -0.5 * t : 0));
  void post("/api/v1/status", {
    machine_id: machineId,
    timestamp_ms: Date.now() - started,
    boot_id: bootId,
    seq: seq++,
    sensors: [{ id: "sensor_0", type: "thermistor", voltage_mv: voltage }],
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
      console.log(faulty ? "-> sending IMBALANCE" : "-> sending healthy");
    }
  });
}
