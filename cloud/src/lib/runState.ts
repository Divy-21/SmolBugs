// What the machine is doing, from its latest vibration window (the FFT summary the gateway sends
// every 10 s): off, handled (moved by hand), running, or running abnormally.
//
// From the real drill (MG-001), Sun Nov 8, ~80 windows:
//   off, board still on    0.6 - 0.8 mm/s; the "main frequency" is just noise (often ~303 Hz)
//   moved by hand           0.6 - 5 mm/s at 2 - 4 Hz
//   running in the air      8 - 15 mm/s at 67 - 166 Hz
//   runs 01:27 - 01:35 UTC  8 - 40 mm/s, often ~190 Hz (drilling wood under load? not confirmed)
//   fault test (02:15 UTC)  82 - 88 mm/s at ~10 Hz
// So the vibration level decides off / abnormal, and the frequency separates a hand from a motor.
// The main frequency of normal running wanders too much (67 - 166 Hz) to alarm on.
//
// No Next, Prisma or zod imports: scripts/runState.test.mts runs this with plain Node.

export type RunState = "off" | "handled" | "running" | "abnormal";

export type RunProfile = {
  offBelowMmS: number; // below this the machine is off (the sensor only sees its own noise)
  motorAboveHz: number; // a main frequency below this is a hand moving it, not a motor
  abnormalAboveMmS: number; // above this it is shaking far more than normal running does
};

// 50 sits between the heaviest run that may be normal use (40 mm/s) and the fault test (82 mm/s).
// Re-set it once the team confirms what the 01:27 - 01:35 runs were.
export const DEFAULT_RUN_PROFILE: RunProfile = { offBelowMmS: 1.0, motorAboveHz: 15, abnormalAboveMmS: 50 };

// The latest window says nothing about now if it's older than this (one every 10 s, plus slack).
export const WINDOW_FRESH_MS = 30_000;

export function runState(velocityRmsMmS: number, mainFreqHz: number, p: RunProfile = DEFAULT_RUN_PROFILE): RunState {
  if (velocityRmsMmS < p.offBelowMmS) return "off";
  if (velocityRmsMmS > p.abnormalAboveMmS) return "abnormal";
  if (mainFreqHz < p.motorAboveHz) return "handled";
  return "running";
}

export const RUN_STATE_TEXT: Record<RunState, string> = {
  off: "Off (sensor on, machine not running)",
  handled: "Being moved by hand, not running",
  running: "Running normally",
  abnormal: "Running, vibration far above normal",
};
