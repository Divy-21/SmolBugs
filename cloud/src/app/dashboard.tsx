"use client";

// SmolBugs machine-condition dashboard.
//
// Reads only the cloud's own API:
//   GET /api/v1/live      every 1 s   last ~70 s per machine, from memory (live view)
//   GET /api/v1/machines  every 10 s  names, kinds, latest stored FFT window
//   GET /api/v1/alerts    every 5 s   event history
//   GET /api/v1/machines/{id}/readings?minutes=10   every 15 s   10-minute trend (database)
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

type StatusRow = { receivedAt: string; temperatureC: number | null };

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

// Ordered most urgent first: the fleet list is sorted by this.
const HEALTH: Record<Health, { rank: number; label: string; bar: string; text: string; dot: string }> = {
  critical: { rank: 0, label: "Critical", bar: "bg-red-600", text: "text-red-700", dot: "bg-red-600" },
  warning: { rank: 1, label: "Warning", bar: "bg-amber-500", text: "text-amber-700", dot: "bg-amber-500" },
  offline: { rank: 2, label: "No signal", bar: "bg-slate-400", text: "text-slate-600", dot: "bg-slate-400" },
  healthy: { rank: 3, label: "Healthy", bar: "bg-emerald-600", text: "text-emerald-700", dot: "bg-emerald-600" },
  nodata: { rank: 4, label: "Waiting", bar: "bg-slate-200", text: "text-slate-500", dot: "bg-slate-300" },
};

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

// ---------------------------------------------------------------- page

export default function Dashboard() {
  const now = useNow();
  const live = usePoll<Live>("/api/v1/live", 1000);
  const machines = usePoll<{ machines: MachineRow[] }>("/api/v1/machines", 10_000);
  const alerts = usePoll<{ alerts: AlertRow[] }>("/api/v1/alerts?limit=100", 5000);
  const [picked, setPicked] = useState<string | null>(null);

  const liveOf = (id: string) => live.data?.machines.find((m) => m.machine_id === id);
  const infoOf = (id: string) => machines.data?.machines.find((m) => m.id === id) ?? null;
  const alertsOf = (id: string) => (alerts.data?.alerts ?? []).filter((a) => a.machineId === id);
  const openOf = (id: string) => alertsOf(id).filter((a) => a.resolvedAt === null);

  // Every machine either source knows, most urgent first.
  const ids = useMemo(() => {
    const set = new Set<string>();
    for (const m of live.data?.machines ?? []) set.add(m.machine_id);
    for (const m of machines.data?.machines ?? []) set.add(m.id);
    return [...set];
  }, [live.data, machines.data]);
  const rows = ids
    .map((id) => ({ id, health: healthOf(liveOf(id), openOf(id), now) }))
    .sort((a, b) => HEALTH[a.health].rank - HEALTH[b.health].rank || a.id.localeCompare(b.id));

  const selected = picked && ids.includes(picked) ? picked : (rows[0]?.id ?? null);
  const attention = rows.filter((r) => r.health === "critical" || r.health === "warning").length;
  const silent = rows.filter((r) => r.health === "offline").length;
  const apiDown = live.error !== null;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-baseline justify-between gap-x-8 gap-y-1 px-6 py-4">
          <h1 className="text-[15px] font-semibold tracking-tight">SmolBugs</h1>
          <p className="text-sm text-slate-600">
            {rows.length === 0
              ? "No machines reporting yet"
              : attention > 0
                ? `${attention} of ${rows.length} machines need attention`
                : `All ${rows.length} machines within limits`}
            {silent > 0 && `, ${silent} not reporting`}
          </p>
        </div>
      </header>

      {apiDown && (
        <p role="alert" className="border-b border-red-200 bg-red-50 px-6 py-2 text-center text-sm text-red-800">
          Lost contact with the SmolBugs server ({live.error}). Showing the last data received.
        </p>
      )}

      <div className="mx-auto max-w-7xl space-y-8 px-6 py-8">
        <Fleet
          rows={rows}
          selected={selected}
          onSelect={setPicked}
          now={now}
          liveOf={liveOf}
          infoOf={infoOf}
          limits={live.data?.limits ?? null}
        />

        {selected ? (
          <MachineView
            key={selected}
            id={selected}
            now={now}
            live={liveOf(selected)}
            limits={live.data?.limits ?? null}
            info={infoOf(selected)}
            alerts={alertsOf(selected)}
          />
        ) : (
          <p className="border border-dashed border-slate-300 bg-white px-6 py-12 text-center text-sm text-slate-500">
            Machines appear here as soon as they send their first reading. To try it without hardware, run{" "}
            <code className="rounded bg-slate-100 px-1 py-0.5">npm run fake-fleet</code>.
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- fleet table

function Fleet(props: {
  rows: { id: string; health: Health }[];
  selected: string | null;
  onSelect: (id: string) => void;
  now: number;
  liveOf: (id: string) => LiveMachine | undefined;
  infoOf: (id: string) => MachineRow | null;
  limits: Limits | null;
}) {
  const { rows, selected, onSelect, now, liveOf, infoOf, limits } = props;
  if (rows.length === 0) return null;
  const cols = "grid grid-cols-[1fr_auto] items-center gap-x-6 md:grid-cols-[minmax(180px,1.4fr)_120px_1fr_1fr_96px]";

  return (
    <section aria-labelledby="fleet-heading" className="border border-slate-200 bg-white">
      <h2 id="fleet-heading" className="sr-only">
        Machines
      </h2>
      <div className={`${cols} border-b border-slate-200 px-5 py-2.5 text-xs text-slate-500 max-md:hidden`}>
        <span>Machine</span>
        <span>Condition</span>
        <span>Temperature</span>
        <span>Vibration</span>
        <span className="text-right">Last reading</span>
      </div>
      <ul>
        {rows.map(({ id, health }) => {
          const live = liveOf(id);
          const info = infoOf(id);
          const points = live?.recent ?? [];
          const last = points.at(-1);
          const temp = tempSeries(points);
          const vib = spreadSeries(points, limits?.vibration_window ?? 20);
          const isSel = id === selected;
          return (
            <li key={id} className="border-b border-slate-100 last:border-b-0">
              <button
                onClick={() => onSelect(id)}
                aria-current={isSel ? "true" : undefined}
                className={`${cols} relative w-full px-5 py-3 text-left focus-visible:z-10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-slate-900 ${
                  isSel ? "bg-slate-50" : "hover:bg-slate-50/70"
                }`}
              >
                <span className={`absolute inset-y-0 left-0 w-1 ${HEALTH[health].bar}`} aria-hidden />
                <span className="min-w-0">
                  <span className="block truncate font-medium">{info?.name ?? id}</span>
                  <span className="block text-xs text-slate-500">
                    {id}
                    {kindText(info) ? `, ${kindText(info)}` : ""}
                  </span>
                </span>
                <span className={`text-sm font-medium ${HEALTH[health].text}`}>{HEALTH[health].label}</span>
                <Reading value={temp.at(-1)?.v} digits={1} unit="°C" points={temp} limit={limits?.temp_max_c ?? null} />
                <Reading value={vib.at(-1)?.v} digits={3} unit="g" points={vib} limit={limits?.vibration_std_max_g ?? null} />
                <span className="text-right text-sm tabular-nums text-slate-500 max-md:hidden">
                  {last ? ago(now, last.t) : "never"}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Reading(props: { value: number | undefined; digits: number; unit: string; points: Point[]; limit: number | null }) {
  const over = props.limit !== null && props.value !== undefined && props.value > props.limit;
  return (
    <span className="flex items-center gap-3 max-md:hidden">
      <span className={`w-20 shrink-0 text-sm tabular-nums ${over ? "font-medium text-red-700" : ""}`}>
        {props.value === undefined ? "—" : `${props.value.toFixed(props.digits)} ${props.unit}`}
      </span>
      <Sparkline points={props.points} limit={props.limit} />
    </span>
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
}) {
  const { id, now, live, limits, info, alerts } = props;
  const open = alerts.filter((a) => a.resolvedAt === null);
  const points = live?.recent ?? [];
  const last = points.at(-1);
  const health = healthOf(live, open, now);
  const [range, setRange] = useState<"live" | "10m">("live");

  const history = usePoll<{ status: StatusRow[] }>(
    range === "10m" ? `/api/v1/machines/${encodeURIComponent(id)}/readings?minutes=10` : null,
    15_000,
  );
  const tempLive = tempSeries(points);
  const tempStored = (history.data?.status ?? [])
    .filter((s) => s.temperatureC !== null)
    .map((s) => ({ t: Date.parse(s.receivedAt), v: s.temperatureC as number }));
  const vib = spreadSeries(points, limits?.vibration_window ?? 20);
  const a = last?.accel ?? null;
  const fft = info?.latestWindow ?? null;
  const flags = new Set(last?.flags ?? []);

  return (
    <section aria-labelledby="machine-heading" className="grid gap-8 lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
      <div className="border border-slate-200 bg-white">
        {/* Condition: the one loud element on the page */}
        <div className="relative border-b border-slate-200 py-5 pr-6 pl-7">
          <span className={`absolute inset-y-0 left-0 w-1.5 ${HEALTH[health].bar}`} aria-hidden />
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
            <h2 id="machine-heading" className="text-xl font-semibold tracking-tight">
              {info?.name ?? id}
            </h2>
            <p className="text-sm text-slate-500">
              {id}
              {kindText(info) ? `, ${kindText(info)}` : ""}
              {info?.rpm ? `, ${info.rpm.toLocaleString()} rpm` : ""}
            </p>
          </div>
          <p className={`mt-3 text-3xl font-semibold tracking-tight ${HEALTH[health].text}`}>{HEALTH[health].label}</p>
          <p className="mt-1 text-slate-700">{summary(health, last, open)}</p>
          {open[0] && <p className="mt-1 text-sm text-slate-500">Alert open since {clock(open[0].createdAt)}</p>}
        </div>

        {/* Temperature */}
        <Measure
          title="Temperature"
          value={tempLive.at(-1)?.v}
          digits={1}
          unit="°C"
          limitText={limits ? `Limit ${limits.temp_max_c} °C, or a rise faster than ${limits.temp_rise_c_per_min} °C/min` : null}
          bad={flags.has("temp_high") || flags.has("temp_rising")}
          control={
            <Segmented
              value={range}
              onChange={setRange}
              options={[
                { value: "live", label: "Last minute" },
                { value: "10m", label: "Last 10 min" },
              ]}
            />
          }
        >
          {range === "live" ? (
            <Chart points={tempLive} limit={limits?.temp_max_c ?? null} limitLabel={limits ? `${limits.temp_max_c} °C` : ""} span="1 min" />
          ) : tempStored.length > 1 ? (
            <Chart points={tempStored} limit={limits?.temp_max_c ?? null} limitLabel={limits ? `${limits.temp_max_c} °C` : ""} span="10 min" />
          ) : (
            <Empty>Stored readings appear here about every 30 seconds.</Empty>
          )}
        </Measure>

        {/* Vibration */}
        <Measure
          title="Vibration"
          value={vib.at(-1)?.v}
          digits={3}
          unit="g"
          limitText={
            limits
              ? `Limit ${limits.vibration_std_max_g} g. Spread of the last ${limits.vibration_window} accelerometer readings; relative, not calibrated.`
              : null
          }
          bad={flags.has("vibration_high") || flags.has("shock")}
        >
          <Chart points={vib} limit={limits?.vibration_std_max_g ?? null} limitLabel={limits ? `${limits.vibration_std_max_g} g` : ""} span="1 min" />
          <dl className="mt-4 grid grid-cols-4 gap-4 text-sm">
            <Stat label="X" value={a ? `${a.x.toFixed(3)} g` : "—"} />
            <Stat label="Y" value={a ? `${a.y.toFixed(3)} g` : "—"} />
            <Stat label="Z" value={a ? `${a.z.toFixed(3)} g` : "—"} />
            <Stat label="Total (≈1 g at rest)" value={a ? `${Math.hypot(a.x, a.y, a.z).toFixed(2)} g` : "—"} />
          </dl>
        </Measure>
      </div>

      <aside className="space-y-8">
        <Side title="Events">
          {alerts.length === 0 ? (
            <Empty>Alerts for this machine will be listed here.</Empty>
          ) : (
            <ol className="relative space-y-4 border-l border-slate-200 pl-5">
              {alerts.slice(0, 8).map((e) => (
                <li key={e.id} className="relative">
                  <span
                    aria-hidden
                    className={`absolute top-1.5 -left-[25px] h-2.5 w-2.5 rounded-full ring-2 ring-white ${
                      e.resolvedAt ? "bg-slate-300" : e.severity === "alert" ? "bg-red-600" : "bg-amber-500"
                    }`}
                  />
                  <p className="text-sm">
                    <span className="font-medium">{eventTitle(e)}</span>
                    <span className="text-slate-500">, {clock(e.createdAt)}</span>
                  </p>
                  <p className="text-sm text-slate-600">{eventDetail(e)}</p>
                  <p className="text-xs text-slate-500">
                    {e.resolvedAt ? `Back to normal at ${clock(e.resolvedAt)}` : "Still open"}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </Side>

        <Side title="Monitoring">
          <dl className="divide-y divide-slate-100 text-sm">
            <Row label="Last reading" value={last ? `${ago(now, last.t)} ago` : "None yet"} bad={!last || now - Date.parse(last.t) > STALE_MS} />
            <Row label="Readings in the last minute" value={String(points.filter((p) => now - Date.parse(p.t) < 60_000).length)} />
            <Row label="Temperature sensor" value={sensorState(last?.thermistor_valid, flags.has("thermistor_fault"))} bad={flags.has("thermistor_fault")} />
            <Row label="Accelerometer" value={sensorState(last?.accel_valid, flags.has("accel_fault"))} bad={flags.has("accel_fault")} />
            <Row label="Saved to database" value={live?.last_flush_at ? `${ago(now, live.last_flush_at)} ago` : "Not yet"} bad={live?.last_error != null} />
            <Row label="Waiting to be saved" value={live ? String(live.buffered) : "—"} />
            {live?.last_error && <Row label="Last save failed" value={live.last_error} bad />}
            {live && live.dropped > 0 && <Row label="Lost while the database was down" value={String(live.dropped)} bad />}
          </dl>
        </Side>

        <Side title="Frequency analysis">
          {fft ? (
            <dl className="divide-y divide-slate-100 text-sm">
              <Row label="Dominant frequency" value={fmt(fft.dominantFreqHz, 1, "Hz")} />
              <Row label="Peak amplitude" value={fmt(fft.dominantAmpG, 3, "g")} />
              <Row label="Velocity RMS" value={fmt(fft.velocityRmsMmS, 2, "mm/s")} />
              <Row label="Analysed at" value={clock(fft.receivedAt)} />
            </dl>
          ) : (
            <Empty>
              Needs full-rate vibration windows from the sensor node. The current firmware sends one accelerometer reading
              every half second, which shows how much the machine moves but not at what frequency.
            </Empty>
          )}
        </Side>
      </aside>
    </section>
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
  return words.length ? capitalise(words.join(", ")) + "." : e.message;
}

// ---------------------------------------------------------------- pieces

function Measure(props: {
  title: string;
  value: number | undefined;
  digits: number;
  unit: string;
  limitText: string | null;
  bad: boolean;
  control?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="border-b border-slate-200 px-6 py-5 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="text-sm text-slate-600">{props.title}</h3>
          <p className={`mt-0.5 text-2xl font-semibold tabular-nums ${props.bad ? "text-red-700" : ""}`}>
            {props.value === undefined ? "—" : props.value.toFixed(props.digits)}
            <span className="ml-1 text-base font-normal text-slate-500">{props.unit}</span>
          </p>
        </div>
        {props.control}
      </div>
      <div className="mt-4">{props.children}</div>
      {props.limitText && <p className="mt-2 text-xs text-slate-500">{props.limitText}</p>}
    </div>
  );
}

function Side(props: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold">{props.title}</h3>
      {props.children}
    </section>
  );
}

function Segmented<T extends string>(props: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] }) {
  return (
    <div role="radiogroup" className="inline-flex border border-slate-200 text-xs">
      {props.options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={props.value === o.value}
          onClick={() => props.onChange(o.value)}
          className={`px-3 py-1.5 focus-visible:outline-2 focus-visible:outline-slate-900 ${
            props.value === o.value ? "bg-slate-900 text-white" : "bg-white text-slate-600 hover:bg-slate-50"
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
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}

function Row({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className="flex justify-between gap-4 py-2 first:pt-0">
      <dt className="text-slate-600">{label}</dt>
      <dd className={`text-right tabular-nums ${bad ? "font-medium text-red-700" : ""}`}>{value}</dd>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-slate-500">{children}</p>;
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

function Sparkline({ points, limit }: { points: Point[]; limit: number | null }) {
  const W = 120;
  const H = 24;
  if (points.length < 2) return <span className="block h-6 w-full max-w-[160px]" />;
  const s = scale(points, limit, H, W);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block h-6 w-full max-w-[160px]" aria-hidden>
      {limit !== null && limit >= s.lo && limit <= s.hi && (
        <line x1={0} x2={W} y1={s.y(limit)} y2={s.y(limit)} stroke="#fca5a5" strokeWidth={1} vectorEffect="non-scaling-stroke" />
      )}
      <path d={s.path} fill="none" stroke="#334155" strokeWidth={1.25} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

function Chart(props: { points: Point[]; limit: number | null; limitLabel: string; span: string; height?: number }) {
  const H = props.height ?? 140;
  const W = 600;
  if (props.points.length < 2) {
    return (
      <div style={{ height: H }} className="flex items-center justify-center border border-dashed border-slate-200 text-sm text-slate-400">
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
        <div className="relative w-12 shrink-0 text-right text-[11px] tabular-nums text-slate-400" style={{ height: H }} aria-hidden>
          {ticks.map((v, i) => (
            <span key={i} className="absolute right-0 -translate-y-1/2" style={{ top: `${(i / 2) * 100}%` }}>
              {v.toFixed(tickDigits)}
            </span>
          ))}
        </div>
        <div className="relative min-w-0 flex-1">
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block w-full" style={{ height: H }} role="img" aria-label={`Trend over the last ${props.span}`}>
            {[0, 0.5, 1].map((f) => (
              <line key={f} x1={0} x2={W} y1={f * H} y2={f * H} stroke="#f1f5f9" strokeWidth={1} vectorEffect="non-scaling-stroke" />
            ))}
            {limitShown && (
              <line x1={0} x2={W} y1={s.y(props.limit as number)} y2={s.y(props.limit as number)} stroke="#dc2626" strokeDasharray="5 4" strokeWidth={1} vectorEffect="non-scaling-stroke" />
            )}
            <path d={s.path} fill="none" stroke="#0f172a" strokeWidth={1.75} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          </svg>
          {limitShown && (
            <span
              className="absolute right-0 -translate-y-full bg-white/80 pb-0.5 pl-1 text-[11px] text-red-700"
              style={{ top: `${(s.y(props.limit as number) / H) * 100}%` }}
            >
              Limit {props.limitLabel}
            </span>
          )}
          <div className="mt-1 flex justify-between text-[11px] text-slate-400" aria-hidden>
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
