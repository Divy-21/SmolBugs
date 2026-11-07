import { connection } from "next/server";
import { db } from "@/lib/db";
import { toJson } from "@/lib/json";

// GET every machine with its latest status packet and vibration window.
export async function GET() {
  await connection(); // always run at request time, never prerendered
  const machines = await db().machine.findMany({
    orderBy: { id: "asc" },
    include: {
      // id breaks ties: rows from one batch share the same receivedAt.
      statusPackets: { orderBy: [{ receivedAt: "desc" }, { id: "desc" }], take: 1 },
      windows: { orderBy: [{ receivedAt: "desc" }, { id: "desc" }], take: 1 },
    },
  });
  return toJson({
    machines: machines.map(({ statusPackets, windows, ...m }) => ({
      ...m,
      latestStatus: statusPackets[0] ?? null,
      latestWindow: windows[0] ?? null,
    })),
  });
}
