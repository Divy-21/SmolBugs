// Runs machine1.ino on a PC with the stand-ins in arduino_stubs/: a simulated LSM303D on
// Wire1 (GPIO2/4), a fake receiver that connects and subscribes, ~25 simulated seconds of
// loop(). Vibration frames go to stdout as [u16 length][bytes]; status messages and a
// summary go to stderr.
// Usage: sketch [mtu] [true_odr_hz] [stall_ms] [no_sensor]
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "Arduino.h"
#include "BLEDevice.h"
#include "Wire.h"

namespace stub {
uint64_t now_us = 0;
uint32_t rng = 12345;
uint16_t peer_mtu = 247;
std::function<void(const char*, const uint8_t*, size_t)> on_notify;
}
SerialStub Serial;
TwoWire Wire, Wire1;
const uint8_t ArialMT_Plain_10[] = {0};
BLEServer* BLEDevice::server = nullptr;

#include "machine1.ino"

int main(int argc, char** argv) {
    stub::FakeLSM303D imu;
    if (argc > 1) stub::peer_mtu = (uint16_t)atoi(argv[1]);
    if (argc > 2) imu.odr = atof(argv[2]);
    if (argc > 3 && atoi(argv[3]) > 0) { imu.stall_at_us = 10'500'000; imu.stall_us = (uint64_t)atoi(argv[3]) * 1000; }
    if (!(argc > 4 && atoi(argv[4]))) Wire1.dev = &imu;

    stub::on_notify = [](const char* uuid, const uint8_t* d, size_t n) {
        if (strcmp(uuid, CHARACTERISTIC_UUID_VIBRATION) == 0) {
            uint8_t h[2] = {(uint8_t)(n & 0xFF), (uint8_t)(n >> 8)};
            fwrite(h, 1, 2, stdout);
            fwrite(d, 1, n, stdout);
        } else {
            fprintf(stderr, "STATUS %.*s\n", (int)n, (const char*)d);
        }
    };
    setup();
    BLEDevice::server->cb->onConnect(BLEDevice::server);  // receiver connects
    vibCccd->notifications = true;                        // and subscribes
    while (stub::now_us < 25'000'000ULL) loop();
    fprintf(stderr, "INIT %d WINDOWS %u FAILURES %u ERRORS %u\n", (int)accelerometerInitialized,
            (unsigned)vibWindowsSent, (unsigned)vibCaptureFailures, (unsigned)vibNotifyErrors);
    return 0;
}
