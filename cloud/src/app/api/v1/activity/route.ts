import { connection } from "next/server";
import { activityConfigFromEnv, classifyActivity, type StoredSample } from "@/lib/activity";
import { db } from "@/lib/db";
import { readModel } from "@/lib/packets";
import { DEFAULT_RUN_PROFILE, RUN_STATE_TEXT, WINDOW_FRESH_MS, runState } from "@/lib/runState";

// GET /api/v1/activity
// For every machine, from the DATABASE (not the server's memory): is it online,
// idle or offline, when was its last reading stored, and is it broken (an alert
// still open)? The dashboard polls this every few seconds. Rules: src/lib/activity.ts.
// run_state (off / handled / running / abnormal) comes from the latest vibration window,
// null if there is none from the last 30 s. Rules: src/lib/runState.ts.
// model is the trained model's answer from that same window (the gateway runs it), null when the
// window is older than 30 s or the gateway isn't running the model. Shape: lib/packets.ts modelAnswer.
export async function GET() {
  await connection();
  const cfg = activityConfigFromEnv();
  const now = new Date();
  const since = new Date(now.getTime() - 60_000);

  const [machines, lastStored, recent, open, windows] = await Promise.all([
    db().machine.findMany({ select: { id: true, name: true }, orderBy: { id: "asc" } }),
    db().statusPacket.groupBy({ by: ["machineId"], _max: { receivedAt: true } }),
    db().statusPacket.findMany({
      where: { receivedAt: { gte: since } },
      orderBy: [{ receivedAt: "desc" }, { id: "desc" }],
      take: 5000,
      select: { machineId: true, receivedAt: true, accelXG: true, accelYG: true, accelZG: true },
    }),
    db().alert.findMany({ where: { resolvedAt: null }, orderBy: [{ createdAt: "desc" }, { id: "desc" }] }),
    db().vibrationWindow.findMany({
      distinct: ["machineId"],
      orderBy: [{ machineId: "asc" }, { receivedAt: "desc" }, { id: "desc" }],
      select: { machineId: true, receivedAt: true, velocityRmsMmS: true, dominantFreqHz: true, payload: true },
    }),
  ]);
  const latestWindow = new Map(windows.map((w) => [w.machineId, w]));

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
    abnormal_above_mm_s: DEFAULT_RUN_PROFILE.abnormalAboveMmS, // the vibration alert line, for charts
    machines: machines.map((m) => {
      const a = classifyActivity(last.get(m.id) ?? null, samples.get(m.id) ?? [], now, cfg);
      const alert = open.find((x) => x.machineId === m.id) ?? null;
      const w = latestWindow.get(m.id);
      const fresh = w && w.velocityRmsMmS !== null && w.dominantFreqHz !== null &&
        now.getTime() - w.receivedAt.getTime() <= WINDOW_FRESH_MS;
      const run = fresh ? runState(w.velocityRmsMmS as number, w.dominantFreqHz as number) : null;
      return {
        machine_id: m.id,
        state: a.state,
        reason: a.reason,
        last_received_at: a.lastReceivedAt?.toISOString() ?? null,
        age_ms: a.ageMs,
        readings_last_minute: a.readingsLastMinute,
        vibration_spread_g: a.vibrationSpreadG,
        run_state: run,
        run_state_text: run ? RUN_STATE_TEXT[run] : null,
        vibration_mm_s: w?.velocityRmsMmS ?? null, // latest window, however old (see window_at)
        main_freq_hz: w?.dominantFreqHz ?? null,
        window_at: w?.receivedAt.toISOString() ?? null,
        model: fresh ? readModel(w.payload) : null,
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
