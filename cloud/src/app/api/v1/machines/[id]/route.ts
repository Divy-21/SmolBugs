import { z } from "zod";
import { checkDeviceKey } from "@/lib/auth";
import { db } from "@/lib/db";

// PUT /api/v1/machines/MG-002  { "name": "Band saw", "kind": "band_saw", "rpm": 1450 }
// Registers a machine, or updates its details, so the dashboard can show a
// name instead of an ID. Needs the x-device-key header, like the ingest routes.
const details = z.strictObject({
  name: z.string().trim().min(1).max(80).optional(),
  kind: z.string().trim().min(1).max(40).optional(),
  rpm: z.number().positive().max(100_000).optional(),
});

const machineId = /^[A-Za-z0-9_.-]{1,64}$/;

export async function PUT(request: Request, ctx: RouteContext<"/api/v1/machines/[id]">) {
  const denied = checkDeviceKey(request);
  if (denied) return denied;

  const { id } = await ctx.params;
  if (!machineId.test(id)) {
    return Response.json({ ok: false, error: "machine_id may use letters, digits, _ . - only" }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: "Body is not valid JSON" }, { status: 400 });
  }
  const parsed = details.safeParse(body);
  if (!parsed.success) {
    return Response.json(
      { ok: false, error: "Details don't match the format", details: z.prettifyError(parsed.error) },
      { status: 400 },
    );
  }

  const machine = await db().machine.upsert({
    where: { id },
    create: { id, ...parsed.data },
    update: parsed.data,
  });
  return Response.json({ ok: true, machine });
}
