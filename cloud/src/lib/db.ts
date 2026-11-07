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
    globalForPrisma.prisma = new PrismaClient({
      adapter: new PrismaPg({ connectionString }),
    });
  }
  return globalForPrisma.prisma;
}
