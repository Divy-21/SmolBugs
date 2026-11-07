-- CreateTable
CREATE TABLE "Machine" (
    "id" TEXT NOT NULL,
    "name" TEXT,
    "kind" TEXT,
    "rpm" DOUBLE PRECISION,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "lastSeenAt" TIMESTAMP(3),

    CONSTRAINT "Machine_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "StatusPacket" (
    "id" SERIAL NOT NULL,
    "machineId" TEXT NOT NULL,
    "receivedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deviceTimeMs" BIGINT,
    "bootId" TEXT,
    "seq" INTEGER,
    "thermistorMv" INTEGER,
    "temperatureC" DOUBLE PRECISION,
    "payload" JSONB NOT NULL,

    CONSTRAINT "StatusPacket_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "VibrationWindow" (
    "id" SERIAL NOT NULL,
    "machineId" TEXT NOT NULL,
    "receivedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "windowId" INTEGER NOT NULL,
    "firstSampleMs" BIGINT,
    "sampleRateHz" DOUBLE PRECISION NOT NULL,
    "nSamples" INTEGER NOT NULL,
    "clippedFraction" DOUBLE PRECISION,
    "dominantFreqHz" DOUBLE PRECISION,
    "dominantAmpG" DOUBLE PRECISION,
    "velocityRmsMmS" DOUBLE PRECISION,
    "temperatureC" DOUBLE PRECISION,
    "health" DOUBLE PRECISION,
    "warnings" JSONB,
    "axes" JSONB NOT NULL,
    "payload" JSONB NOT NULL,

    CONSTRAINT "VibrationWindow_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Alert" (
    "id" SERIAL NOT NULL,
    "machineId" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "severity" TEXT NOT NULL,
    "kind" TEXT NOT NULL,
    "message" TEXT NOT NULL,
    "resolvedAt" TIMESTAMP(3),

    CONSTRAINT "Alert_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "StatusPacket_machineId_receivedAt_idx" ON "StatusPacket"("machineId", "receivedAt");

-- CreateIndex
CREATE INDEX "VibrationWindow_machineId_receivedAt_idx" ON "VibrationWindow"("machineId", "receivedAt");

-- CreateIndex
CREATE INDEX "Alert_machineId_createdAt_idx" ON "Alert"("machineId", "createdAt");

-- AddForeignKey
ALTER TABLE "StatusPacket" ADD CONSTRAINT "StatusPacket_machineId_fkey" FOREIGN KEY ("machineId") REFERENCES "Machine"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "VibrationWindow" ADD CONSTRAINT "VibrationWindow_machineId_fkey" FOREIGN KEY ("machineId") REFERENCES "Machine"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Alert" ADD CONSTRAINT "Alert_machineId_fkey" FOREIGN KEY ("machineId") REFERENCES "Machine"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
