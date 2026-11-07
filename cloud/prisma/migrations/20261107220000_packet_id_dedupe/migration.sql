-- AlterTable
ALTER TABLE "StatusPacket" ADD COLUMN     "packetId" TEXT;

-- AlterTable
ALTER TABLE "VibrationWindow" ADD COLUMN     "bootId" TEXT,
ADD COLUMN     "packetId" TEXT;

-- CreateIndex
CREATE UNIQUE INDEX "StatusPacket_packetId_key" ON "StatusPacket"("packetId");

-- CreateIndex
CREATE UNIQUE INDEX "VibrationWindow_packetId_key" ON "VibrationWindow"("packetId");

