import { connection } from "next/server";
import { streamBuffer } from "@/lib/streamStore";

// GET the last ~70 s of status packets per machine, from memory (not yet in the
// database), plus the limits in use. The dashboard polls this every second.
// Empty right after a server restart until packets arrive again.
export async function GET() {
  await connection();
  return Response.json(streamBuffer().live());
}
