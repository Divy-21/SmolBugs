import { z } from "zod";
import { checkDeviceKey } from "@/lib/auth";
import { db } from "@/lib/db";
import {
  statusPacket,
  vibrationWindow,
  type StatusPacket,
  type VibrationWindow,
} from "@/lib/packets";
import type { Prisma } from "@/generated/prisma/client";

type Tx = Prisma.TransactionClient;

// Shared handling for every ingest route: check the key, parse JSON, validate,
// store inside one transaction, and answer with something a teammate can debug.
export async function ingest<T extends z.ZodType>(
  request: Request,
  schema: T,
  store: (tx: Tx, packets: z.infer<T>[]) => Promise<void>,
): Promise<Response> {
  const denied = checkDeviceKey(request);
  if (denied) return denied;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json(
      { ok: false, error: "Body is not valid JSON (NaN and Infinity aren't JSON either)" },
      { status: 400 },
    );
  }

  // Validate against the single or the batch schema directly, not a union, so a
  // bad packet gets the exact field that's wrong rather than "Invalid input".
  const parsed = (Array.isArray(body) ? z.array(schema).min(1).max(100) : schema).safeParse(body);
  if (!parsed.success) {
    return Response.json(
      { ok: false, error: "Packet doesn't match the format", details: z.prettifyError(parsed.error) },
      { status: 400 },
    );
  }

  const packets = (Array.isArray(parsed.data) ? parsed.data : [parsed.data]) as z.infer<T>[];
  await db().$transaction(async (tx) => {
    await touchMachines(tx, packets as { machine_id: string }[]);
    await store(tx, packets);
  });
  return Response.json({ ok: true, stored: packets.length });
}

// A machine row appears the first time a packet names it.
async function touchMachines(tx: Tx, packets: { machine_id: string }[]) {
  const now = new Date();
  for (const id of new Set(packets.map((p) => p.machine_id))) {
    await tx.machine.upsert({
      where: { id },
      create: { id, lastSeenAt: now },
      update: { lastSeenAt: now },
    });
  }
}

export const statusRoute = (request: Request) =>
  ingest(request, statusPacket, async (tx, packets: StatusPacket[]) => {
    await tx.statusPacket.createMany({
      data: packets.map((p) => {
        const thermistor = p.sensors.find((s) => s.type === "thermistor");
        return {
          machineId: p.machine_id,
          deviceTimeMs: p.timestamp_ms === undefined ? null : BigInt(p.timestamp_ms),
          bootId: p.boot_id ?? null,
          seq: p.seq ?? null,
          thermistorMv:
            thermistor?.voltage_mv === undefined ? null : Math.round(thermistor.voltage_mv),
          temperatureC: thermistor?.temperature_c ?? null,
          payload: p as Prisma.InputJsonValue,
        };
      }),
    });
  });

export const windowRoute = (request: Request) =>
  ingest(request, vibrationWindow, async (tx, windows: VibrationWindow[]) => {
    await tx.vibrationWindow.createMany({
      data: windows.map((w) => {
        const axes = [w.axes.x, w.axes.y, w.axes.z];
        const peak = axes.reduce((a, b) => (b.dominant_amp_g > a.dominant_amp_g ? b : a));
        return {
          machineId: w.machine_id,
          windowId: w.window_id,
          firstSampleMs: w.first_sample_ms == null ? null : BigInt(w.first_sample_ms),
          sampleRateHz: w.sample_rate_hz,
          nSamples: w.n_samples,
          clippedFraction: w.clipped_fraction ?? null,
          dominantFreqHz: peak.dominant_freq_hz,
          dominantAmpG: peak.dominant_amp_g,
          velocityRmsMmS: Math.max(...axes.map((a) => a.velocity_rms_mm_s)),
          temperatureC: w.temperature_c ?? null,
          warnings: (w.warnings ?? []) as Prisma.InputJsonValue,
          axes: w.axes as Prisma.InputJsonValue,
          payload: w as Prisma.InputJsonValue,
        };
      }),
    });
  });
