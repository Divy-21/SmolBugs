import { timingSafeEqual } from "node:crypto";

// Devices and the gateway prove who they are with the x-device-key header.
// Valid keys come from DEVICE_KEYS (comma-separated). With no keys configured,
// every request is refused rather than every request accepted.
export function checkDeviceKey(request: Request): Response | null {
  const keys = (process.env.DEVICE_KEYS ?? "")
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);
  if (keys.length === 0) {
    return Response.json(
      { ok: false, error: "Server has no DEVICE_KEYS configured" },
      { status: 500 },
    );
  }

  const sent = request.headers.get("x-device-key") ?? "";
  const ok = keys.some((key) => sameString(sent, key));
  if (!ok) {
    return Response.json(
      { ok: false, error: "Missing or wrong x-device-key header" },
      { status: 401 },
    );
  }
  return null;
}

function sameString(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  return ab.length === bb.length && timingSafeEqual(ab, bb);
}
