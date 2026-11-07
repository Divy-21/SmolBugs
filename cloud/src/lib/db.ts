import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "@/generated/prisma/client";

// One client per server process: each PrismaClient opens its own connection
// pool, and Next's dev server reloads modules on every edit.
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

// Created on first use, not at import, so `next build` works without a
// database (the Docker image is built before it knows DATABASE_URL).
export function db(): PrismaClient {
  if (!globalForPrisma.prisma) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) throw new Error("DATABASE_URL is not set");
    globalForPrisma.prisma = new PrismaClient({ adapter: adapterFor(connectionString) });
  }
  return globalForPrisma.prisma;
}

// The same DATABASE_URL feeds two readers: `prisma migrate` (Prisma's own
// engine, which understands sslmode/sslaccept) and node-postgres here. RDS
// forces SSL, and node-postgres would reject RDS's certificate authority, which
// isn't in Node's trust store. So the SSL settings are taken out of the URL and
// set directly: encrypted, but the certificate isn't verified.
// TODO before real use: ship the RDS CA bundle and verify it.
function adapterFor(connectionString: string): PrismaPg {
  const url = new URL(connectionString);
  const sslmode = url.searchParams.get("sslmode");
  url.searchParams.delete("sslmode");
  url.searchParams.delete("sslaccept");
  const ssl = sslmode && sslmode !== "disable" ? { rejectUnauthorized: false } : undefined;
  return new PrismaPg({ connectionString: url.toString(), ssl });
}
