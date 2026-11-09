import { connection } from "next/server";
import { activityConfigFromEnv, classifyActivity, type StoredSample } from "@/lib/activity";
import { db } from "@/lib/db";

// GET /api/v1/activity
// For every machine, from the DATABASE (not the server's memory): is it online,
// idle or offline, when was its last reading stored, and is it broken (an alert
// still open)? The dashboard polls this every few seconds. Rules: src/lib/activity.ts.
export async function GET() {
  await connection();
  const cfg = activityConfigFromEnv();
  const now = new Date();
  const since = new Date(now.getTime() - 60_000);

  const [machines, lastStored, recent, open] = await Promise.all([
    db().machine.findMany({ select: { id: true, name: true }, orderBy: { id: "asc" } }),
    db().statusPacket.groupBy({ by: ["machineId"], _max: { receivedAt: true } }),
    db().statusPacket.findMany({
      where: { receivedAt: { gte: since } },
      orderBy: [{ receivedAt: "desc" }, { id: "desc" }],
      take: 5000,
      select: { machineId: true, receivedAt: true, accelXG: true, accelYG: true, accelZG: true },
    }),
    db().alert.findMany({ where: { resolvedAt: null }, orderBy: [{ createdAt: "desc" }, { id: "desc" }] }),
  ]);

  const last = new Map(lastStored.map((g) => [g.machineId, g._max.receivedAt ?? null]));
  const samples = new Map<string, StoredSample[]>();
  for (const r of recent) {
    const list = samples.get(r.machineId) ?? [];
    list.push({ receivedAt: r.receivedAt, x: r.accelXG, y: r.accelYG, z: r.accelZG });
    samples.set(r.machineId, list);
  }

  return Response.json({
    checked_at: now.toISOString(),
    offline_after_ms: cfg.offlineAfterMs,
    idle_spread_max_g: cfg.idleSpreadMaxG,
    machines: machines.map((m) => {
      const a = classifyActivity(last.get(m.id) ?? null, samples.get(m.id) ?? [], now, cfg);
      const alert = open.find((x) => x.machineId === m.id) ?? null;
      return {
        machine_id: m.id,
        state: a.state,
        reason: a.reason,
        last_received_at: a.lastReceivedAt?.toISOString() ?? null,
        age_ms: a.ageMs,
        readings_last_minute: a.readingsLastMinute,
        vibration_spread_g: a.vibrationSpreadG,
        broken: alert
          ? {
              alert_id: alert.id,
              since: alert.createdAt.toISOString(),
              severity: alert.severity,
              kind: alert.kind,
              message: alert.message,
            }
          : null,
      };
    }),
  });
}
