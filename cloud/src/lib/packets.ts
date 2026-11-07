import { z } from "zod";

// The packet formats the cloud accepts. Fields the team adds later are kept
// (loose objects) and stored in `payload`; only the fields below are checked.

const machineId = z
  .string()
  .trim()
  .min(1)
  .max(64)
  .regex(/^[A-Za-z0-9_.-]+$/, "machine_id may use letters, digits, _ . - only");

// --- Status packet: Kelvin's format, posted by the gateway or the device ----
//
// { "machine_id": "MG-001", "timestamp_ms": 96847,
//   "sensors": [ { "id": "sensor_0", "type": "thermistor", "voltage_mv": 1582 } ] }

const sensor = z.looseObject({
  id: z.string().min(1).max(64),
  type: z.string().min(1).max(32), // "thermistor", "accelerometer", ...
  voltage_mv: z.number().optional(),
  temperature_c: z.number().optional(), // if the gateway converted it
});

export const statusPacket = z.looseObject({
  machine_id: machineId,
  timestamp_ms: z.number().int().nonnegative().optional(),
  boot_id: z.string().max(64).optional(),
  seq: z.number().int().nonnegative().optional(),
  sensors: z.array(sensor).max(32),
});
export type StatusPacket = z.infer<typeof statusPacket>;

// --- Vibration window: Xavion's analyze_window() output -----------------------
//
// Posted by the gateway once a chunked window is reassembled and analysed.
// Optional extra: temperature_c, if the gateway knows the latest temperature.

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
  temperature_c: z.number().optional(),
});
export type VibrationWindow = z.infer<typeof vibrationWindow>;

// Every ingest route takes one packet or a JSON array of up to 100, so a
// gateway can send several at once over one HTTPS request (see lib/ingest.ts).
