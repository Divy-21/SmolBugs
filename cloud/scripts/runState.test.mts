// Tests for src/lib/runState.ts, with plain Node: node --test scripts/runState.test.mts
// Every case is a real window from the drill (MG-001), Sun Nov 8 (UTC times).
import { test } from "node:test";
import assert from "node:assert/strict";
import { runState } from "../src/lib/runState.ts";

test("drill off, board on: tiny vibration, the peak is just noise", () => {
  assert.equal(runState(0.70, 302.4), "off"); // 00:50 run, median
  assert.equal(runState(0.76, 311.8), "off"); // 01:19:24
  assert.equal(runState(0.70, 3.7), "off"); // 00:34:32, a start-up window
});

test("moved by hand: slow (2-4 Hz), a few mm/s", () => {
  assert.equal(runState(1.24, 2.9), "handled"); // 01:17:44
  assert.equal(runState(5.15, 2.2), "handled"); // 01:18:04
});

test("running, in the air or into wood: 8-15 mm/s, 67-166 Hz", () => {
  assert.equal(runState(8.04, 135.8), "running");
  assert.equal(runState(14.80, 135.8), "running"); // the highest normal window
  assert.equal(runState(12.42, 96.4), "running"); // wood, 01:28:15
  assert.equal(runState(5.92, 67.2), "running"); // 00:33:12, spinning up
});

test("rubbing on wood (01:27-01:35 UTC, 21-40 mm/s) is abnormal; normal never goes above 20", () => {
  assert.equal(runState(40, 22), "abnormal");
  assert.equal(runState(28, 193), "abnormal");
  assert.equal(runState(21, 189), "abnormal");
  assert.equal(runState(19.5, 190), "running"); // just under the line
});

test("the fault test (something off-balance on the drill): ~85 mm/s at ~10 Hz is abnormal, not 'handled'", () => {
  assert.equal(runState(82.1, 10), "abnormal"); // 02:15:42
  assert.equal(runState(87.6, 10), "abnormal"); // 02:15:52
});
