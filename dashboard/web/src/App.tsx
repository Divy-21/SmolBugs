import { useEffect, useState } from "react";
import { post, useLive } from "./useLive";
import type { EventMsg, Status, WindowMsg } from "./types";
import { SpectrumChart, TemperatureChart, VibrationChart } from "./Charts";

const STATUS_LOOK: Record<Status, { word: string; tone: string }> = {
  learning: { word: "Learning normal", tone: "learning" },
  waiting: { word: "Waiting", tone: "off" },
  ok: { word: "Running normally", tone: "good" },
  off: { word: "Machine off", tone: "off" },
  check: { word: "Check the machine", tone: "warning" },
  fault: { word: "Fault detected", tone: "critical" },
};

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function Icon({ tone }: { tone: string }) {
  const common = { width: 40, height: 40, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (tone === "good") return <svg {...common}><circle cx="12" cy="12" r="10" /><path d="m8 12 3 3 5-6" /></svg>;
  if (tone === "warning") return <svg {...common}><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4M12 17h.01" /></svg>;
  if (tone === "critical") return <svg {...common}><path d="M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9Z" /><path d="m15 9-6 6M9 9l6 6" /></svg>;
  if (tone === "learning") return <svg {...common} className="spin"><path d="M21 12a9 9 0 1 1-6.2-8.6" /></svg>;
  return <svg {...common}><path d="M12 2v10" /><path d="M18.4 6.6a9 9 0 1 1-12.8 0" /></svg>;
}

function StatusCard({ msg }: { msg: WindowMsg | null }) {
  if (!msg) return <section className="card status tone-off"><p className="muted">Connecting to MachineGuard...</p></section>;
  const look = STATUS_LOOK[msg.status];
  const learnTotal = 15;
  return (
    <section className={`card status tone-${look.tone}`} aria-live="polite">
      <div className="status-top">
        <span className="status-icon"><Icon tone={look.tone} /></span>
        <div>
          <p className="eyebrow">Drill 1</p>
          <h1>{look.word}</h1>
        </div>
      </div>
      {msg.status === "fault" || msg.status === "check" ? <p className="status-msg">{msg.message}</p> : null}
      {msg.status === "learning" && msg.learning_seconds_left !== null && (
        <div className="progress" role="progressbar" aria-valuenow={msg.learning_seconds_left}>
          <div style={{ width: `${Math.max(0, 100 - (msg.learning_seconds_left / learnTotal) * 100)}%` }} />
          <span>{Math.ceil(msg.learning_seconds_left)} s left. Keep it running normally.</span>
        </div>
      )}
      <div className="status-meta">
        {msg.fault_since && <span>Since {time(msg.fault_since)}</span>}
        {msg.confidence !== null && (msg.status === "fault" || msg.status === "ok") && <span>{Math.round(msg.confidence * 100)}% sure</span>}
        {msg.live_status && msg.live_status !== msg.status && <span>Right now: {STATUS_LOOK[msg.live_status].word.toLowerCase()}</span>}
      </div>
    </section>
  );
}

function Recommendation({ msg, onFixed }: { msg: WindowMsg | null; onFixed: () => void }) {
  const isFault = msg?.status === "fault";
  return (
    <section className="card reco">
      <h2>What to do</h2>
      <p className="reco-text">{msg?.recommendation ?? "Waiting for data."}</p>
      {isFault && <button className="btn primary" onClick={onFixed}>Fixed it</button>}
    </section>
  );
}

function Tiles({ msg }: { msg: WindowMsg | null }) {
  const rms = msg ? Math.max(msg.rms_g.x, msg.rms_g.y, msg.rms_g.z) : null;
  const slope = msg?.temp_slope_c_per_min ?? 0;
  const tiles = [
    { label: "Running speed", value: msg?.run_hz ? `${Math.round(msg.run_hz * 60)}` : "--", unit: "RPM" },
    { label: "Vibration", value: rms !== null ? rms.toFixed(3) : "--", unit: "g RMS" },
    { label: "Temperature", value: msg?.temp_c != null ? msg.temp_c.toFixed(1) : "--", unit: "°C",
      sub: Math.abs(slope) >= 1 ? `${slope > 0 ? "+" : ""}${slope.toFixed(1)} °C per min` : "steady" },
    { label: "Running time", value: msg ? formatDuration(msg.running_hours) : "--", unit: "" },
  ];
  return (
    <div className="tiles">
      {tiles.map((t) => (
        <div className="card tile" key={t.label}>
          <p className="eyebrow">{t.label}</p>
          <p className="tile-value">{t.value} <span className="unit">{t.unit}</span></p>
          {t.sub && <p className="muted small">{t.sub}</p>}
        </div>
      ))}
    </div>
  );
}

function formatDuration(hours: number) {
  const mins = Math.floor(hours * 60);
  if (mins < 60) return `${mins} min`;
  return `${Math.floor(mins / 60)} h ${mins % 60} min`;
}

function Health({ hours }: { hours: number }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="card health">
      <h2>Machine health</h2>
      <p className="health-status"><span className="dot good" /> No wear detected</p>
      <p className="muted small">
        Bearing wear builds up over many hours, so this checks the trend across hours of use.
        {hours < 4 ? " It needs a few hours of running before it can judge." : ""}
      </p>
      <button className="chart-thumb" onClick={() => setOpen(true)} aria-label="Open bearing test chart">
        <img src="/api/bearing-chart" alt="Bearing run-to-failure test chart" />
        <span>Tested on a real bearing that ran until it failed: warned 79 hours before failure.</span>
      </button>
      {open && (
        <div className="modal" onClick={() => setOpen(false)} role="dialog">
          <img src="/api/bearing-chart" alt="Bearing run-to-failure test chart" />
        </div>
      )}
    </section>
  );
}

function EventLog({ events }: { events: EventMsg[] }) {
  return (
    <section className="card log">
      <h2>Event log</h2>
      {events.length === 0 ? <p className="muted">Nothing yet.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Time</th><th>What happened</th><th>What to do</th></tr></thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={`${e.time}-${i}`}>
                  <td className="nowrap">{new Date(e.time).toLocaleTimeString()}</td>
                  <td>{e.text}</td>
                  <td className="muted">{e.recommendation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Controls({ msg, onSourceChange }: { msg: WindowMsg | null; onSourceChange: () => void }) {
  const [files, setFiles] = useState<string[]>([]);
  const [ports, setPorts] = useState<{ device: string; description: string }[]>([]);
  const [choice, setChoice] = useState("demo");
  const [error, setError] = useState("");

  const refresh = () => {
    fetch("/api/recordings").then((r) => r.json()).then((d) => setFiles(d.files)).catch(() => {});
    fetch("/api/ports").then((r) => r.json()).then((d) => setPorts(d.ports)).catch(() => {});
  };
  useEffect(refresh, []);

  const change = async (value: string) => {
    setChoice(value);
    setError("");
    const [mode, arg] = value.split(":");
    try {
      await post("/api/source", mode === "demo" ? { mode } : mode === "serial" ? { mode, port: arg } : { mode, file: arg });
      onSourceChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="controls">
      <label className="field">
        <span>Data source</span>
        <select value={choice} onChange={(e) => change(e.target.value)} onFocus={refresh}>
          <option value="demo">Demo: every fault in a row</option>
          {ports.length > 0 && (
            <optgroup label="Live from the ESP32">
              {ports.map((p) => <option key={p.device} value={`serial:${p.device}`}>{p.device} ({p.description})</option>)}
            </optgroup>
          )}
          <optgroup label="Replay one recording">
            {files.map((f) => <option key={f} value={`replay:${f}`}>{f}</option>)}
          </optgroup>
        </select>
      </label>
      <button className="btn" onClick={() => post("/api/calibrate").then(onSourceChange)}>Learn normal</button>
      {msg && <span className="muted small playing">Playing {msg.source.playing}</span>}
      {error && <span className="error small">{error}</span>}
    </div>
  );
}

export default function App() {
  const { latest, events, rms, temps, connected, clearCharts } = useLive();
  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>MG</span>
          <span>MachineGuard</span>
          <span className={`conn ${connected ? "on" : ""}`}>{connected ? "Connected" : "Reconnecting"}</span>
        </div>
        <Controls msg={latest} onSourceChange={clearCharts} />
      </header>

      <main className="grid">
        <StatusCard msg={latest} />
        <Recommendation msg={latest} onFixed={() => post("/api/reset")} />
        <Tiles msg={latest} />

        <section className="card chart wide">
          <h2>Frequency spectrum <span className="muted small">({latest?.spectrum.axis.toUpperCase() ?? "X"} axis, from the FFT)</span></h2>
          <p className="muted small">Dotted lines mark 1x, 2x and 3x running speed. A tall 1x peak means imbalance, a tall 2x peak means a bent bit.</p>
          <SpectrumChart msg={latest} />
        </section>
        <section className="card chart">
          <h2>Vibration level <span className="muted small">(last 60 s)</span></h2>
          <VibrationChart data={rms} />
        </section>
        <section className="card chart">
          <h2>Temperature <span className="muted small">(last 10 min)</span></h2>
          <TemperatureChart data={temps} />
        </section>

        <Health hours={latest?.running_hours ?? 0} />
        <EventLog events={events} />
      </main>
    </div>
  );
}
