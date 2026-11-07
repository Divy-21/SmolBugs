export type Status = "learning" | "waiting" | "ok" | "off" | "check" | "fault";

export interface WindowMsg {
  type: "window";
  machine_id: string;
  time: string;
  status: Status;
  label: string;
  message: string;
  recommendation: string;
  confidence: number | null;
  live_status?: Status;
  live_message?: string;
  fault_since: string | null;
  learning_seconds_left: number | null;
  rms_g: { x: number; y: number; z: number };
  temp_c: number | null;
  temp_slope_c_per_min: number;
  run_hz: number | null;
  running_hours: number;
  source: { mode: "replay" | "serial"; playing: string };
  spectrum: { axis: string; freqs_hz: number[]; amp_g: number[] };
}

export interface EventMsg {
  type: "event";
  time: string;
  text: string;
  recommendation: string;
}

export interface HistoryMsg {
  type: "history";
  events: EventMsg[];
}
