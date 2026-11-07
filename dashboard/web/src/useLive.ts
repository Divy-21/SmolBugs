import { useEffect, useRef, useState } from "react";
import type { EventMsg, HistoryMsg, WindowMsg } from "./types";

const RMS_POINTS = 120;   // 60 s at one window every 0.5 s
const TEMP_POINTS = 1200; // 10 minutes

export interface RmsPoint { t: number; x: number; y: number; z: number }
export interface TempPoint { t: number; temp: number }

export function useLive() {
  const [latest, setLatest] = useState<WindowMsg | null>(null);
  const [events, setEvents] = useState<EventMsg[]>([]);
  const [rms, setRms] = useState<RmsPoint[]>([]);
  const [temps, setTemps] = useState<TempPoint[]>([]);
  const [connected, setConnected] = useState(false);
  const retry = useRef<number>();

  useEffect(() => {
    let ws: WebSocket;
    let closed = false;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry.current = window.setTimeout(connect, 1500);
      };
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data) as WindowMsg | EventMsg | HistoryMsg;
        if (msg.type === "history") setEvents(msg.events);
        else if (msg.type === "event") setEvents((prev) => [msg, ...prev].slice(0, 200));
        else {
          const t = Date.parse(msg.time);
          setLatest(msg);
          setRms((prev) => [...prev, { t, ...msg.rms_g }].slice(-RMS_POINTS));
          if (msg.temp_c !== null) {
            setTemps((prev) => [...prev, { t, temp: msg.temp_c as number }].slice(-TEMP_POINTS));
          }
        }
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry.current);
      ws?.close();
    };
  }, []);

  const clearCharts = () => { setRms([]); setTemps([]); };
  return { latest, events, rms, temps, connected, clearCharts };
}

export async function post(path: string, body: unknown = {}) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
  return res.json();
}
