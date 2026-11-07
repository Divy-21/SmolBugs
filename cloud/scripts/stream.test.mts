// Tests for src/lib/stream.ts, with plain Node 22 (no install needed):
//   node --test scripts/stream.test.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_CONFIG,
  StreamBuffer,
  configFromEnv,
  evaluate,
  voltageToCelsius,
  type Batch,
  type IncomingReading,
  type StreamConfig,
} from "../src/lib/stream.ts";

const cfg: StreamConfig = { ...DEFAULT_CONFIG, flushIntervalMs: 60_000 };
const T0 = Date.parse("2026-11-07T18:00:00Z");

// A packet as the route unpacks it from ingest/cloud.py's JSON status packet.
let tick = 0;
function reading(over: Partial<IncomingReading> = {}): IncomingReading {
  tick++;
  return {
    machineId: "MG-001",
    receivedAt: new Date(T0 + tick * 500),
    deviceTimeMs: 10_000 + tick * 500,
    bootId: "a91f3c02",
    seq: tick,
    packetId: `pkt-${tick}`,
    thermistorMv: 1582, // about 33.6 °C with the default constants
    thermistorValid: true,
    temperatureC: null,
    accel: { x: 0.01, y: -0.01, z: 1.0 },
    accelValid: true,
    payload: {},
    ...over,
  };
}
const hot = () => reading({ thermistorMv: 400 }); // well above 60 °C

function recorder() {
  const batches: Batch[] = [];
  let failNext = false;
  const persist = async (b: Batch) => {
    if (failNext) {
      failNext = false;
      throw new Error("database down");
    }
    batches.push(b);
  };
  return { batches, persist, fail: () => (failNext = true) };
}

test("temperature matches processing/temperature.py", () => {
  const t = voltageToCelsius(1582, cfg.thermistor);
  assert.ok(t !== null && Math.abs(t - 33.57) < 0.05, `got ${t}`);
  assert.equal(voltageToCelsius(0, cfg.thermistor), null);
  assert.equal(voltageToCelsius(3300, cfg.thermistor), null);
});

test("the gateway's temperature_c is used when it sent one", () => {
  const r = evaluate(reading({ temperatureC: 75 }), [], cfg);
  assert.equal(r.temperatureC, 75);
  assert.deepEqual(r.flags, ["temp_high"]);
});

test("normal packets are held until the 30 s write", async () => {
  const rec = recorder();
  const buf = new StreamBuffer(rec.persist, cfg);
  for (let i = 0; i < 10; i++) assert.equal(buf.add(reading()).flushNow, false);
  assert.equal(rec.batches.length, 0);
  assert.equal(await buf.flush(), true);
  assert.equal(rec.batches.length, 1);
  assert.equal(rec.batches[0].readings.length, 10);
  assert.equal(rec.batches[0].events.length, 0);
  assert.equal(buf.stats().machines[0].buffered, 0);
});

test("the timer writes on its own", async () => {
  const rec = recorder();
  const buf = new StreamBuffer(rec.persist, { ...cfg, flushIntervalMs: 30 });
  buf.start();
  buf.add(reading());
  await new Promise((r) => setTimeout(r, 80));
  buf.stop();
  assert.equal(rec.batches.length >= 1, true);
  assert.equal(rec.batches[0].readings.length, 1);
});

test("one or two abnormal packets don't trigger; the third in a row does", () => {
  const buf = new StreamBuffer(recorder().persist, cfg);
  buf.add(reading());
  assert.deepEqual(buf.add(hot()).flags, ["temp_high"]);
  assert.equal(buf.add(hot()).flushNow, false);
  assert.equal(buf.add(hot()).flushNow, true);
  const s = buf.machines.get("MG-001")!;
  assert.equal(s.inAlert, true);
  assert.deepEqual(s.buffer.map((r) => r.abnormal), [false, true, true, true]);
  assert.equal(s.events.length, 1);
  const e = s.events[0];
  assert.equal(e.type, "open");
  if (e.type === "open") {
    assert.equal(e.kind, "overheat");
    assert.equal(e.severity, "alert");
  }
});

test("a broken streak does not trigger", () => {
  const buf = new StreamBuffer(recorder().persist, cfg);
  buf.add(hot());
  buf.add(hot());
  buf.add(reading());
  assert.equal(buf.add(hot()).flushNow, false);
  assert.equal(buf.machines.get("MG-001")!.inAlert, false);
});

test("during an alert every abnormal packet goes at once; it closes after clearCount normal", () => {
  const buf = new StreamBuffer(recorder().persist, cfg);
  for (let i = 0; i < 3; i++) buf.add(hot());
  assert.equal(buf.add(hot()).flushNow, true);
  for (let i = 0; i < cfg.clearCount - 1; i++) buf.add(reading());
  assert.equal(buf.machines.get("MG-001")!.inAlert, true);
  buf.add(reading());
  const s = buf.machines.get("MG-001")!;
  assert.equal(s.inAlert, false);
  assert.deepEqual(s.events.map((e) => e.type), ["open", "resolve"]);
});

test("a failed write keeps the packets, in order, for the next one", async () => {
  const rec = recorder();
  const buf = new StreamBuffer(rec.persist, cfg);
  const a = reading();
  const b = reading();
  buf.add(a);
  buf.add(b);
  rec.fail();
  assert.equal(await buf.flush(), false);
  assert.equal(buf.stats().machines[0].last_error, "database down");
  const c = reading();
  buf.add(c);
  assert.equal(await buf.flush(), true);
  assert.deepEqual(rec.batches[0].readings.map((r) => r.packetId), [a.packetId, b.packetId, c.packetId]);
});

test("a gateway retry (same packet_id) is not stored or counted twice", () => {
  const buf = new StreamBuffer(recorder().persist, cfg);
  const p = reading();
  assert.equal(buf.add(p).duplicate, false);
  assert.equal(buf.add({ ...p }).duplicate, true);
  assert.equal(buf.machines.get("MG-001")!.buffer.length, 1);
  // Without packet_id, boot_id + seq is the key; without either, nothing is dropped.
  const q = reading({ packetId: null });
  buf.add(q);
  assert.equal(buf.add({ ...q }).duplicate, true);
  const r = reading({ packetId: null, bootId: null });
  buf.add(r);
  assert.equal(buf.add({ ...r }).duplicate, false);
});

test("sensor faults: firmware valid=false, all-zero accelerometer, thermistor at a rail", () => {
  assert.deepEqual(evaluate(reading({ thermistorValid: false, accelValid: false }), [], cfg).flags, [
    "thermistor_fault",
    "accel_fault",
  ]);
  const z = evaluate(reading({ accel: { x: 0, y: 0, z: 0 }, thermistorMv: 3299 }), [], cfg);
  assert.deepEqual(z.flags, ["thermistor_fault", "accel_fault"]);
  assert.equal(z.temperatureC, null);
});

test("a packet with only some sensors is fine", () => {
  assert.deepEqual(evaluate(reading({ accel: null, accelValid: null }), [], cfg).flags, []);
  assert.deepEqual(evaluate(reading({ thermistorMv: null, thermistorValid: null }), [], cfg).flags, []);
});

test("shock and vibration spread", () => {
  assert.deepEqual(evaluate(reading({ accel: { x: 0, y: 0, z: 2.0 } }), [], cfg).flags, ["shock"]);

  const buf = new StreamBuffer(recorder().persist, cfg);
  let flags: string[] = [];
  for (let i = 0; i < 20; i++) {
    const s = i % 2 === 0 ? 0.3 : -0.3; // shaking ±0.3 g on x
    flags = buf.add(reading({ accel: { x: s, y: 0, z: 1 } })).flags;
  }
  assert.deepEqual(flags, ["vibration_high"]);
});

test("temperature rising too fast", () => {
  const buf = new StreamBuffer(recorder().persist, cfg);
  // 1582 mV is ~33.6 °C; 1300 mV is ~42.1 °C, reached 30 s later: ~17 °C/min.
  buf.add(reading({ thermistorMv: 1582, receivedAt: new Date(T0) }));
  const r = buf.add(reading({ thermistorMv: 1300, receivedAt: new Date(T0 + 30_000) }));
  assert.deepEqual(r.flags, ["temp_rising"]);
});

test("limits can be set from the environment", () => {
  const c = configFromEnv({ TEMP_MAX_C: "45", STREAM_FLUSH_MS: "10000", THERMISTOR_BETA: "abc" });
  assert.equal(c.tempMaxC, 45);
  assert.equal(c.flushIntervalMs, 10_000);
  assert.equal(c.thermistor.beta, DEFAULT_CONFIG.thermistor.beta); // bad value -> default
});
