// The buffered status endpoint (POST /api/v1/status), minus the web and
// database parts.
//
//   - The gateway (ingest/cloud.py) posts status packets, about 2 per second
//     per machine. Each one is checked against the limits below and held in memory.
//   - Every 30 s everything held is written to the database in one batch.
//   - Once a machine has broken a limit `confirmCount` times in a row, that
//     machine's held packets are written immediately and an alert is opened.
//     While the alert is open, every further abnormal packet is written
//     immediately too. The alert closes after `clearCount` normal packets in a row.
//   - The gateway retries with the same packet_id, so packets already seen are
//     skipped (here in memory, and by a unique index in the database).
//
// No Next, Prisma or zod imports on purpose: scripts/stream.test.mts runs this
// file with plain Node. The database write is passed in as `persist`
// (see lib/streamStore.ts).
//
// Limits: the buffer lives in memory, so it only works with ONE server process,
// and a restart loses up to 30 s of held packets. The gateway deletes packets as
// soon as this endpoint answers 200, so it can't resend them.

export type Vec3 = { x: number; y: number; z: number };

export type ThermistorConfig = {
  supplyMv: number;
  fixedResistorOhm: number;
  r0Ohm: number; // NTC resistance at t0C. UNVERIFIED, same as processing/temperature.py
  t0C: number;
  beta: number; // UNVERIFIED
  ntcOnLowSide: boolean; // 3.3 V -> fixed resistor -> ADC pin -> NTC -> GND
};

export type StreamConfig = {
  flushIntervalMs: number;
  confirmCount: number; // abnormal packets in a row before an alert opens
  clearCount: number; // normal packets in a row before it closes
  tempMaxC: number;
  tempRiseCPerMin: number;
  tempRiseWindowMs: number; // how far back to look for the rise rate
  shockToleranceG: number; // allowed |acceleration magnitude - 1 g| in one packet
  vibrationStdMaxG: number; // allowed spread of the last `vibrationWindow` readings
  vibrationWindow: number;
  maxBufferPerMachine: number; // oldest dropped beyond this if the database stays down
  thermistor: ThermistorConfig;
};

// Starting values only. Set the real ones from your own test runs (env vars below).
export const DEFAULT_CONFIG: StreamConfig = {
  flushIntervalMs: 30_000,
  confirmCount: 3,
  clearCount: 6,
  tempMaxC: 60,
  tempRiseCPerMin: 5,
  tempRiseWindowMs: 60_000,
  shockToleranceG: 0.5,
  vibrationStdMaxG: 0.15,
  vibrationWindow: 20,
  maxBufferPerMachine: 1200, // 10 min at 2 packets/s
  thermistor: {
    supplyMv: 3300,
    fixedResistorOhm: 75_000,
    r0Ohm: 100_000,
    t0C: 25,
    beta: 3950,
    ntcOnLowSide: true,
  },
};

export function configFromEnv(env: Record<string, string | undefined> = process.env): StreamConfig {
  const d = DEFAULT_CONFIG;
  const num = (name: string, fallback: number) => {
    const v = Number(env[name]);
    return env[name] !== undefined && env[name] !== "" && Number.isFinite(v) ? v : fallback;
  };
  return {
    flushIntervalMs: num("STREAM_FLUSH_MS", d.flushIntervalMs),
    confirmCount: Math.max(1, Math.round(num("STREAM_CONFIRM_COUNT", d.confirmCount))),
    clearCount: Math.max(1, Math.round(num("STREAM_CLEAR_COUNT", d.clearCount))),
    tempMaxC: num("TEMP_MAX_C", d.tempMaxC),
    tempRiseCPerMin: num("TEMP_RISE_C_PER_MIN", d.tempRiseCPerMin),
    tempRiseWindowMs: d.tempRiseWindowMs,
    shockToleranceG: num("SHOCK_TOLERANCE_G", d.shockToleranceG),
    vibrationStdMaxG: num("VIBRATION_STD_MAX_G", d.vibrationStdMaxG),
    vibrationWindow: d.vibrationWindow,
    maxBufferPerMachine: d.maxBufferPerMachine,
    thermistor: {
      supplyMv: num("THERMISTOR_SUPPLY_MV", d.thermistor.supplyMv),
      fixedResistorOhm: num("THERMISTOR_FIXED_OHM", d.thermistor.fixedResistorOhm),
      r0Ohm: num("THERMISTOR_R0_OHM", d.thermistor.r0Ohm),
      t0C: d.thermistor.t0C,
      beta: num("THERMISTOR_BETA", d.thermistor.beta),
      ntcOnLowSide: d.thermistor.ntcOnLowSide,
    },
  };
}

// --- Packets ------------------------------------------------------------------

// One status packet, already validated and unpacked by the route.
export type IncomingReading = {
  machineId: string;
  receivedAt: Date; // received_at_utc from the gateway, or the server clock
  deviceTimeMs: number | null; // timestamp_ms (device uptime)
  bootId: string | null;
  seq: number | null;
  packetId: string | null;
  thermistorMv: number | null;
  thermistorValid: boolean | null; // the firmware's "valid" flag, if sent
  temperatureC: number | null; // as converted by the gateway, if it did
  accel: Vec3 | null;
  accelValid: boolean | null;
  payload: unknown; // the packet exactly as received
};

export type StoredReading = IncomingReading & {
  flags: string[]; // which limits this packet broke; empty = normal
  abnormal: boolean; // part of a confirmed abnormal run
};

export type AlertEvent =
  | { type: "open"; machineId: string; at: Date; severity: "alert" | "watch"; kind: string; message: string }
  | { type: "resolve"; machineId: string; at: Date };

export type Batch = { readings: StoredReading[]; events: AlertEvent[] };
export type Persist = (batch: Batch) => Promise<void>;

export type AddResult = {
  duplicate: boolean;
  flags: string[];
  temperatureC: number | null;
  flushNow: boolean; // the caller should write this machine's packets right away
};

// --- Temperature ----------------------------------------------------------------

const KELVIN = 273.15;
const RAIL_MARGIN_MV = 10; // within 10 mV of 0 or supply = short or open circuit (as the firmware checks)

export function thermistorFault(mv: number, cfg: ThermistorConfig): boolean {
  return mv <= RAIL_MARGIN_MV || mv >= cfg.supplyMv - RAIL_MARGIN_MV;
}

// Beta equation, same as processing/temperature.py. null if the reading is unusable.
export function voltageToCelsius(mv: number, cfg: ThermistorConfig): number | null {
  if (!(mv > 0 && mv < cfg.supplyMv)) return null;
  const r = cfg.ntcOnLowSide
    ? (cfg.fixedResistorOhm * mv) / (cfg.supplyMv - mv)
    : (cfg.fixedResistorOhm * (cfg.supplyMv - mv)) / mv;
  const invT = 1 / (cfg.t0C + KELVIN) + Math.log(r / cfg.r0Ohm) / cfg.beta;
  return 1 / invT - KELVIN;
}

// --- Rules ----------------------------------------------------------------------

// Checks one packet. `history` holds the same machine's earlier packets, oldest first.
// Returns the temperature it used (the gateway's if it sent one, else converted here).
//
// The accelerometer gives one snapshot per packet, so vibration frequency can't be
// measured here (2 packets/s sees nothing above 1 Hz). What the snapshots do give:
//   shock:          one snapshot far from 1 g
//   vibration_high: the spread of recent snapshots, a rough estimate of vibration
//                   amplitude. It reads low if the machine vibrates at a whole
//                   multiple of 2 Hz, because then every snapshot lands on the same
//                   point of the cycle.
export function evaluate(
  reading: IncomingReading,
  history: readonly StoredReading[],
  cfg: StreamConfig,
): { temperatureC: number | null; flags: string[] } {
  const flags: string[] = [];
  let temperatureC: number | null = null;

  const mv = reading.thermistorMv;
  if (mv !== null || reading.thermistorValid === false) {
    if (reading.thermistorValid === false || (mv !== null && thermistorFault(mv, cfg.thermistor))) {
      flags.push("thermistor_fault");
    } else {
      temperatureC = reading.temperatureC ?? (mv === null ? null : voltageToCelsius(mv, cfg.thermistor));
      if (temperatureC !== null) {
        if (temperatureC > cfg.tempMaxC) flags.push("temp_high");
        const rise = riseRate(reading.receivedAt, temperatureC, history, cfg.tempRiseWindowMs);
        if (rise !== null && rise > cfg.tempRiseCPerMin) flags.push("temp_rising");
      }
    }
  }

  const a = reading.accel;
  if (reading.accelValid === false || (a !== null && a.x === 0 && a.y === 0 && a.z === 0)) {
    flags.push("accel_fault"); // a working sensor always reads ~1 g of gravity
  } else if (a !== null) {
    if (Math.abs(Math.hypot(a.x, a.y, a.z) - 1) > cfg.shockToleranceG) flags.push("shock");
    const recent = history
      .filter((h) => h.accel !== null && !h.flags.includes("accel_fault"))
      .slice(-(cfg.vibrationWindow - 1))
      .map((h) => h.accel as Vec3);
    recent.push(a);
    if (recent.length >= Math.min(10, cfg.vibrationWindow) && spread(recent) > cfg.vibrationStdMaxG) {
      flags.push("vibration_high");
    }
  }
  return { temperatureC, flags };
}

// °C per minute, against the oldest usable reading in the window (at least 10 s back).
function riseRate(at: Date, tempC: number, history: readonly StoredReading[], windowMs: number): number | null {
  const now = at.getTime();
  for (const h of history) {
    const dt = now - h.receivedAt.getTime();
    if (h.temperatureC === null || dt > windowMs) continue;
    if (dt < 10_000) return null;
    return (tempC - h.temperatureC) / (dt / 60_000);
  }
  return null;
}

// sqrt(var_x + var_y + var_z): the size of the movement around the average, in g.
function spread(v: Vec3[]): number {
  const n = v.length;
  const mean = v.reduce((m, p) => ({ x: m.x + p.x / n, y: m.y + p.y / n, z: m.z + p.z / n }), { x: 0, y: 0, z: 0 });
  const variance = v.reduce(
    (s, p) => s + ((p.x - mean.x) ** 2 + (p.y - mean.y) ** 2 + (p.z - mean.z) ** 2) / n,
    0,
  );
  return Math.sqrt(variance);
}

function describeAlert(flags: string[]): { kind: string; severity: "alert" | "watch"; message: string } {
  const set = new Set(flags);
  const kinds: string[] = [];
  if (set.has("temp_high") || set.has("temp_rising")) kinds.push("overheat");
  if (set.has("vibration_high") || set.has("shock")) kinds.push("vibration");
  if (set.has("thermistor_fault") || set.has("accel_fault")) kinds.push("sensor_fault");
  const onlyFault = kinds.length === 1 && kinds[0] === "sensor_fault";
  return {
    kind: kinds[0] ?? "abnormal",
    severity: onlyFault ? "watch" : "alert",
    message: `Outside limits: ${[...set].join(", ")}`,
  };
}

// The gateway's packet_id, or boot_id|seq if a sender has no packet_id. null: can't tell.
export function dedupeKey(r: IncomingReading): string | null {
  if (r.packetId) return r.packetId;
  if (r.bootId && r.seq !== null) return `${r.bootId}|${r.seq}`;
  return null;
}

// --- Buffer -----------------------------------------------------------------------

type MachineState = {
  buffer: StoredReading[]; // waiting for the next write
  events: AlertEvent[]; // alert opens/closes waiting for the next write
  history: StoredReading[]; // recent packets for the rules; survives writes
  seen: Set<string>; // recent dedupe keys
  seenOrder: string[];
  abnormalStreak: number;
  normalStreak: number;
  inAlert: boolean;
  dropped: number;
  lastFlushAt: Date | null;
  lastError: string | null;
};

const DUPLICATE_MEMORY = 2000; // > what the gateway can hold (its backlog is 2000)

export class StreamBuffer {
  readonly cfg: StreamConfig;
  readonly machines = new Map<string, MachineState>();
  private readonly persist: Persist;
  private chain: Promise<unknown> = Promise.resolve();
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(persist: Persist, cfg: StreamConfig = DEFAULT_CONFIG) {
    this.persist = persist;
    this.cfg = cfg;
  }

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => void this.flush(), this.cfg.flushIntervalMs);
    // Don't keep the process alive just for this. (Node's timer has unref(); the DOM type doesn't.)
    (this.timer as unknown as { unref?: () => void }).unref?.();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  add(r: IncomingReading): AddResult {
    const s = this.state(r.machineId);

    const key = dedupeKey(r);
    if (key !== null) {
      if (s.seen.has(key)) return { duplicate: true, flags: [], temperatureC: null, flushNow: false };
      s.seen.add(key);
      s.seenOrder.push(key);
      if (s.seenOrder.length > DUPLICATE_MEMORY) s.seen.delete(s.seenOrder.shift() as string);
    }

    const { temperatureC, flags } = evaluate(r, s.history, this.cfg);
    const stored: StoredReading = { ...r, temperatureC, flags, abnormal: false };

    s.history.push(stored);
    const historyMax = Math.max(this.cfg.vibrationWindow, Math.ceil(this.cfg.tempRiseWindowMs / 500) + 20);
    if (s.history.length > historyMax) s.history.splice(0, s.history.length - historyMax);

    s.buffer.push(stored);
    this.trim(s);

    if (flags.length > 0) {
      s.abnormalStreak++;
      s.normalStreak = 0;
    } else {
      s.normalStreak++;
      s.abnormalStreak = 0;
    }

    let flushNow = false;
    if (!s.inAlert && s.abnormalStreak >= this.cfg.confirmCount) {
      // Confirmed: mark the packets that built the streak and open an alert.
      s.inAlert = true;
      let marked = 0;
      for (let i = s.buffer.length - 1; i >= 0 && marked < this.cfg.confirmCount; i--) {
        if (s.buffer[i].flags.length > 0) {
          s.buffer[i].abnormal = true;
          marked++;
        }
      }
      s.events.push({ type: "open", machineId: r.machineId, at: r.receivedAt, ...describeAlert(flags) });
      flushNow = true;
    } else if (s.inAlert && flags.length > 0) {
      stored.abnormal = true;
      flushNow = true;
    } else if (s.inAlert && s.normalStreak >= this.cfg.clearCount) {
      s.inAlert = false;
      s.events.push({ type: "resolve", machineId: r.machineId, at: r.receivedAt });
    }

    return { duplicate: false, flags, temperatureC, flushNow };
  }

  // Writes the held packets (one machine, or all). Writes never overlap: each
  // waits for the one before it. Resolves to false if the write failed; the
  // packets are then kept and retried on the next write.
  flush(machineId?: string): Promise<boolean> {
    const run = this.chain.then(() => this.flushNow(machineId));
    this.chain = run.catch(() => undefined);
    return run;
  }

  stats() {
    return {
      flush_interval_ms: this.cfg.flushIntervalMs,
      machines: [...this.machines].map(([id, s]) => ({
        machine_id: id,
        buffered: s.buffer.length,
        pending_alert_events: s.events.length,
        in_alert: s.inAlert,
        dropped: s.dropped,
        last_flush_at: s.lastFlushAt?.toISOString() ?? null,
        last_error: s.lastError,
      })),
    };
  }

  private async flushNow(machineId?: string): Promise<boolean> {
    const ids = machineId === undefined ? [...this.machines.keys()] : [machineId];
    const taken: { s: MachineState; readings: StoredReading[]; events: AlertEvent[] }[] = [];
    for (const id of ids) {
      const s = this.machines.get(id);
      if (!s || (s.buffer.length === 0 && s.events.length === 0)) continue;
      taken.push({ s, readings: s.buffer.splice(0), events: s.events.splice(0) });
    }
    if (taken.length === 0) return true;

    try {
      await this.persist({
        readings: taken.flatMap((t) => t.readings),
        events: taken.flatMap((t) => t.events),
      });
      const now = new Date();
      for (const t of taken) {
        t.s.lastFlushAt = now;
        t.s.lastError = null;
      }
      return true;
    } catch (err) {
      // Put them back in front of anything that arrived during the write.
      for (const t of taken) {
        t.s.buffer.unshift(...t.readings);
        t.s.events.unshift(...t.events);
        t.s.lastError = err instanceof Error ? err.message : String(err);
        this.trim(t.s);
      }
      return false;
    }
  }

  private trim(s: MachineState): void {
    const extra = s.buffer.length - this.cfg.maxBufferPerMachine;
    if (extra > 0) {
      s.buffer.splice(0, extra);
      s.dropped += extra;
    }
  }

  private state(id: string): MachineState {
    let s = this.machines.get(id);
    if (!s) {
      s = {
        buffer: [],
        events: [],
        history: [],
        seen: new Set(),
        seenOrder: [],
        abnormalStreak: 0,
        normalStreak: 0,
        inAlert: false,
        dropped: 0,
        lastFlushAt: null,
        lastError: null,
      };
      this.machines.set(id, s);
    }
    return s;
  }
}
