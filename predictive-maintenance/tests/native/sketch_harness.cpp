// Runs machine1.ino on a PC with the stubs above: connects a fake receiver, enables
// vibration notifications, runs loop() for ~25 simulated seconds, and writes every
// vibration frame to stdout as [u16 length][bytes]. Temperature messages go to stderr.
#include <stdio.h>
#include <string.h>
#include "Arduino.h"
#include "BLEDevice.h"

namespace stub {
uint32_t now_ms = 0;
uint32_t rng = 12345;
uint16_t peer_mtu = 247;
std::function<void(const char*, const uint8_t*, size_t)> on_notify;
}
SerialStub Serial;
const uint8_t ArialMT_Plain_10[] = {0};
BLEServer* BLEDevice::server = nullptr;

#include "machine1.ino"

int main(int argc, char** argv) {
    if (argc > 1) stub::peer_mtu = (uint16_t)atoi(argv[1]);
    stub::on_notify = [](const char* uuid, const uint8_t* d, size_t n) {
        if (strcmp(uuid, CHARACTERISTIC_UUID_VIBRATION) == 0) {
            uint8_t h[2] = {(uint8_t)(n & 0xFF), (uint8_t)(n >> 8)};
            fwrite(h, 1, 2, stdout);
            fwrite(d, 1, n, stdout);
        } else {
            fprintf(stderr, "TEMP %.*s\n", (int)n, (const char*)d);
        }
    };
    setup();
    BLEDevice::server->cb->onConnect(BLEDevice::server);  // receiver connects
    vibCccd->notifications = true;                        // and subscribes
    while (stub::now_ms < 25000) loop();
    fprintf(stderr, "WINDOWS %u ERRORS %u\n", (unsigned)vibWindowsSent, (unsigned)vibNotifyErrors);
    return 0;
}
