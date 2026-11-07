// SmolBugs BLE vibration protocol: frame encoding.
//
// Pure C++ (no Arduino dependencies) so it can be unit-tested on a PC.
// Must stay byte-for-byte compatible with processing/ble_protocol.py.
// All multi-byte values are little-endian.
//
// START frame (17 bytes), once per window:
//   u8 type=0x01 | u16 window_id | f32 sample_rate_hz | u16 n_samples |
//   u8 samples_per_chunk | u8 range_g | u16 lsb_per_g | u32 first_sample_ms
//
// DATA frame (5 + 6*k bytes; 197 when full):
//   u8 type=0x02 | u16 window_id | u16 chunk | k x (i16 x, i16 y, i16 z)

#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace smolbugs {

constexpr uint8_t FRAME_START = 0x01;
constexpr uint8_t FRAME_DATA = 0x02;
constexpr size_t START_FRAME_SIZE = 17;
constexpr size_t DATA_HEADER_SIZE = 5;
constexpr size_t BYTES_PER_SAMPLE = 6;
constexpr size_t SAMPLES_PER_CHUNK = 32;
constexpr size_t MAX_DATA_FRAME_SIZE = DATA_HEADER_SIZE + SAMPLES_PER_CHUNK * BYTES_PER_SAMPLE;  // 197
constexpr uint16_t MIN_MTU = MAX_DATA_FRAME_SIZE + 3;  // notify payload = MTU - 3

// int16 counts per g after shifting ADXL355 20-bit data right by 4 bits.
inline uint16_t lsbPerG(uint8_t rangeG) {
    switch (rangeG) {
        case 2: return 16000;
        case 4: return 8000;
        case 8: return 4000;
        default: return 0;
    }
}

inline void putU16(uint8_t* p, uint16_t v) {
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)(v >> 8);
}

inline void putU32(uint8_t* p, uint32_t v) {
    for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i));
}

inline void putF32(uint8_t* p, float f) {
    uint32_t bits;
    memcpy(&bits, &f, sizeof bits);  // IEEE-754 single, as Python's struct 'f'
    putU32(p, bits);
}

inline uint16_t chunkCount(uint16_t nSamples, uint8_t samplesPerChunk) {
    return (uint16_t)((nSamples + samplesPerChunk - 1) / samplesPerChunk);
}

// Convert g to int16 counts, rounding and saturating like the Python encoder.
inline int16_t gToCounts(float g, uint16_t lsb) {
    float v = g * (float)lsb;
    v = v >= 0 ? v + 0.5f : v - 0.5f;
    if (v > 32767.0f) return 32767;
    if (v < -32768.0f) return -32768;
    return (int16_t)v;
}

// ADXL355 raw 20-bit reading (already sign-extended to int32) -> int16 counts.
inline int16_t adxl355ToCounts(int32_t raw20) {
    int32_t v = raw20 >> 4;  // arithmetic shift keeps the sign
    if (v > 32767) return 32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}

// Returns bytes written (always START_FRAME_SIZE).
inline size_t encodeStart(uint8_t* out, uint16_t windowId, float sampleRateHz, uint16_t nSamples,
                          uint8_t samplesPerChunk, uint8_t rangeG, uint16_t lsb, uint32_t firstSampleMs) {
    out[0] = FRAME_START;
    putU16(out + 1, windowId);
    putF32(out + 3, sampleRateHz);
    putU16(out + 7, nSamples);
    out[9] = samplesPerChunk;
    out[10] = rangeG;
    putU16(out + 11, lsb);
    putU32(out + 13, firstSampleMs);
    return START_FRAME_SIZE;
}

// samples: count rows of {x, y, z}. Returns bytes written, or 0 if count is too large.
inline size_t encodeData(uint8_t* out, uint16_t windowId, uint16_t chunk,
                         const int16_t (*samples)[3], size_t count) {
    if (count == 0 || count > SAMPLES_PER_CHUNK) return 0;
    out[0] = FRAME_DATA;
    putU16(out + 1, windowId);
    putU16(out + 3, chunk);
    uint8_t* p = out + DATA_HEADER_SIZE;
    for (size_t i = 0; i < count; i++) {
        for (int a = 0; a < 3; a++) {
            putU16(p, (uint16_t)samples[i][a]);
            p += 2;
        }
    }
    return DATA_HEADER_SIZE + count * BYTES_PER_SAMPLE;
}

}  // namespace smolbugs
