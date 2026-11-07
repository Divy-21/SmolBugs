// Response.json can't serialise BigInt (device uptime columns). Device times
// stay far below 2^53 ms, so plain numbers are exact.
export function toJson(value: unknown): Response {
  return new Response(
    JSON.stringify(value, (_key, v) => (typeof v === "bigint" ? Number(v) : v)),
    { headers: { "content-type": "application/json" } },
  );
}
