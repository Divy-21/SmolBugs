import type { AlertEvent } from "@/lib/stream";

// Phone alerts through ntfy (https://ntfy.sh): install the ntfy app, subscribe to the topic, and
// every alert that opens or closes pings the phone. Off unless NTFY_TOPIC is set. On AWS the topic
// is the secret smolbugs/ntfy-topic: anyone who knows it can read (and send) these messages.
//
// Called only after the alert is safely in the database, so a write that fails and is retried
// doesn't ping twice. Never throws and never delays the write: a phone alert that can't be sent
// is logged and dropped.

const FLAG_WORDS: Record<string, string> = {
  temp_high: "temperature above its limit",
  temp_rising: "temperature rising fast",
  vibration_high: "vibration above its limit",
  shock: "sudden shock",
  thermistor_fault: "temperature sensor fault",
  accel_fault: "accelerometer fault",
};

export type Push = { title: string; message: string; priority: string; tags: string };

// What the phone shows for one alert event.
export function pushFor(e: AlertEvent): Push {
  if (e.type === "resolve") {
    return { title: `${e.machineId}: back to normal`, message: "Readings are within limits again.", priority: "default", tags: "white_check_mark" };
  }
  const flags = e.message.replace(/^Outside limits:\s*/, "").split(/,\s*/).filter(Boolean);
  const words = flags.map((f) => FLAG_WORDS[f] ?? f).join(", ");
  const sensor = e.kind === "sensor_fault";
  return {
    title: `${e.machineId}: ${sensor ? "check the sensor" : `${e.kind} alert`}`,
    message: `${words.charAt(0).toUpperCase()}${words.slice(1)}.`,
    priority: e.severity === "alert" ? "high" : "default",
    tags: sensor ? "wrench" : "warning",
  };
}

export function notifyAlerts(events: readonly AlertEvent[], env: Record<string, string | undefined> = process.env): void {
  const topic = env.NTFY_TOPIC?.trim();
  if (!topic || events.length === 0) return;
  const server = (env.NTFY_SERVER?.trim() || "https://ntfy.sh").replace(/\/+$/, "");
  for (const e of events) {
    const p = pushFor(e);
    const headers: Record<string, string> = { Title: p.title, Priority: p.priority, Tags: p.tags };
    if (env.APP_URL) headers.Click = env.APP_URL;
    fetch(`${server}/${encodeURIComponent(topic)}`, {
      method: "POST",
      body: p.message,
      headers,
      signal: AbortSignal.timeout(5_000),
    })
      .then((res) => {
        if (!res.ok) console.error(`ntfy: ${res.status} for ${p.title}`);
      })
      .catch((err: unknown) => console.error(`ntfy: not sent (${(err as Error).message}) for ${p.title}`));
  }
}
