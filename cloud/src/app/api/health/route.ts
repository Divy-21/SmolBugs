// For the load balancer's health check. Deliberately doesn't touch the
// database, so a slow database doesn't get a healthy app restarted.
export function GET() {
  return Response.json({ ok: true });
}
