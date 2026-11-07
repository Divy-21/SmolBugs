// Native test for smolbugs_protocol.h. Built and run by tests/test_firmware_protocol.py.
// 1) runs unit checks (exit code 1 on failure)
// 2) writes encoded frames to stdout as [u16 length][bytes]... for byte-exact comparison
//    with the Python encoder.
// Usage: protocol_check <n_samples> <window_id>

#include <stdio.h>
#include <stdlib.h>

#include "smolbugs_protocol.h"

using namespace smolbugs;

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #cond); failures++; } } while (0)

static void unitChecks() {
    CHECK(lsbPerG(2) == 16000 && lsbPerG(4) == 8000 && lsbPerG(8) == 4000 && lsbPerG(3) == 0);
    CHECK(chunkCount(4096, 32) == 128);
    CHECK(chunkCount(1000, 32) == 32);
    CHECK(MAX_DATA_FRAME_SIZE == 197 && MIN_MTU == 200);

    CHECK(gToCounts(1.0f, 16000) == 16000);
    CHECK(gToCounts(-0.5f, 16000) == -8000);
    CHECK(gToCounts(0.00003f, 16000) == 0);
    CHECK(gToCounts(0.00004f, 16000) == 1);
    CHECK(gToCounts(5.0f, 16000) == 32767);    // saturates
    CHECK(gToCounts(-5.0f, 16000) == -32768);

    CHECK(adxl355ToCounts(256000) == 16000);   // +1 g at +/-2 g range
    CHECK(adxl355ToCounts(-256000) == -16000); // sign preserved
    CHECK(adxl355ToCounts(15) == 0);
    CHECK(adxl355ToCounts(-16) == -1);
    CHECK(adxl355ToCounts(524287) == 32767);   // max 20-bit

    uint8_t buf[MAX_DATA_FRAME_SIZE + 8];
    int16_t s[SAMPLES_PER_CHUNK + 1][3] = {};
    CHECK(encodeData(buf, 0, 0, s, 0) == 0);
    CHECK(encodeData(buf, 0, 0, s, SAMPLES_PER_CHUNK + 1) == 0);
    CHECK(encodeData(buf, 0, 0, s, SAMPLES_PER_CHUNK) == MAX_DATA_FRAME_SIZE);
    CHECK(encodeData(buf, 0, 0, s, 1) == DATA_HEADER_SIZE + BYTES_PER_SAMPLE);
}

// Deterministic pattern covering negatives and both int16 extremes.
static int16_t pattern(long i, int axis) {
    long v = (i * 2711L + axis * 9973L) % 65536L;
    return (int16_t)(v - 32768L);
}

static void writeFrame(const uint8_t* data, size_t len) {
    uint8_t hdr[2];
    putU16(hdr, (uint16_t)len);
    fwrite(hdr, 1, 2, stdout);
    fwrite(data, 1, len, stdout);
}

int main(int argc, char** argv) {
    unitChecks();
    if (failures) return 1;

    uint16_t n = (uint16_t)(argc > 1 ? atoi(argv[1]) : 4096);
    uint16_t windowId = (uint16_t)(argc > 2 ? atoi(argv[2]) : 7);

    uint8_t frame[MAX_DATA_FRAME_SIZE];
    size_t len = encodeStart(frame, windowId, 3998.5f, n, SAMPLES_PER_CHUNK, 2, lsbPerG(2), 123456789UL);
    writeFrame(frame, len);

    int16_t chunk[SAMPLES_PER_CHUNK][3];
    uint16_t chunks = chunkCount(n, SAMPLES_PER_CHUNK);
    for (uint16_t c = 0; c < chunks; c++) {
        size_t count = 0;
        for (size_t k = 0; k < SAMPLES_PER_CHUNK; k++) {
            long i = (long)c * SAMPLES_PER_CHUNK + (long)k;
            if (i >= n) break;
            for (int a = 0; a < 3; a++) chunk[k][a] = pattern(i, a);
            count++;
        }
        len = encodeData(frame, windowId, c, chunk, count);
        if (len == 0) return 2;
        writeFrame(frame, len);
    }
    return 0;
}
