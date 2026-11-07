// Signatures mirror arduino-esp32 libraries/BLE/src (checked against 2.0.17 and 3.2.0).
#pragma once
#include <functional>
#include <stdint.h>
#include <string.h>
#include <vector>
#include "Arduino.h"

typedef int esp_err_t;
class BLECharacteristic;
class BLEServer;

class BLEDescriptor { public: virtual ~BLEDescriptor() {} };

class BLE2902 : public BLEDescriptor {
public:
    bool notifications = false;
    bool getNotifications() { return notifications; }
};

class BLECharacteristicCallbacks {
public:
    typedef enum {
        SUCCESS_INDICATE, SUCCESS_NOTIFY, ERROR_INDICATE_DISABLED, ERROR_NOTIFY_DISABLED,
        ERROR_GATT, ERROR_NO_CLIENT, ERROR_INDICATE_TIMEOUT, ERROR_INDICATE_FAILURE
    } Status;
    virtual ~BLECharacteristicCallbacks() {}
    virtual void onStatus(BLECharacteristic* pCharacteristic, Status s, uint32_t code) {}
};

namespace stub {
extern std::function<void(const char* uuid, const uint8_t*, size_t)> on_notify;
extern uint16_t peer_mtu;
}

class BLECharacteristic {
public:
    static const uint32_t PROPERTY_NOTIFY = 1 << 4;
    std::string uuid;
    std::vector<uint8_t> value;
    BLECharacteristicCallbacks* cb = nullptr;
    std::vector<BLEDescriptor*> descriptors;
    explicit BLECharacteristic(const char* u) : uuid(u) {}
    void addDescriptor(BLEDescriptor* d) { descriptors.push_back(d); }
    void setCallbacks(BLECharacteristicCallbacks* c) { cb = c; }
    void setValue(uint8_t* data, size_t size) { value.assign(data, data + size); }
    void setValue(String v) { value.assign(v.s.begin(), v.s.end()); }
    void notify(bool is_notification = true) {
        size_t n = value.size();
        if (n > (size_t)(stub::peer_mtu - 3)) n = stub::peer_mtu - 3;  // real lib truncates too
        if (stub::on_notify) stub::on_notify(uuid.c_str(), value.data(), n);
        if (cb) cb->onStatus(this, BLECharacteristicCallbacks::SUCCESS_NOTIFY, 0);
    }
};

class BLEService {
public:
    std::vector<BLECharacteristic*> chars;
    BLECharacteristic* createCharacteristic(const char* uuid, uint32_t properties) {
        chars.push_back(new BLECharacteristic(uuid));
        return chars.back();
    }
    void start() {}
};

class BLEServerCallbacks {
public:
    virtual ~BLEServerCallbacks() {}
    virtual void onConnect(BLEServer* pServer) {}
    virtual void onDisconnect(BLEServer* pServer) {}
};

class BLEServer {
public:
    BLEServerCallbacks* cb = nullptr;
    void setCallbacks(BLEServerCallbacks* c) { cb = c; }
    BLEService* createService(const char*) { return new BLEService(); }
    uint16_t getConnId() { return 0; }
    uint16_t getPeerMTU(uint16_t conn_id) { return stub::peer_mtu; }
};

class BLEDevice {
public:
    static BLEServer* server;
    static void init(String deviceName) {}
    static esp_err_t setMTU(uint16_t mtu) { return 0; }
    static BLEServer* createServer() { server = new BLEServer(); return server; }
    static void startAdvertising() {}
};
