"use client";

// MachineGuard machine-condition dashboard.
//
// Reads only the cloud's own API:
//   GET /api/v1/live      every 1 s   last ~70 s per machine, from memory (live view)
//   GET /api/v1/machines  every 10 s  names, kinds, latest stored FFT window
//   GET /api/v1/alerts    every 5 s   event history
//   GET /api/v1/activity  every 3 s   online / idle / offline and broken, from the database
//   GET /api/v1/machines/{id}/readings?minutes=10   every 10 s   10-minute trend, while shown (database)
//
// Health is worked out here from what the endpoint already decided (flags,
// open alerts) and from how old the last packet is, so "the machine is fine"
// and "we've stopped hearing from the machine" never look the same.

import { useEffect, useMemo, useState, type ReactNode } from "react";

// ---------------------------------------------------------------- API shapes

type Vec3 = { x: number; y: number; z: number };

type LivePoint = {
  t: string;
  temperature_c: number | null;
  accel: Vec3 | null;
  thermistor_valid: boolean | null;
  accel_valid: boolean | null;
  flags: string[];
  abnormal: boolean;
};

type LiveMachine = {
  machine_id: string;
  limits?: Limits; // this machine's own limits (some machines override the shared ones)
  in_alert: boolean;
  buffered: number;
  dropped: number;
  last_flush_at: string | null;
  last_error: string | null;
  recent: LivePoint[];
};

type Limits = {
  temp_max_c: number;
  temp_rise_c_per_min: number;
  shock_tolerance_g: number;
  vibration_std_max_g: number;
  vibration_window: number;
  confirm_count: number;
  flush_interval_ms: number;
};

type Live = { limits: Limits; machines: LiveMachine[] };

type MachineRow = {
  id: string;
  name: string | null;
  kind: string | null;
  rpm: number | null;
  latestWindow: {
    receivedAt: string;
    dominantFreqHz: number | null;
    dominantAmpG: number | null;
    velocityRmsMmS: number | null;
  } | null;
};

type AlertRow = {
  id: number;
  machineId: string;
  createdAt: string;
  severity: "alert" | "watch";
  kind: string;
  message: string;
  resolvedAt: string | null;
};

// From GET /api/v1/activity: what the DATABASE says about each machine.
type ActivityState = "online" | "idle" | "offline";

type Broken = { alert_id: number; since: string; severity: "alert" | "watch"; kind: string; message: string };

type ActivityRow = {
  machine_id: string;
  state: ActivityState;
  reason: string;
  last_received_at: string | null;
  age_ms: number | null;
  readings_last_minute: number;
  vibration_spread_g: number | null;
  broken: Broken | null;
};

type ActivityResp = { checked_at: string; offline_after_ms: number; idle_spread_max_g: number; machines: ActivityRow[] };

type StatusRow = { receivedAt: string; temperatureC: number | null; abnormal?: boolean; flags?: string[] | null };

type Point = { t: number; v: number };

// ---------------------------------------------------------------- data

function usePoll<T>(url: string | null, everyMs: number): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!url) return;
    let stopped = false;
    const load = async () => {
      try {
        const res = await fetch(url, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = (await res.json()) as T;
        if (!stopped) {
          setData(body);
          setError(null);
        }
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : String(err));
      }
    };
    void load();
    const timer = setInterval(load, everyMs);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [url, everyMs]);
  return { data, error };
}

// Starts at 0 and is set after mounting: reading the clock while Next prerenders
// the page is an error with cacheComponents, and would mismatch on hydration.
function useNow(everyMs = 1000): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

// ---------------------------------------------------------------- health

type Health = "critical" | "warning" | "healthy" | "offline" | "nodata";

const STALE_MS = 10_000; // no packet for 10 s: the link is down, not the machine

// Is the database receiving this machine's data? Online/idle/offline is the one
// status every machine always has; "broken" is shown on top of it, in its own space.
const STATE: Record<ActivityState, { rank: number; label: string; text: string; dot: string; sideDot: string }> = {
  online: { rank: 0, label: "Online", text: "text-emerald-700", dot: "bg-emerald-500", sideDot: "bg-emerald-400" },
  idle: { rank: 1, label: "Idle", text: "text-steel", dot: "bg-steel", sideDot: "bg-sky-300" },
  offline: { rank: 2, label: "Offline", text: "text-navy/70", dot: "bg-navy/40", sideDot: "bg-cool/40" },
};

const BROKEN_TITLE: Record<string, string> = {
  overheat: "Overheating",
  vibration: "Abnormal vibration",
  sensor_fault: "Sensor not responding",
};

function brokenDetail(b: Broken): string {
  const flags = b.message.replace(/^Outside limits:\s*/, "").split(/,\s*/).filter(Boolean);
  const words = flags.map((f) => FLAG_TEXT[f] ?? f);
  return words.length ? capitalise(words.join(", ")) : b.message;
}

const FAULT_FLAGS = new Set(["thermistor_fault", "accel_fault"]);

const FLAG_TEXT: Record<string, string> = {
  temp_high: "temperature above limit",
  temp_rising: "temperature rising fast",
  shock: "a shock",
  vibration_high: "vibration above limit",
  thermistor_fault: "temperature sensor not responding",
  accel_fault: "accelerometer not responding",
};

const KIND_TEXT: Record<string, string> = {
  drill: "Drill",
  band_saw: "Band saw",
  lathe: "Lathe",
  compressor: "Compressor",
  mill: "Milling machine",
  grinder: "Grinder",
};

function kindText(info: MachineRow | null): string | null {
  if (!info?.kind) return null;
  const k = KIND_TEXT[info.kind] ?? info.kind;
  return info.name && info.name.toLowerCase() === k.toLowerCase() ? null : k;
}

function healthOf(live: LiveMachine | undefined, openAlerts: AlertRow[], now: number): Health {
  const last = live?.recent.at(-1);
  if (!last) return "nodata";
  if (now - Date.parse(last.t) > STALE_MS) return "offline";
  const machineAlert = openAlerts.some((a) => a.severity === "alert");
  if (machineAlert || (live?.in_alert && last.flags.some((f) => !FAULT_FLAGS.has(f)))) return "critical";
  if (live?.in_alert || last.flags.length > 0 || openAlerts.length > 0) return "warning";
  return "healthy";
}

function summary(health: Health, last: LivePoint | undefined, openAlerts: AlertRow[]): string {
  switch (health) {
    case "nodata":
      return "No readings since the server started.";
    case "offline":
      return "Readings have stopped arriving. Check the sensor node and the gateway.";
    case "healthy":
      return "All readings are within the configured limits.";
    default: {
      const flags = (last?.flags ?? []).map((f) => FLAG_TEXT[f] ?? f);
      if (flags.length > 0) {
        const what = flags.length > 1 ? `${flags.slice(0, -1).join(", ")} and ${flags.at(-1)}` : flags[0];
        return health === "critical" ? `${capitalise(what)}. Inspect before the next run.` : `${capitalise(what)}.`;
      }
      return openAlerts.length ? "Back within limits. The alert closes once readings stay normal." : "";
    }
  }
}

// ---------------------------------------------------------------- metrics

// Spread of recent accelerometer snapshots (sqrt of summed per-axis variance), in g.
// The same measure the endpoint checks. Relative: the accelerometer isn't calibrated,
// and 2 snapshots/s can't show vibration frequency.
function spreadSeries(points: LivePoint[], window: number): Point[] {
  const out: Point[] = [];
  const acc: Vec3[] = [];
  for (const p of points) {
    if (!p.accel || p.flags.includes("accel_fault")) continue;
    acc.push(p.accel);
    if (acc.length > window) acc.shift();
    if (acc.length < Math.min(10, window)) continue;
    const n = acc.length;
    const m = acc.reduce((s, a) => ({ x: s.x + a.x / n, y: s.y + a.y / n, z: s.z + a.z / n }), { x: 0, y: 0, z: 0 });
    const v = acc.reduce((s, a) => s + ((a.x - m.x) ** 2 + (a.y - m.y) ** 2 + (a.z - m.z) ** 2) / n, 0);
    out.push({ t: Date.parse(p.t), v: Math.sqrt(v) });
  }
  return out;
}

function tempSeries(points: LivePoint[]): Point[] {
  return points.filter((p) => p.temperature_c !== null).map((p) => ({ t: Date.parse(p.t), v: p.temperature_c as number }));
}

// What to look at first for each alert type (the "recommended action" competitors attach to alerts).
const SUGGESTED_CHECK: Record<string, string> = {
  vibration: "Check for loose mounts, imbalance or misalignment",
  overheat: "Check cooling, lubrication and load",
  sensor_fault: "Check the sensor's mounting and wiring",
};

// ---------------------------------------------------------------- page

export default function Dashboard() {
  const now = useNow();
  const live = usePoll<Live>("/api/v1/live", 1000);
  const machines = usePoll<{ machines: MachineRow[] }>("/api/v1/machines", 10_000);
  const alerts = usePoll<{ alerts: AlertRow[] }>("/api/v1/alerts?limit=100", 5000);
  const activity = usePoll<ActivityResp>("/api/v1/activity", 3000);
  const [picked, setPicked] = useState<string | null>(null); // null: the overview

  const liveOf = (id: string) => live.data?.machines.find((m) => m.machine_id === id);
  const infoOf = (id: string) => machines.data?.machines.find((m) => m.id === id) ?? null;
  const alertsOf = (id: string) => (alerts.data?.alerts ?? []).filter((a) => a.machineId === id);
  const activityOf = (id: string) => activity.data?.machines.find((m) => m.machine_id === id) ?? null;
  const limitsOf = (id: string) => liveOf(id)?.limits ?? live.data?.limits ?? null;

  const ids = useMemo(() => {
    const set = new Set<string>();
    for (const m of live.data?.machines ?? []) set.add(m.machine_id);
    for (const m of machines.data?.machines ?? []) set.add(m.id);
    for (const m of activity.data?.machines ?? []) set.add(m.machine_id);
    return [...set];
  }, [live.data, machines.data, activity.data]);

  // Broken first, then online, idle, offline: the machines that need someone come first.
  const rows: FleetRow[] = ids
    .map((id) => {
      const a = activityOf(id);
      return { id, state: a?.state ?? null, broken: a?.broken ?? null, warning: healthOf(liveOf(id), alertsOf(id).filter((x) => !x.resolvedAt), now) === "warning" };
    })
    .sort(
      (a, b) =>
        Number(b.broken !== null) - Number(a.broken !== null) ||
        (a.state ? STATE[a.state].rank : 3) - (b.state ? STATE[b.state].rank : 3) ||
        a.id.localeCompare(b.id),
    );

  const selected = picked && ids.includes(picked) ? picked : null;
  const apiDown = live.error !== null && activity.error !== null;
  const noStatus = activity.error ? "Unknown" : activity.data ? "Nothing stored" : "Checking";

  return (
    <div className="min-h-screen bg-cool text-navy lg:flex">
      <Sidebar rows={rows} selected={selected} onSelect={setPicked} infoOf={infoOf} noStatus={noStatus} />

      <main className="min-w-0 flex-1 space-y-6 px-5 py-6 lg:px-8">
        {apiDown && (
          <p role="alert" className="rounded-md bg-amber px-4 py-2 text-sm font-medium text-navy">
            Server not reachable. Showing the last data received.
          </p>
        )}
        {activity.error && !apiDown && (
          <p role="alert" className="rounded-md bg-amber px-4 py-2 text-sm text-navy">
            Database status unavailable ({activity.error}).
          </p>
        )}

        {selected ? (
          <MachineView
            key={selected}
            id={selected}
            now={now}
            live={liveOf(selected)}
            limits={limitsOf(selected)}
            info={infoOf(selected)}
            alerts={alertsOf(selected)}
            activity={activityOf(selected)}
            noStatus={noStatus}
            onBack={() => setPicked(null)}
          />
        ) : (
          <Overview
            rows={rows}
            now={now}
            infoOf={infoOf}
            liveOf={liveOf}
            limitsOf={limitsOf}
            alerts={alerts.data?.alerts ?? []}
            onSelect={setPicked}
            noStatus={noStatus}
          />
        )}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------- sidebar

type FleetRow = { id: string; state: ActivityState | null; broken: Broken | null; warning: boolean };

function Sidebar(props: {
  rows: FleetRow[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  infoOf: (id: string) => MachineRow | null;
  noStatus: string;
}) {
  const { rows, selected, onSelect, infoOf, noStatus } = props;
  const itemClass = (on: boolean) =>
    `flex w-full items-center justify-between gap-3 px-5 py-2.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-amber ${
      on ? "bg-cool/10 shadow-[inset_3px_0_0_var(--color-cool)]" : "hover:bg-cool/5"
    }`;
  return (
    <aside className="flex flex-col bg-navy text-cool lg:sticky lg:top-0 lg:h-screen lg:w-64 lg:shrink-0">
      <div className="border-b-2 border-amber px-5 pt-5 pb-4">
        <p className="text-[28px] leading-none font-normal tracking-[-0.01em]">MachineGuard</p>
      </div>

      <nav aria-label="Machines" className="flex-1 overflow-y-auto py-3 max-lg:max-h-80">
        <button onClick={() => onSelect(null)} aria-current={selected === null ? "page" : undefined} className={itemClass(selected === null)}>
          <span className="text-[15px]">Overview</span>
          <span className="text-xs text-cool/75">{rows.length}</span>
        </button>

        <p className="px-5 pt-4 pb-2 text-xs text-cool/70">Machines</p>
        {rows.length === 0 && <p className="px-5 text-sm text-cool/75">None yet</p>}
        <ul>
          {rows.map(({ id, state, broken }) => {
            const info = infoOf(id);
            return (
              <li key={id}>
                <button onClick={() => onSelect(id)} aria-current={id === selected ? "page" : undefined} className={itemClass(id === selected)}>
                  <span className="min-w-0">
                    <span className="block truncate text-[15px]">{info?.name ?? id}</span>
                    <span className="flex items-center gap-1.5 text-xs text-cool/75">
                      <span className={`h-1.5 w-1.5 rounded-full ${state ? STATE[state].sideDot : "bg-cool/30"}`} aria-hidden />
                      {state ? STATE[state].label : noStatus}
                      {broken && <span className="ml-1 rounded-sm bg-amber px-1 text-[10px] font-semibold text-navy">Broken</span>}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}

// ---------------------------------------------------------------- overview

function Overview(props: {
  rows: FleetRow[];
  now: number;
  infoOf: (id: string) => MachineRow | null;
  liveOf: (id: string) => LiveMachine | undefined;
  limitsOf: (id: string) => Limits | null;
  alerts: AlertRow[];
  onSelect: (id: string) => void;
  noStatus: string;
}) {
  const { rows, now, infoOf, liveOf, limitsOf, alerts, onSelect, noStatus } = props;
  if (rows.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-line bg-white px-6 py-12 text-center text-sm text-navy/70">
        No machines yet. Start the gateway or run <code className="rounded bg-cool px-1 py-0.5">npm run fake-fleet</code>.
      </p>
    );
  }
  const open = alerts
    .filter((a) => a.resolvedAt === null)
    .sort((a, b) => Number(b.severity === "alert") - Number(a.severity === "alert") || a.createdAt.localeCompare(b.createdAt));
  const offline = rows.filter((r) => r.state === "offline").length;
  const attention = rows.filter((r) => r.broken).length;

  return (
    <div className="space-y-8">
      {/* One sentence instead of a row of number cards */}
      <header>
        <h1 className="text-4xl leading-[1.1] font-normal tracking-[-0.02em] sm:text-5xl">
          {attention === 0
            ? "All machines within limits"
            : `${attention} of ${rows.length} ${rows.length === 1 ? "machine" : "machines"} ${attention === 1 ? "needs" : "need"} attention`}
        </h1>
        <p className="mt-3 text-lg text-navy/70">
          {rows.length} monitored
          {offline > 0 && <>, {offline} not reporting</>}
        </p>
      </header>

      {/* Action items: laid out like the deck's FAULT REPORT card, the one place Roboto Mono is used */}
      {open.length > 0 && (
        <section aria-labelledby="actions-heading" className="rounded-lg bg-white font-mono ring-2 ring-amber">
          <h2 id="actions-heading" className="border-b border-line/70 px-5 py-3 text-[15px] font-bold tracking-[0.07em] uppercase">
            Action items
          </h2>
          <ul className="divide-y divide-line/60">
            {open.map((a) => (
              <li key={a.id}>
                <button
                  onClick={() => onSelect(a.machineId)}
                  className="grid w-full gap-x-6 gap-y-3 px-5 py-4 text-left hover:bg-cool/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-navy sm:grid-cols-2 xl:grid-cols-[minmax(140px,1fr)_minmax(160px,1fr)_minmax(0,2fr)_minmax(0,2fr)]"
                >
                  <ReportField label="Machine" value={infoOf(a.machineId)?.name ?? a.machineId} note={`since ${clock(a.createdAt)}`} />
                  <ReportField label="Fault" value={eventTitle(a)} strong />
                  <ReportField label="Sign" value={eventDetail(a)} />
                  <ReportField label="Do" value={SUGGESTED_CHECK[a.kind] ?? "Inspect the machine"} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* The machine board: the one bold element on the page */}
      <section aria-label="Machines">
        <ul className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
          {rows.map((r) => (
            <li key={r.id}>
              <MachineTile
                row={r}
                now={now}
                info={infoOf(r.id)}
                live={liveOf(r.id)}
                limits={limitsOf(r.id)}
                noStatus={noStatus}
                onSelect={() => onSelect(r.id)}
              />
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

// One labelled field of the action-item report (deck: 13 pt caps label, 14 pt value).
function ReportField(props: { label: string; value: string; strong?: boolean; note?: string }) {
  return (
    <span className="block">
      <span className="block text-[13px] tracking-[0.04em] text-navy/70 uppercase">{props.label}</span>
      <span className={`block text-sm ${props.strong ? "font-bold text-amber-ink" : ""}`}>{props.value}</span>
      {props.note && <span className="block text-[13px] text-navy/70">{props.note}</span>}
    </span>
  );
}

// One machine on the board. A broken machine gets an amber strip with when it broke,
// the fault in amber and what to check; an offline one fades to a dashed outline,
// so a silent sensor never looks like a healthy machine.
function MachineTile(props: {
  row: FleetRow;
  now: number;
  info: MachineRow | null;
  live: LiveMachine | undefined;
  limits: Limits | null;
  noStatus: string;
  onSelect: () => void;
}) {
  const { row, now, info, live, limits, noStatus, onSelect } = props;
  const pts = live?.recent ?? [];
  const vib = spreadSeries(pts, limits?.vibration_window ?? 20);
  const temp = tempSeries(pts).at(-1)?.v;
  const vibNow = vib.at(-1)?.v;
  const last = pts.at(-1);
  const broken = row.broken;
  const offline = row.state === "offline" || row.state === null;
  const tempOver = limits !== null && temp !== undefined && temp > limits.temp_max_c;
  const vibOver = limits !== null && vibNow !== undefined && vibNow > limits.vibration_std_max_g;

  const condition = broken
    ? (BROKEN_TITLE[broken.kind] ?? "Broken")
    : offline
      ? "No signal"
      : row.warning
        ? "Outside a limit"
        : row.state === "idle"
          ? "Idle"
          : "Running";
  const conditionTone = broken || row.warning ? "text-amber-ink" : row.state === "online" ? "text-emerald-700" : row.state === "idle" ? "text-steel" : "text-navy/70";
  const surface = broken
    ? "bg-white ring-2 ring-amber"
    : offline
      ? "border border-dashed border-navy/25 hover:bg-white/50"
      : "bg-white hover:ring-1 hover:ring-line";

  return (
    <button
      onClick={onSelect}
      className={`flex h-full w-full flex-col overflow-hidden rounded-lg text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy ${surface}`}
    >
      {broken && (
        <span className="flex items-center justify-between gap-4 bg-amber px-5 py-1.5 text-sm text-navy">
          <span className="font-bold">Broken</span>
          <span className="text-xs">since {clock(broken.since)}</span>
        </span>
      )}

      <span className="flex flex-1 flex-col p-5">
        <span className="flex items-start justify-between gap-4">
          <span className="min-w-0">
            <span className="block truncate text-xl font-normal">{info?.name ?? row.id}</span>
            <span className="block text-xs text-navy/70">{row.id}</span>
          </span>
          <span className={`flex shrink-0 items-center gap-1.5 text-sm ${row.state ? STATE[row.state].text : "text-navy/70"}`}>
            <span className={`h-2 w-2 rounded-full ${row.state ? STATE[row.state].dot : "bg-navy/25"}`} aria-hidden />
            {row.state ? STATE[row.state].label : noStatus}
          </span>
        </span>

        <span className="mt-4 flex items-end justify-between gap-4">
          <span className="min-w-0">
            <span className={`block text-2xl leading-tight font-normal tracking-[-0.01em] ${conditionTone}`}>{condition}</span>
            {broken && SUGGESTED_CHECK[broken.kind] && (
              <span className="mt-1 block text-[15px] text-navy/80">{SUGGESTED_CHECK[broken.kind]}</span>
            )}
          </span>
          <span className="w-28 shrink-0">
            {vib.length > 1 ? (
              <TileTrace points={vib} limit={limits?.vibration_std_max_g ?? null} />
            ) : (
              <span className="block text-right text-xs text-navy/70">No vibration data</span>
            )}
          </span>
        </span>

        <span className="mt-auto pt-4">
          <span className="grid grid-cols-3 gap-4 border-t border-line/70 pt-3">
            <TileStat label="Temperature" value={temp === undefined ? "–" : `${temp.toFixed(1)} °C`} over={tempOver} />
            <TileStat label="Vibration" value={vibNow === undefined ? "–" : `${vibNow.toFixed(3)} g`} over={vibOver} />
            <TileStat label="Updated" value={last ? `${ago(now, last.t)} ago` : "never"} align="right" />
          </span>
        </span>
      </span>
    </button>
  );
}

function TileStat(props: { label: string; value: string; over?: boolean; align?: "right" }) {
  return (
    <span className={props.align === "right" ? "text-right" : ""}>
      <span className="block text-xs text-navy/70">{props.label}</span>
      <span className={`block text-[15px] tabular-nums ${props.over ? "font-medium text-amber-ink" : ""}`}>
        {props.value}
        {props.over && <span className="sr-only"> (over limit)</span>}
      </span>
    </span>
  );
}

// The last minute of vibration, drawn small, with the limit as a dashed line.
function TileTrace({ points, limit }: { points: Point[]; limit: number | null }) {
  const W = 112;
  const H = 40;
  const s = scale(points, limit, H, W);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block h-10 w-full" aria-hidden>
      {limit !== null && limit >= s.lo && limit <= s.hi && (
        <line x1={0} x2={W} y1={s.y(limit)} y2={s.y(limit)} stroke="#F2A541" strokeDasharray="3 3" strokeWidth={1} vectorEffect="non-scaling-stroke" />
      )}
      <path d={s.path} fill="none" stroke="#102A35" strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

// ---------------------------------------------------------------- one machine

function MachineView(props: {
  id: string;
  now: number;
  live: LiveMachine | undefined;
  limits: Limits | null;
  info: MachineRow | null;
  alerts: AlertRow[];
  activity: ActivityRow | null;
  noStatus: string;
  onBack: () => void;
}) {
  const { id, now, live, limits, info, alerts, activity, noStatus } = props;
  const open = alerts.filter((a) => a.resolvedAt === null);
  const points = live?.recent ?? [];
  const last = points.at(-1);
  const health = healthOf(live, open, now);
  const state = activity?.state ?? null;
  const broken = activity?.broken ?? null;
  const headline = broken
    ? `${BROKEN_TITLE[broken.kind] ?? capitalise(broken.kind.replace(/_/g, " "))}: ${brokenDetail(broken).toLowerCase()}`
    : state === "offline"
      ? activity?.last_received_at
        ? `No data for ${ago(now, activity.last_received_at)}`
        : "No data stored yet"
      : state === "idle"
        ? "Connected, not running"
        : state === "online"
          ? health === "warning"
            ? summary(health, last, open).replace(/\.$/, "")
            : "Running normally"
          : "";
  const [range, setRange] = useState<"live" | "10m">("live");

  // Stored readings, only fetched while the 10-minute chart is showing.
  const history = usePoll<{ status: StatusRow[] }>(
    range === "10m" ? `/api/v1/machines/${encodeURIComponent(id)}/readings?minutes=10` : null,
    10_000,
  );
  const stored = history.data?.status ?? [];
  const tempLive = tempSeries(points);
  const tempStored = stored.filter((s) => s.temperatureC !== null).map((s) => ({ t: Date.parse(s.receivedAt), v: s.temperatureC as number }));
  const vib = spreadSeries(points, limits?.vibration_window ?? 20);
  const a = last?.accel ?? null;
  const fft = info?.latestWindow ?? null;
  const flags = new Set(last?.flags ?? []);
  const tempBad = flags.has("temp_high") || flags.has("temp_rising");
  const vibBad = flags.has("vibration_high") || flags.has("shock");

  return (
    <div className="space-y-6">
      <button onClick={props.onBack} className="text-sm text-navy/70 hover:text-navy">
        ← Overview
      </button>

      {/* Machine */}
      <section aria-labelledby="machine-heading" className={`rounded-lg bg-white px-6 py-5 ${broken ? "ring-2 ring-amber" : ""}`}>
        <div className="flex flex-wrap items-center justify-between gap-x-8 gap-y-3">
          <div>
            <h2 id="machine-heading" className="text-[28px] leading-tight font-normal tracking-[-0.01em]">
              {info?.name ?? id}
            </h2>
            <p className="text-xs text-navy/70">
              {id}
              {kindText(info) ? `, ${kindText(info)}` : ""}
              {info?.rpm ? `, ${info.rpm.toLocaleString()} rpm` : ""}
            </p>
          </div>
          <div className="flex items-center gap-6">
            <div className="flex flex-col items-end gap-1.5">
              <span className={`flex items-center gap-1.5 rounded-sm bg-cool px-2.5 py-1 text-sm font-medium ${state ? STATE[state].text : "text-navy/70"}`}>
                <span className={`h-2 w-2 rounded-full ${state ? STATE[state].dot : "bg-navy/25"}`} aria-hidden />
                {state ? STATE[state].label : noStatus}
              </span>
              {broken && <span className="rounded-sm bg-amber px-2.5 py-1 text-sm font-semibold text-navy">Broken</span>}
            </div>
          </div>
        </div>
        {headline && <p className={`mt-2 text-sm ${broken ? "text-amber-ink" : "text-navy/75"}`}>{headline}</p>}
        {broken && SUGGESTED_CHECK[broken.kind] && (
          <p className="mt-1 text-sm text-navy/75">
            <span className="font-medium">Suggested check:</span> {SUGGESTED_CHECK[broken.kind]}
          </p>
        )}
      </section>

      {/* Readings */}
      <div className="grid gap-6 xl:grid-cols-2">
        <Box
          title="Temperature"
          tone={tempBad ? "danger" : undefined}
          action={
            <Segmented
              value={range}
              onChange={setRange}
              options={[
                { value: "live", label: "1 min" },
                { value: "10m", label: "10 min" },
              ]}
            />
          }
        >
          <Big value={tempLive.at(-1)?.v} digits={1} unit="°C" bad={tempBad} />
          {range === "live" ? (
            <Chart points={tempLive} limit={limits?.temp_max_c ?? null} limitLabel={limits ? `${limits.temp_max_c} °C` : ""} span="1 min" />
          ) : tempStored.length > 1 ? (
            <Chart points={tempStored} limit={limits?.temp_max_c ?? null} limitLabel={limits ? `${limits.temp_max_c} °C` : ""} span="10 min" />
          ) : (
            <Empty>No stored readings yet</Empty>
          )}
        </Box>

        <Box title="Vibration" tone={vibBad ? "danger" : undefined}>
          <Big value={vib.at(-1)?.v} digits={3} unit="g" bad={vibBad} note="Relative, not calibrated" />
          <Chart points={vib} limit={limits?.vibration_std_max_g ?? null} limitLabel={limits ? `${limits.vibration_std_max_g} g` : ""} span="1 min" />
          <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-line/70 pt-3 text-sm sm:grid-cols-4">
            <Stat label="X" value={a ? `${a.x.toFixed(3)} g` : "—"} />
            <Stat label="Y" value={a ? `${a.y.toFixed(3)} g` : "—"} />
            <Stat label="Z" value={a ? `${a.z.toFixed(3)} g` : "—"} />
            <Stat label="Total" value={a ? `${Math.hypot(a.x, a.y, a.z).toFixed(2)} g` : "—"} />
          </dl>
        </Box>
      </div>

      {/* Details */}
      <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
        <Box title="Database info" variant="quiet">
          <dl className="divide-y divide-line/60 text-[15px]">
            <Row label="Saved to database" value={live?.last_flush_at ? `${ago(now, live.last_flush_at)} ago` : "Not yet"} bad={live?.last_error != null} />
            <Row label="Waiting to be saved" value={live ? String(live.buffered) : "—"} />
            <Row
              label="Last stored in database"
              value={activity?.last_received_at ? `${ago(now, activity.last_received_at)} ago` : "Never"}
              bad={state === "offline"}
            />
            <Row label="Stored in the last minute" value={activity ? String(activity.readings_last_minute) : "—"} />
            {live?.last_error && <Row label="Save failed" value={live.last_error} bad />}
            {live && live.dropped > 0 && <Row label="Lost (buffer full)" value={String(live.dropped)} bad />}
          </dl>
        </Box>

        <Box title="Sensors" variant="quiet">
          <dl className="divide-y divide-line/60 text-[15px]">
            <Row label="Temperature sensor" value={sensorState(last?.thermistor_valid, flags.has("thermistor_fault"))} bad={flags.has("thermistor_fault")} />
            <Row label="Accelerometer" value={sensorState(last?.accel_valid, flags.has("accel_fault"))} bad={flags.has("accel_fault")} />
            <Row label="Last reading" value={last ? `${ago(now, last.t)} ago` : "None"} bad={!last || now - Date.parse(last.t) > STALE_MS} />
            <Row label="Readings per minute" value={String(points.filter((p) => now - Date.parse(p.t) < 60_000).length)} />
          </dl>
        </Box>

        <Box title="Frequency analysis" variant="quiet">
          {fft ? (
            <dl className="divide-y divide-line/60 text-[15px]">
              <Row label="Dominant frequency" value={fmt(fft.dominantFreqHz, 1, "Hz")} />
              <Row label="Peak amplitude" value={fmt(fft.dominantAmpG, 3, "g")} />
              <Row label="Velocity RMS" value={fmt(fft.velocityRmsMmS, 2, "mm/s")} />
              <Row label="Analysed" value={clock(fft.receivedAt)} />
            </dl>
          ) : (
            <Empty>No FFT data from this sensor node</Empty>
          )}
        </Box>
      </div>

      <Box title="Events" variant="quiet">
        {alerts.length === 0 ? (
          <Empty>No events</Empty>
        ) : (
          <ol className="divide-y divide-line/60">
            {alerts.slice(0, 8).map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-x-4 gap-y-0.5 py-2 text-sm first:pt-0 last:pb-0">
                <span
                  aria-hidden
                  className={`h-2.5 w-2.5 shrink-0 rounded-full ${e.resolvedAt ? "bg-navy/25" : e.severity === "alert" ? "bg-amber" : "bg-amber/60"}`}
                />
                <span className="font-medium sm:w-44 sm:shrink-0">{eventTitle(e)}</span>
                <span className="min-w-0 text-navy/70 max-sm:order-last max-sm:basis-full max-sm:pl-6 sm:flex-1">{eventDetail(e)}</span>
                <span className="shrink-0 text-xs text-navy/70 tabular-nums">
                  {clock(e.createdAt)}
                  {e.resolvedAt ? ` to ${clock(e.resolvedAt)}` : ", open"}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Box>
    </div>
  );
}

function eventTitle(e: AlertRow): string {
  const what: Record<string, string> = {
    overheat: "Overheating",
    vibration: "Abnormal vibration",
    sensor_fault: "Sensor problem",
  };
  return what[e.kind] ?? capitalise(e.kind.replace(/_/g, " "));
}

function eventDetail(e: AlertRow): string {
  const listed = e.message.replace(/^Outside limits:\s*/, "").split(/,\s*/).filter(Boolean);
  const words = listed.map((f) => FLAG_TEXT[f] ?? f);
  return words.length ? capitalise(words.join(", ")) : e.message;
}

// ---------------------------------------------------------------- pieces

// A labelled box grouping related data. "quiet" is for secondary information:
// lighter and borderless, so the readings stay the first thing you look at.
function Box(props: { title: string; action?: ReactNode; tone?: "danger"; variant?: "quiet"; children: ReactNode }) {
  const quiet = props.variant === "quiet";
  const surface = quiet ? "bg-white/55" : "bg-white";
  return (
    <section className={`rounded-lg ${surface}`}>
      <div className={`flex items-center justify-between gap-4 px-5 ${quiet ? "pt-4 pb-1" : "border-b border-line/70 py-3"}`}>
        <h3 className={`text-[17px] font-normal ${props.tone === "danger" ? "text-amber-ink" : quiet ? "text-navy/70" : ""}`}>{props.title}</h3>
        {props.action}
      </div>
      <div className={`px-5 ${quiet ? "pb-4" : "py-4"}`}>{props.children}</div>
    </section>
  );
}

function Big(props: { value: number | undefined; digits: number; unit: string; bad: boolean; note?: string }) {
  return (
    <p className="mb-3 flex items-baseline justify-between gap-4">
      <span className={`text-5xl leading-none font-normal tracking-[-0.02em] tabular-nums ${props.bad ? "text-amber-ink" : ""}`}>
        {props.value === undefined ? "—" : props.value.toFixed(props.digits)}
        <span className="ml-1 text-base font-normal text-navy/70">{props.unit}</span>
      </span>
      {props.note && <span className="text-xs text-navy/70">{props.note}</span>}
    </p>
  );
}

function Segmented<T extends string>(props: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] }) {
  return (
    <div role="radiogroup" className="inline-flex border border-line text-xs">
      {props.options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={props.value === o.value}
          onClick={() => props.onChange(o.value)}
          className={`px-3 py-1.5 focus-visible:outline-2 focus-visible:outline-navy ${
            props.value === o.value ? "bg-navy text-cool" : "bg-white text-navy/70 hover:bg-cool"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-navy/70">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}

function Row({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className="flex justify-between gap-4 py-2 first:pt-0">
      <dt className="text-navy/70">{label}</dt>
      <dd className={`text-right tabular-nums ${bad ? "font-medium text-amber-ink" : ""}`}>{value}</dd>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-navy/70">{children}</p>;
}

// ---------------------------------------------------------------- charts (plain SVG)

function scale(points: Point[], limit: number | null, height: number, width: number) {
  const values = points.map((p) => p.v);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (limit !== null && limit <= hi * 1.6) hi = Math.max(hi, limit); // show the limit when it's close
  const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.05 || 1;
  lo = values.every((v) => v >= 0) ? Math.max(0, lo - pad) : lo - pad;
  hi += pad;
  const t0 = points[0].t;
  const t1 = Math.max(points[points.length - 1].t, t0 + 1);
  const x = (t: number) => ((t - t0) / (t1 - t0)) * width;
  const y = (v: number) => height - ((v - lo) / (hi - lo)) * height;
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  return { lo, hi, y, path };
}

function Chart(props: { points: Point[]; limit: number | null; limitLabel: string; span: string; height?: number }) {
  const H = props.height ?? 140;
  const W = 600;
  if (props.points.length < 2) {
    return (
      <div style={{ height: H }} className="flex items-center justify-center border border-dashed border-line text-sm text-navy/70">
        Waiting for readings
      </div>
    );
  }
  const s = scale(props.points, props.limit, H, W);
  const limitShown = props.limit !== null && props.limit >= s.lo && props.limit <= s.hi;
  const ticks = [s.hi, (s.hi + s.lo) / 2, s.lo];
  const tickDigits = s.hi - s.lo < 1 ? 3 : 1;
  return (
    <figure>
      <div className="flex gap-3">
        <div className="relative w-12 shrink-0 text-right text-[11px] tabular-nums text-navy/70" style={{ height: H }} aria-hidden>
          {ticks.map((v, i) => (
            <span key={i} className="absolute right-0 -translate-y-1/2" style={{ top: `${(i / 2) * 100}%` }}>
              {v.toFixed(tickDigits)}
            </span>
          ))}
        </div>
        <div className="relative min-w-0 flex-1">
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block w-full" style={{ height: H }} role="img" aria-label={`Trend over the last ${props.span}`}>
            {[0, 0.5, 1].map((f) => (
              <line key={f} x1={0} x2={W} y1={f * H} y2={f * H} stroke="#E9F0F2" strokeWidth={1} vectorEffect="non-scaling-stroke" />
            ))}
            {limitShown && (
              <line x1={0} x2={W} y1={s.y(props.limit as number)} y2={s.y(props.limit as number)} stroke="#F2A541" strokeDasharray="5 4" strokeWidth={1} vectorEffect="non-scaling-stroke" />
            )}
            <path d={s.path} fill="none" stroke="#102A35" strokeWidth={1.75} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          </svg>
          {limitShown && (
            <span
              className="absolute right-0 -translate-y-full bg-white/80 pb-0.5 pl-1 text-[11px] text-amber-ink"
              style={{ top: `${(s.y(props.limit as number) / H) * 100}%` }}
            >
              Limit {props.limitLabel}
            </span>
          )}
          <div className="mt-1 flex justify-between text-[11px] text-navy/70" aria-hidden>
            <span>{duration(props.points[props.points.length - 1].t - props.points[0].t)} ago</span>
            <span>Now</span>
          </div>
        </div>
      </div>
    </figure>
  );
}

// ---------------------------------------------------------------- formatting

function ago(now: number, iso: string): string {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  return `${Math.floor(s / 3600)} h`;
}

function duration(ms: number): string {
  const s = Math.round(ms / 1000);
  return s < 90 ? `${s} s` : `${Math.round(s / 60)} min`;
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmt(v: number | null, digits: number, unit: string): string {
  return v === null ? "—" : `${v.toFixed(digits)} ${unit}`;
}

function sensorState(valid: boolean | null | undefined, fault: boolean): string {
  if (fault || valid === false) return "Not responding";
  if (valid === true) return "Working";
  return "Unknown";
}

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
