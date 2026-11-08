import type { Prisma } from "@/generated/prisma/client";
import { db } from "@/lib/db";
import { notifyAlerts } from "@/lib/notify";
import { StreamBuffer, configFromEnv, type Batch } from "@/lib/stream";

// The one StreamBuffer for this server process, created on first use (like
// db()), so `next build` doesn't start a timer. Kept on globalThis so Next's
// dev server doesn't make a new one, and lose the held packets, on every edit.
const g = globalThis as unknown as { smolbugsStream?: StreamBuffer };

export function streamBuffer(): StreamBuffer {
  if (!g.smolbugsStream) {
    const buffer = new StreamBuffer(persistBatch, configFromEnv());
    buffer.start();
    flushOnShutdown(buffer);
    g.smolbugsStream = buffer;
  }
  return g.smolbugsStream;
}

// One transaction per write: either the whole batch lands or none of it does.
// skipDuplicates + the unique packetId mean a packet written before (e.g. by a
// server that restarted) is silently skipped instead of failing the batch.
async function persistBatch({ readings, events }: Batch): Promise<void> {
  await db().$transaction(async (tx) => {
    const lastSeen = new Map<string, Date>();
    for (const r of readings) {
      const prev = lastSeen.get(r.machineId);
      if (!prev || r.receivedAt > prev) lastSeen.set(r.machineId, r.receivedAt);
    }
    for (const e of events) if (!lastSeen.has(e.machineId)) lastSeen.set(e.machineId, e.at);
    for (const [id, at] of lastSeen) {
      await tx.machine.upsert({
        where: { id },
        create: { id, lastSeenAt: at },
        update: { lastSeenAt: at },
      });
    }

    if (readings.length > 0) {
      await tx.statusPacket.createMany({
        skipDuplicates: true,
        data: readings.map((r) => ({
          machineId: r.machineId,
          receivedAt: r.receivedAt, // the packet's own time, not the time of this write
          packetId: r.packetId,
          deviceTimeMs: r.deviceTimeMs === null ? null : BigInt(r.deviceTimeMs),
          bootId: r.bootId,
          seq: r.seq,
          thermistorMv: r.thermistorMv === null ? null : Math.round(r.thermistorMv),
          temperatureC: r.temperatureC,
          accelXG: r.accel?.x ?? null,
          accelYG: r.accel?.y ?? null,
          accelZG: r.accel?.z ?? null,
          abnormal: r.abnormal,
          flags: r.flags as Prisma.InputJsonValue,
          payload: r.payload as Prisma.InputJsonValue,
        })),
      });
    }

    // In order, so an alert opened and closed in the same batch ends up closed.
    for (const e of events) {
      if (e.type === "open") {
        await tx.alert.create({
          data: {
            machineId: e.machineId,
            createdAt: e.at,
            severity: e.severity,
            kind: e.kind,
            message: e.message,
          },
        });
      } else {
        await tx.alert.updateMany({
          where: { machineId: e.machineId, resolvedAt: null },
          data: { resolvedAt: e.at },
        });
      }
    }
  });
  notifyAlerts(events); // only once the transaction has committed
}

// The host stops the server with SIGTERM. Try to write what's held (5 s at most)
// before exiting; anything still unwritten after that is lost.
function flushOnShutdown(buffer: StreamBuffer): void {
  process.once("SIGTERM", () => {
    buffer.stop();
    const timeout = new Promise((resolve) => setTimeout(resolve, 5_000));
    void Promise.race([buffer.flush(), timeout]).finally(() => process.exit(0));
  });
}
