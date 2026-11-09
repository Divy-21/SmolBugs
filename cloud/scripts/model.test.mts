// Tests for the trained model's answer riding on a vibration window (src/lib/packets.ts).
// Plain Node: node --test scripts/model.test.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import { readModel, vibrationWindow } from "../src/lib/packets.ts";

// What predictive-maintenance/ingest/live_model.py adds under "ml".
const ok = {
  state: "ok", label: "normal_free", confidence: 0.94, message: "Running normally",
  recommendation: "No action needed.", agree: 7, of: 7,
};
const learning = {
  state: "learning", label: null, confidence: null, message: "Learning this drill's normal (1 of 3)",
  recommendation: "Run the drill normally until this finishes.", agree: null, of: null,
};
const axis = { dominant_freq_hz: 135.8, dominant_amp_g: 0.05, velocity_rms_mm_s: 9.1, band_power_g2: {} };
const win = (ml?: unknown) => ({
  machine_id: "MG-001", window_id: 7, sample_rate_hz: 1600, n_samples: 4096,
  axes: { x: axis, y: axis, z: axis }, ...(ml === undefined ? {} : { ml }),
});

test("the gateway's answers are read back as sent", () => {
  assert.deepEqual(readModel({ ml: ok }), ok);
  assert.deepEqual(readModel({ ml: learning }), learning);
});

test("no model, or a malformed answer, reads as null instead of breaking the dashboard", () => {
  assert.equal(readModel(null), null);
  assert.equal(readModel({}), null);
  assert.equal(readModel({ ml: null }), null);
  assert.equal(readModel({ ml: { ...ok, state: "exploded" } }), null);
  assert.equal(readModel({ ml: { ...ok, confidence: 7 } }), null);
  assert.equal(readModel({ ml: { state: "ok" } }), null); // no message
});

test("a window is accepted with or without the model's answer, even an odd one", () => {
  assert.ok(vibrationWindow.safeParse(win()).success);
  assert.ok(vibrationWindow.safeParse(win(ok)).success);
  assert.ok(vibrationWindow.safeParse(win({ state: "exploded" })).success); // stored, never shown
});
