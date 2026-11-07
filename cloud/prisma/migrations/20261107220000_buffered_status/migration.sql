-- AlterTable
ALTER TABLE "StatusPacket" ADD COLUMN     "abnormal" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN     "accelXG" DOUBLE PRECISION,
ADD COLUMN     "accelYG" DOUBLE PRECISION,
ADD COLUMN     "accelZG" DOUBLE PRECISION,
ADD COLUMN     "flags" JSONB,
ADD COLUMN     "packetId" TEXT;

-- AlterTable
ALTER TABLE "VibrationWindow" ADD COLUMN     "bootId" TEXT,
ADD COLUMN     "packetId" TEXT,
ADD COLUMN     "seq" INTEGER;

-- CreateIndex
CREATE UNIQUE INDEX "StatusPacket_packetId_key" ON "StatusPacket"("packetId");

-- CreateIndex
CREATE UNIQUE INDEX "VibrationWindow_packetId_key" ON "VibrationWindow"("packetId");
