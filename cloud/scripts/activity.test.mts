// Tests for src/lib/activity.ts (online / idle / offline), with plain Node 22:
//   node --test scripts/activity.test.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_ACTIVITY, activityConfigFromEnv, classifyActivity, type StoredSample } from "../src/lib/activity.ts";

const NOW = new Date("2026-11-08T18:00:00Z");
const ago = (ms: number) => new Date(NOW.getTime() - ms);

// n readings, 0.5 s apart, ending `endAgoMs` before NOW, shaking ±amp g on x.
function readings(n: number, amp: number, endAgoMs = 500): StoredSample[] {
  return Array.from({ length: n }, (_, i) => ({
    receivedAt: ago(endAgoMs + i * 500),
    x: i % 2 === 0 ? amp : -amp,
    y: 0,
    z: 1,
  }));
}

test("nothing stored yet is offline", () => {
  const a = classifyActivity(null, [], NOW);
  assert.equal(a.state, "offline");
  assert.equal(a.ageMs, null);
});

test("no reading stored for longer than the limit is offline", () => {
  const r = readings(20, 0.3, 20_000);
  const a = classifyActivity(r[0].receivedAt, r, NOW);
  assert.equal(a.state, "offline");
  assert.equal(a.reason, "No reading stored for 20 s");
});

test("receiving, machine moving: online", () => {
  const r = readings(20, 0.3);
  const a = classifyActivity(r[0].receivedAt, r, NOW);
  assert.equal(a.state, "online");
  assert.ok(a.vibrationSpreadG !== null && Math.abs(a.vibrationSpreadG - 0.3) < 1e-9);
  assert.equal(a.readingsLastMinute, 20);
});

test("receiving, machine still: idle", () => {
  const r = readings(20, 0.01);
  assert.equal(classifyActivity(r[0].receivedAt, r, NOW).state, "idle");
});

test("receiving without accelerometer data: online (can't tell idle)", () => {
  const r = readings(20, 0).map((s) => ({ ...s, x: null, y: null, z: null }));
  const a = classifyActivity(r[0].receivedAt, r, NOW);
  assert.equal(a.state, "online");
  assert.equal(a.vibrationSpreadG, null);
});

test("all-zero (dead) accelerometer readings are ignored", () => {
  const r = readings(20, 0).map((s) => ({ ...s, x: 0, y: 0, z: 0 }));
  assert.equal(classifyActivity(r[0].receivedAt, r, NOW).vibrationSpreadG, null);
});

test("a gateway clock slightly ahead of the server counts as just now", () => {
  const future = new Date(NOW.getTime() + 3000);
  const a = classifyActivity(future, readings(20, 0.3), NOW);
  assert.equal(a.state, "online");
  assert.equal(a.ageMs, 0);
});

test("only the newest readings decide idle vs online", () => {
  // Was shaking a minute ago, still for the last 20 readings: idle.
  const r = [...readings(20, 0.01), ...readings(40, 0.5, 11_000)];
  assert.equal(classifyActivity(r[0].receivedAt, r, NOW).state, "idle");
});

test("idle 5 s after it stops: 10 still readings are enough", () => {
  const r = [...readings(10, 0.01), ...readings(40, 0.5, 5_500)];
  assert.equal(classifyActivity(r[0].receivedAt, r, NOW).state, "idle");
  const moving = [...readings(9, 0.01), ...readings(40, 0.5, 5_000)]; // only 9 still: not yet
  assert.equal(classifyActivity(moving[0].receivedAt, moving, NOW).state, "online");
});

test("limits can be set from the environment", () => {
  const c = activityConfigFromEnv({ OFFLINE_AFTER_S: "30", IDLE_SPREAD_MAX_G: "0.1" });
  assert.equal(c.offlineAfterMs, 30_000);
  assert.equal(c.idleSpreadMaxG, 0.1);
  assert.equal(activityConfigFromEnv({ OFFLINE_AFTER_S: "abc" }).offlineAfterMs, DEFAULT_ACTIVITY.offlineAfterMs);
});
