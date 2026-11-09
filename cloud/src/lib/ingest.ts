import { z } from "zod";
import { checkDeviceKey } from "@/lib/auth";
import { db } from "@/lib/db";
import { vibrationWindow, type VibrationWindow } from "@/lib/packets";
import { WINDOW_FRESH_MS, runState } from "@/lib/runState";
import { streamBuffer } from "@/lib/streamStore";
import type { Prisma } from "@/generated/prisma/client";

type Tx = Prisma.TransactionClient;

// Shared handling for ingest routes that write straight to the database: check
// the key, parse JSON, validate, store inside one transaction, and answer with
// something a teammate can debug. (Status packets are buffered instead: see
// app/api/v1/status/route.ts.)
export async function ingest<T extends z.ZodType>(
  request: Request,
  schema: T,
  // Returns how many rows were actually written (repeats are skipped).
  store: (tx: Tx, packets: z.infer<T>[]) => Promise<number>,
  // Runs once the rows are committed. A failure here is logged, never turned into an error answer:
  // the data is already stored, and a retry would only be skipped as a duplicate.
  afterCommit?: (packets: z.infer<T>[]) => Promise<void>,
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
  const stored = await db().$transaction(async (tx) => {
    await touchMachines(tx, packets as { machine_id: string }[]);
    return store(tx, packets);
  });
  if (afterCommit) {
    try {
      await afterCommit(packets);
    } catch (err) {
      console.error(`after storing ${packets.length} packet(s): ${(err as Error).message}`);
    }
  }
  // A packet whose packet_id is already stored (the gateway retried after a
  // timeout that actually succeeded) is skipped and counted, not an error.
  return Response.json({ ok: true, stored, duplicates: packets.length - stored });
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

// Windows arrive every few seconds at most, so they're written straight away.
// skipDuplicates + the unique packetId: a gateway retry doesn't store a window twice.
export const windowRoute = (request: Request) =>
  ingest(request, vibrationWindow, async (tx, windows: VibrationWindow[]) => {
    const { count } = await tx.vibrationWindow.createMany({
      skipDuplicates: true,
      data: windows.map((w) => {
        const axes = [w.axes.x, w.axes.y, w.axes.z];
        const peak = axes.reduce((a, b) => (b.dominant_amp_g > a.dominant_amp_g ? b : a));
        return {
          machineId: w.machine_id,
          receivedAt: w.received_at_utc ? new Date(w.received_at_utc) : new Date(),
          packetId: w.packet_id ?? null,
          bootId: w.boot_id ?? null,
          seq: w.seq ?? null,
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
    return count;
  }, judgeWindows);

// Each fresh window goes to the alert logic (lib/stream.ts addWindow): a window far above normal
// running, twice in a row, opens a vibration alert. Windows more than WINDOW_FRESH_MS old (an old
// recording uploaded later, or a big clock difference) are stored but never alert.
async function judgeWindows(windows: VibrationWindow[]): Promise<void> {
  const buffer = streamBuffer();
  const now = Date.now();
  const urgent = new Set<string>();
  for (const w of windows) {
    const at = w.received_at_utc ? new Date(w.received_at_utc) : new Date();
    if (Math.abs(now - at.getTime()) > WINDOW_FRESH_MS) continue;
    const axes = [w.axes.x, w.axes.y, w.axes.z];
    const peak = axes.reduce((a, b) => (b.dominant_amp_g > a.dominant_amp_g ? b : a));
    const velocity = Math.max(...axes.map((a) => a.velocity_rms_mm_s));
    const abnormal = runState(velocity, peak.dominant_freq_hz) === "abnormal";
    if (buffer.addWindow(w.machine_id, at, abnormal, w.packet_id ?? null).flushNow) urgent.add(w.machine_id);
  }
  for (const id of urgent) await buffer.flush(id); // write the alert now, so the phone hears it now
}
