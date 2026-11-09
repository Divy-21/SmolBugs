// Is each machine online, idle or offline? Worked out from what is stored in the
// database, so it answers "is the database actually receiving this machine's data?"
//
//   offline  no reading stored for `offlineAfterMs` (default 15 s)
//   idle     readings are arriving, but the accelerometer is nearly still: the
//            spread of the last `window` readings is under `idleSpreadMaxG`
//   online   readings are arriving and the machine is moving (or there is no
//            accelerometer data to tell idle from running)
//
// No Next, Prisma or zod imports on purpose: scripts/activity.test.mts runs this
// file with plain Node. The route (app/api/v1/activity/route.ts) does the queries.
//
// Times are the gateway's received_at_utc. If the gateway laptop's clock is far
// behind the server's, a live machine can look offline: keep both clocks synced.

export type ActivityState = "online" | "idle" | "offline";

export type ActivityConfig = {
  offlineAfterMs: number;
  idleSpreadMaxG: number;
  window: number; // how many recent readings the idle check looks at
};

// Idle default: the real drill (MG-001) read about 0.01 g of spread while idle and
// 0.36 g or more while running (Sat Nov 7), so 0.05 g separates them with room.
export const DEFAULT_ACTIVITY: ActivityConfig = {
  offlineAfterMs: 15_000,
  idleSpreadMaxG: 0.05,
  window: 20,
};

export function activityConfigFromEnv(env: Record<string, string | undefined> = process.env): ActivityConfig {
  const num = (name: string, fallback: number) => {
    const v = Number(env[name]);
    return env[name] !== undefined && env[name] !== "" && Number.isFinite(v) && v > 0 ? v : fallback;
  };
  return {
    offlineAfterMs: num("OFFLINE_AFTER_S", DEFAULT_ACTIVITY.offlineAfterMs / 1000) * 1000,
    idleSpreadMaxG: num("IDLE_SPREAD_MAX_G", DEFAULT_ACTIVITY.idleSpreadMaxG),
    window: DEFAULT_ACTIVITY.window,
  };
}

export type StoredSample = {
  receivedAt: Date;
  x: number | null;
  y: number | null;
  z: number | null;
};

export type Activity = {
  state: ActivityState;
  lastReceivedAt: Date | null;
  ageMs: number | null; // since the last stored reading
  readingsLastMinute: number;
  vibrationSpreadG: number | null; // null: not enough accelerometer readings
  reason: string;
};

const MIN_SAMPLES = 5;

// `recent`: this machine's stored readings from about the last minute, any order.
export function classifyActivity(
  lastReceivedAt: Date | null,
  recent: StoredSample[],
  now: Date,
  cfg: ActivityConfig = DEFAULT_ACTIVITY,
): Activity {
  const minuteAgo = now.getTime() - 60_000;
  const readingsLastMinute = recent.filter((r) => r.receivedAt.getTime() >= minuteAgo).length;

  if (lastReceivedAt === null) {
    return { state: "offline", lastReceivedAt, ageMs: null, readingsLastMinute, vibrationSpreadG: null, reason: "Nothing stored yet" };
  }
  // A gateway clock slightly ahead of the server's gives a negative age: count it as just now.
  const ageMs = Math.max(0, now.getTime() - lastReceivedAt.getTime());
  if (ageMs > cfg.offlineAfterMs) {
    return {
      state: "offline",
      lastReceivedAt,
      ageMs,
      readingsLastMinute,
      vibrationSpreadG: null,
      reason: `No reading stored for ${Math.round(ageMs / 1000)} s`,
    };
  }

  const withAccel = recent
    .filter((r) => r.x !== null && r.y !== null && r.z !== null && !(r.x === 0 && r.y === 0 && r.z === 0))
    .sort((a, b) => b.receivedAt.getTime() - a.receivedAt.getTime())
    .slice(0, cfg.window);
  const spread = withAccel.length >= MIN_SAMPLES ? spreadOf(withAccel) : null;

  if (spread === null) {
    return { state: "online", lastReceivedAt, ageMs, readingsLastMinute, vibrationSpreadG: null, reason: "Receiving data; no accelerometer readings to tell if it's running" };
  }
  if (spread < cfg.idleSpreadMaxG) {
    return { state: "idle", lastReceivedAt, ageMs, readingsLastMinute, vibrationSpreadG: spread, reason: "Receiving data; the machine isn't moving" };
  }
  return { state: "online", lastReceivedAt, ageMs, readingsLastMinute, vibrationSpreadG: spread, reason: "Receiving data; the machine is running" };
}

// sqrt(var_x + var_y + var_z), in g: the same measure the alert limits use.
function spreadOf(s: StoredSample[]): number {
  const n = s.length;
  let mx = 0, my = 0, mz = 0;
  for (const r of s) {
    mx += (r.x as number) / n;
    my += (r.y as number) / n;
    mz += (r.z as number) / n;
  }
  let v = 0;
  for (const r of s) v += (((r.x as number) - mx) ** 2 + ((r.y as number) - my) ** 2 + ((r.z as number) - mz) ** 2) / n;
  return Math.sqrt(v);
}
