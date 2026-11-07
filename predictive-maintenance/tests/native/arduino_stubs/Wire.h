// Host stand-in for arduino-esp32 TwoWire (signatures as in 3.2.0 libraries/Wire/src/Wire.h)
// with a simulated LSM303D: real-time sample generation at its own ODR, 32-sample FIFO in
// stream mode, overrun flag, and I2C transfer time at 400 kHz. Testing only.
#pragma once
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <vector>
#include "Arduino.h"

namespace stub {
struct FakeLSM303D {
    uint8_t address = 0x1D;
    double odr = 1600.0;          // the sensor's true rate (can differ from nominal)
    double lsb_g = 0.000122;
    // signal: gravity + tones (freq Hz, amplitude g) on x
    double gx = 0.17, gy = 0.38, gz = -0.80;
    double tone1_hz = 49.6, tone1_g = 0.10, tone2_hz = 99.2, tone2_g = 0.02;
    uint8_t regs[256] = {};
    bool streaming = false;
    int64_t stream_start_index = 0;  // index of last sample produced before stream start
    int64_t popped = 0;
    bool overrun = false;
    uint64_t stall_at_us = 0, stall_us = 0;  // optional one-off stall injected into the bus

    FakeLSM303D() { regs[0x0F] = 0x49; }
    int64_t produced_index() const { return (int64_t)floor(stub::now_us / 1e6 * odr); }
    int16_t raw(int64_t k, int axis) const {
        double t = k / odr, g;
        if (axis == 0) g = gx + tone1_g * sin(2 * M_PI * tone1_hz * t) + tone2_g * sin(2 * M_PI * tone2_hz * t);
        else if (axis == 1) g = gy + 0.5 * tone1_g * sin(2 * M_PI * tone1_hz * t);
        else g = gz;
        double v = round(g / lsb_g);
        return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
    }
    bool fifo_mode() const { return (regs[0x1F] & 0x40) && (regs[0x2E] & 0xE0) == 0x40; }
    int64_t fifo_count() {
        int64_t n = produced_index() - stream_start_index - popped;
        if (n > 32) { overrun = true; popped += n - 32; n = 32; }
        return n;
    }
    void write_reg(uint8_t r, uint8_t v) {
        regs[r] = v;
        if (r == 0x2E || r == 0x1F) {
            bool s = fifo_mode();
            if (s && !streaming) { stream_start_index = produced_index(); popped = 0; overrun = false; }
            streaming = s;
        }
    }
    uint8_t read_reg(uint8_t r, int64_t& sample, int& byte) {
        if (r == 0x2F) {
            int64_t n = streaming ? fifo_count() : 0;
            return (uint8_t)((overrun ? 0x40 : 0) | (n == 0 ? 0x20 : 0) | (n & 0x1F));
        }
        if (r >= 0x28 && r <= 0x2D) {
            if (byte == 0) {
                if (streaming) {
                    fifo_count();
                    sample = stream_start_index + popped + 1;
                    if (produced_index() >= sample) popped++;  // reading pops one sample
                } else {
                    sample = produced_index();
                }
            }
            int16_t v = raw(sample, (r - 0x28) / 2);
            return (r & 1) ? (uint8_t)((uint16_t)v >> 8) : (uint8_t)(v & 0xFF);
        }
        return regs[r];
    }
};
}  // namespace stub

class TwoWire {
public:
    stub::FakeLSM303D* dev = nullptr;
    bool started = false;
    int sda = -1, scl = -1;
    uint8_t tx_addr = 0;
    std::vector<uint8_t> tx, rx;
    size_t rx_pos = 0;
    uint8_t ptr = 0;
    bool auto_inc = false;

    bool begin(int s, int c, uint32_t frequency = 0) {
        if (started) return true;  // like the real library: already-started bus keeps its pins
        started = true; sda = s; scl = c;
        return true;
    }
    void beginTransmission(uint8_t address) { tx_addr = address; tx.clear(); }
    size_t write(uint8_t b) { tx.push_back(b); return 1; }
    uint8_t endTransmission(bool stopBit) {
        bus_time(tx.size() + 1);
        if (!present(tx_addr)) return 2;
        if (!tx.empty()) { ptr = tx[0] & 0x7F; auto_inc = tx[0] & 0x80; }
        for (size_t i = 1; i < tx.size(); i++) { dev->write_reg(ptr, tx[i]); if (auto_inc) ptr++; }
        return 0;
    }
    uint8_t endTransmission() { return endTransmission(true); }
    size_t requestFrom(uint8_t address, size_t len, bool stopBit) {
        bus_time(len + 1);
        if (!present(address)) return 0;
        rx.clear(); rx_pos = 0;
        int64_t sample = 0;
        for (size_t i = 0; i < len; i++) {
            int byte = (int)i;
            uint8_t r = (uint8_t)(ptr + (auto_inc ? i : 0));
            if (r < 0x28 || r > 0x2D) byte = 0;
            rx.push_back(dev->read_reg(r, sample, byte));
        }
        return len;
    }
    size_t requestFrom(uint8_t address, size_t len) { return requestFrom(address, len, true); }
    int read() { return rx_pos < rx.size() ? rx[rx_pos++] : -1; }

private:
    bool present(uint8_t a) const { return started && dev && dev->address == a && sda == 2 && scl == 4; }
    void bus_time(size_t bytes) {
        stub::now_us += (uint64_t)(bytes * 9 * 1e6 / 400000.0) + 20;  // 400 kHz + overhead
        if (dev && dev->stall_us && stub::now_us >= dev->stall_at_us) {
            stub::now_us += dev->stall_us;  // e.g. the BLE stack holding the CPU
            dev->stall_us = 0;
        }
    }
};
extern TwoWire Wire, Wire1;
