import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { RmsPoint, TempPoint } from "./useLive";
import type { WindowMsg } from "./types";
import { useChartTheme } from "./theme";

const clock = (t: number) => new Date(t).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });

function axisProps(c: ReturnType<typeof useChartTheme>) {
  return { stroke: c.axis, tick: { fill: c.text, fontSize: 11 }, tickLine: false, axisLine: { stroke: c.grid } };
}

function tooltipStyle(c: ReturnType<typeof useChartTheme>) {
  return {
    contentStyle: { background: c.surface, border: `1px solid ${c.grid}`, borderRadius: 8, fontSize: 12 },
    labelStyle: { color: c.text },
  };
}

export function VibrationChart({ data }: { data: RmsPoint[] }) {
  const c = useChartTheme();
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={c.grid} vertical={false} />
        <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={clock} {...axisProps(c)} minTickGap={40} />
        <YAxis {...axisProps(c)} width={52} tickFormatter={(v: number) => v.toFixed(2)} />
        <Tooltip {...tooltipStyle(c)} labelFormatter={(t: number) => clock(t)} formatter={(v: number) => `${v.toFixed(3)} g`} />
        <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: c.text }} />
        <Line dataKey="x" name="X axis" stroke={c.x} strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line dataKey="y" name="Y axis" stroke={c.y} strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line dataKey="z" name="Z axis" stroke={c.z} strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function SpectrumChart({ msg }: { msg: WindowMsg | null }) {
  const c = useChartTheme();
  const data = msg ? msg.spectrum.freqs_hz.map((f, i) => ({ f, a: msg.spectrum.amp_g[i] })) : [];
  const run = msg?.run_hz ?? null;
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 18, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={c.grid} vertical={false} />
        <XAxis dataKey="f" type="number" domain={[0, 260]} ticks={[0, 50, 100, 150, 200, 250]}
          tickFormatter={(v: number) => `${v} Hz`} {...axisProps(c)} />
        <YAxis {...axisProps(c)} width={52} tickFormatter={(v: number) => v.toFixed(2)} />
        <Tooltip {...tooltipStyle(c)} labelFormatter={(f: number) => `${f.toFixed(1)} Hz`} formatter={(v: number) => [`${v.toFixed(4)} g`, "Strength"]} />
        {run && [1, 2, 3].map((k) => (
          <ReferenceLine key={k} x={run * k} stroke={c.marker} strokeDasharray="4 4"
            label={{ value: `${k}x`, position: "top", fill: c.text, fontSize: 11 }} />
        ))}
        <Line dataKey="a" stroke={c.x} strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function TemperatureChart({ data }: { data: TempPoint[] }) {
  const c = useChartTheme();
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={c.grid} vertical={false} />
        <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={clock} {...axisProps(c)} minTickGap={40} />
        <YAxis {...axisProps(c)} width={52} domain={["dataMin - 1", "dataMax + 1"]} tickFormatter={(v: number) => `${v.toFixed(1)}°`} />
        <Tooltip {...tooltipStyle(c)} labelFormatter={(t: number) => clock(t)} formatter={(v: number) => [`${v.toFixed(1)} °C`, "Temperature"]} />
        <Line dataKey="temp" stroke={c.temp} strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
