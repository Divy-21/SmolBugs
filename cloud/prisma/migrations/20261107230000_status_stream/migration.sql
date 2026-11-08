-- AlterTable
ALTER TABLE "StatusPacket" ADD COLUMN     "abnormal" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN     "accelXG" DOUBLE PRECISION,
ADD COLUMN     "accelYG" DOUBLE PRECISION,
ADD COLUMN     "accelZG" DOUBLE PRECISION,
ADD COLUMN     "flags" JSONB;

-- AlterTable
ALTER TABLE "VibrationWindow" ADD COLUMN     "seq" INTEGER;

