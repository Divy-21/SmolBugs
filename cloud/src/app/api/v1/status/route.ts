import { z } from "zod";
import { connection } from "next/server";
import { checkDeviceKey } from "@/lib/auth";
import { statusPacket, type StatusPacket } from "@/lib/packets";
import type { IncomingReading } from "@/lib/stream";
import { streamBuffer } from "@/lib/streamStore";

// POST one status packet or an array of up to 100 (ingest/cloud.py sends up to 50).
// Held in memory and written every 2 s (STREAM_FLUSH_MS); abnormal packets are written before
// answering. How "abnormal" is decided: src/lib/stream.ts.
//
// Answers 200 once the packets are held (or written, if abnormal). Answers 503
// if an abnormal packet couldn't be written; the gateway then retries, and the
// retry is recognised by packet_id and not counted twice.
export async function POST(request: Request) {
  const denied = checkDeviceKey(request);
  if (denied) return denied;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json(
      { ok: false, error: "Body is not valid JSON (NaN and Infinity aren't JSON either)" },
      { status: 400 },
    );
  }

  // Validate against the single or the batch schema directly, not a union, so a
  // bad packet gets the exact field that's wrong rather than "Invalid input".
  const parsed = (Array.isArray(body) ? z.array(statusPacket).min(1).max(100) : statusPacket).safeParse(body);
  if (!parsed.success) {
    return Response.json(
      { ok: false, error: "Packet doesn't match the format", details: z.prettifyError(parsed.error) },
      { status: 400 },
    );
  }
  const packets: StatusPacket[] = Array.isArray(parsed.data) ? parsed.data : [parsed.data];

  const buffer = streamBuffer();
  const results = packets.map((p) => buffer.add(toReading(p)));

  const urgent = new Set(packets.filter((_, i) => results[i].flushNow).map((p) => p.machine_id));
  let writtenNow = true;
  for (const id of urgent) writtenNow = (await buffer.flush(id)) && writtenNow;

  const duplicates = results.filter((r) => r.duplicate).length;
  const answer = {
    ok: writtenNow,
    stored: packets.length - duplicates, // held or written; kept for ingest/cloud.py's log
    duplicates,
    abnormal: urgent.size > 0,
    written_now: urgent.size > 0 ? writtenNow : null, // null = held for the next scheduled write
    flags: results.map((r) => r.flags),
  };
  if (!writtenNow) {
    return Response.json(
      { ...answer, error: "Abnormal reading held but not yet written to the database; retry" },
      { status: 503 },
    );
  }
  return Response.json(answer);
}

// GET: what is currently held in memory, for checking the endpoint works.
export async function GET() {
  await connection();
  return Response.json(streamBuffer().stats());
}

function toReading(p: StatusPacket): IncomingReading {
  const thermistor = p.sensors.find((s) => s.type === "thermistor");
  const accel = p.sensors.find((s) => s.type === "accelerometer");
  const hasXyz = accel && typeof accel.x_g === "number" && typeof accel.y_g === "number" && typeof accel.z_g === "number";
  return {
    machineId: p.machine_id,
    receivedAt: p.received_at_utc ? new Date(p.received_at_utc) : new Date(),
    deviceTimeMs: p.timestamp_ms ?? null,
    bootId: p.boot_id ?? null,
    seq: p.seq ?? null,
    packetId: p.packet_id ?? null,
    thermistorMv: thermistor?.voltage_mv ?? null,
    thermistorValid: thermistor?.valid ?? null,
    temperatureC: thermistor?.temperature_c ?? null,
    accel: accel && hasXyz ? { x: accel.x_g as number, y: accel.y_g as number, z: accel.z_g as number } : null,
    accelValid: accel?.valid ?? null,
    payload: p,
  };
}
