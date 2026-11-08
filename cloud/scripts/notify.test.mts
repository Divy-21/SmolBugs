// Tests for src/lib/notify.ts (phone alerts), with plain Node:
//   node --test scripts/notify.test.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer, type IncomingHttpHeaders } from "node:http";
import type { AddressInfo } from "node:net";
import { notifyAlerts, pushFor } from "../src/lib/notify.ts";

const at = new Date("2026-11-08T18:00:00Z");

test("an opened alert reads as words, with high priority", () => {
  const p = pushFor({
    type: "open", machineId: "MG-001", at, severity: "alert", kind: "vibration",
    message: "Outside limits: vibration_high, shock",
  });
  assert.equal(p.title, "MG-001: vibration alert");
  assert.equal(p.message, "Vibration above its limit, sudden shock.");
  assert.equal(p.priority, "high");
});

test("a sensor fault asks to check the sensor; a resolve says back to normal", () => {
  const fault = pushFor({
    type: "open", machineId: "MG-002", at, severity: "watch", kind: "sensor_fault",
    message: "Outside limits: accel_fault",
  });
  assert.equal(fault.title, "MG-002: check the sensor");
  assert.equal(fault.priority, "default");
  assert.equal(pushFor({ type: "resolve", machineId: "MG-002", at }).title, "MG-002: back to normal");
});

test("posts each event to the topic; off without NTFY_TOPIC; a dead server doesn't throw", async () => {
  const got: { url: string; body: string; headers: IncomingHttpHeaders }[] = [];
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      got.push({ url: req.url ?? "", body, headers: req.headers });
      res.end("{}");
    });
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const events = [
    { type: "open" as const, machineId: "MG-001", at, severity: "alert" as const, kind: "overheat", message: "Outside limits: temp_high" },
    { type: "resolve" as const, machineId: "MG-001", at },
  ];

  notifyAlerts(events, { NTFY_SERVER: base }); // no topic: nothing sent
  notifyAlerts(events, { NTFY_SERVER: base, NTFY_TOPIC: "smolbugs-test", APP_URL: "https://example.test" });
  for (let i = 0; i < 50 && got.length < 2; i++) await new Promise((r) => setTimeout(r, 20));
  server.close();

  assert.equal(got.length, 2);
  assert.equal(got[0].url, "/smolbugs-test");
  assert.equal(got[0].headers.title, "MG-001: overheat alert");
  assert.equal(got[0].headers.click, "https://example.test");
  assert.equal(got[0].body, "Temperature above its limit.");
  assert.equal(got[1].headers.title, "MG-001: back to normal");

  // nothing listening: logged, not thrown
  assert.doesNotThrow(() => notifyAlerts(events, { NTFY_SERVER: "http://127.0.0.1:1", NTFY_TOPIC: "x" }));
});
