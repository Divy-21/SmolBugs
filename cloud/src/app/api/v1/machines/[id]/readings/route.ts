import type { NextRequest } from "next/server";
import { db } from "@/lib/db";
import { toJson } from "@/lib/json";

// GET /api/v1/machines/MG-001/readings?minutes=10
// Status packets and vibration windows from the last N minutes (default 10,
// max 24 h), oldest first, for the dashboard's charts.
export async function GET(request: NextRequest, ctx: RouteContext<"/api/v1/machines/[id]/readings">) {
  const { id } = await ctx.params;
  const minutes = clamp(Number(request.nextUrl.searchParams.get("minutes") ?? 10), 1, 24 * 60);
  const since = new Date(Date.now() - minutes * 60_000);

  const machine = await db().machine.findUnique({ where: { id } });
  if (!machine) {
    return Response.json({ ok: false, error: `No machine called ${id}` }, { status: 404 });
  }

  // Newest first with a cap, then reversed, so a long range keeps the most
  // recent rows rather than the oldest. id breaks ties within one batch.
  const newest = [{ receivedAt: "desc" as const }, { id: "desc" as const }];
  const [status, windows] = await Promise.all([
    db().statusPacket.findMany({
      where: { machineId: id, receivedAt: { gte: since } },
      orderBy: newest,
      take: 5000,
      omit: { payload: true },
    }),
    db().vibrationWindow.findMany({
      where: { machineId: id, receivedAt: { gte: since } },
      orderBy: newest,
      take: 2000,
      omit: { payload: true },
    }),
  ]);
  return toJson({ machine, minutes, status: status.reverse(), windows: windows.reverse() });
}

function clamp(n: number, lo: number, hi: number) {
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : lo;
}
