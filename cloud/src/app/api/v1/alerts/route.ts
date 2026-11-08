import type { NextRequest } from "next/server";
import { connection } from "next/server";
import { db } from "@/lib/db";

// GET /api/v1/alerts?machine_id=MG-001&limit=50
// Alerts newest first (open and resolved), for the dashboard's event history.
export async function GET(request: NextRequest) {
  await connection();
  const params = request.nextUrl.searchParams;
  const machineId = params.get("machine_id");
  const limit = Math.min(200, Math.max(1, Number(params.get("limit")) || 50));
  const alerts = await db().alert.findMany({
    where: machineId ? { machineId } : undefined,
    orderBy: [{ createdAt: "desc" }, { id: "desc" }],
    take: limit,
  });
  return Response.json({ alerts });
}
