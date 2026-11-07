import { z } from "zod";

// The packet formats the cloud accepts. Fields the team adds later are kept
// (loose objects) and stored in `payload`; only the fields below are checked.
//
// Senders: predictive-maintenance/ingest/cloud.py (the gateway). It adds
// boot_id, seq, packet_id and received_at_utc to everything it sends, turns
// NaN into null, and retries with the same packet_id.

const machineId = z
  .string()
  .trim()
  .min(1)
  .max(64)
  .regex(/^[A-Za-z0-9_.-]+$/, "machine_id may use letters, digits, _ . - only");

// Identity fields the gateway adds. All optional, so a test script can leave them out.
const identity = {
  boot_id: z.string().max(64).optional(),
  seq: z.number().int().nonnegative().optional(),
  packet_id: z.string().min(1).max(64).optional(),
  received_at_utc: z
    .string()
    .refine((s) => !Number.isNaN(Date.parse(s)), "received_at_utc must be an ISO 8601 time, e.g. 2026-11-07T17:58:52Z")
    .optional(),
};

// --- Status packet: posted by the gateway every ~0.5 s per machine -------------
//
// { "machine_id": "MG-001", "timestamp_ms": 96847,
//   "sensors": [ { "id": "sensor_0", "type": "thermistor", "voltage_mv": 1582, "valid": true,
//                  "temperature_c": 33.6 },
//                { "id": "sensor_1", "type": "accelerometer", "x_g": 0.01, "y_g": -0.02, "z_g": 1.0,
//                  "valid": true },
//                { "id": "sensor_2", "type": "gyroscope", "x_dps": 0.1, "y_dps": 0.0, "z_dps": 0.2 } ],
//   "boot_id": "a91f3c02", "seq": 1234, "packet_id": "…",
//   "received_at_utc": "2026-11-07T17:58:52.177715+00:00" }
//
// timestamp_ms is missing for the CSV-format firmware. temperature_c and
// resistance_ohm are null when the gateway couldn't convert the reading.

const sensor = z.looseObject({
  id: z.string().min(1).max(64),
  type: z.string().min(1).max(32), // "thermistor", "accelerometer", "gyroscope", ...
  valid: z.boolean().optional(),
  voltage_mv: z.number().nullable().optional(),
  temperature_c: z.number().nullable().optional(),
  x_g: z.number().nullable().optional(),
  y_g: z.number().nullable().optional(),
  z_g: z.number().nullable().optional(),
});

export const statusPacket = z.looseObject({
  machine_id: machineId,
  timestamp_ms: z.number().int().nonnegative().optional(),
  sensors: z.array(sensor).max(32),
  ...identity,
});
export type StatusPacket = z.infer<typeof statusPacket>;

// --- Vibration window: Xavion's analyze_window() output -----------------------
//
// Posted by the gateway once a chunked window is reassembled and analysed.
// temperature_c is the latest temperature, or null if there isn't one yet.

const axis = z.looseObject({
  dominant_freq_hz: z.number(),
  dominant_amp_g: z.number(),
  velocity_rms_mm_s: z.number(),
  band_power_g2: z.record(z.string(), z.number()),
  harmonics: z
    .array(
      z.looseObject({
        order: z.number().int(),
        freq_hz: z.number(),
        amp_g: z.number(),
      }),
    )
    .optional(),
});

export const vibrationWindow = z.looseObject({
  machine_id: machineId,
  window_id: z.number().int().nonnegative(),
  first_sample_ms: z.number().int().nonnegative().nullable().optional(),
  sample_rate_hz: z.number().positive(),
  n_samples: z.number().int().positive(),
  clipped_fraction: z.number().min(0).max(1).optional(),
  warnings: z.array(z.string()).optional(),
  axes: z.object({ x: axis, y: axis, z: axis }),
  temperature_c: z.number().nullable().optional(),
  ...identity,
});
export type VibrationWindow = z.infer<typeof vibrationWindow>;

// Every ingest route takes one packet or a JSON array of up to 100, so a
// gateway can send several at once over one HTTPS request (see lib/ingest.ts).
