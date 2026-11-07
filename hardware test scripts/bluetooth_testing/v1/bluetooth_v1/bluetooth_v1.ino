
#include <Arduino.h>

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// BLE device configuration
#define DEVICE_NAME "MachineGuard"

// Nordic UART Service UUID
#define SERVICE_UUID \
    "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"

// TX characteristic: ESP32 -> connected device
#define CHARACTERISTIC_UUID_TX \
    "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

// BLE objects
BLEServer* bleServer = nullptr;
BLECharacteristic* txCharacteristic = nullptr;

// Connection state
bool deviceConnected = false;

// Transmission configuration
unsigned long lastTransmission = 0;
const unsigned long transmissionInterval = 1000;

uint32_t packetCounter = 0;


// Handle BLE connection events
class ServerCallbacks : public BLEServerCallbacks {

    void onConnect(BLEServer* server) override {
        deviceConnected = true;

        Serial.println("BLE client connected");
    }

    void onDisconnect(BLEServer* server) override {
        deviceConnected = false;

        Serial.println("BLE client disconnected");

        // Resume advertising
        BLEDevice::startAdvertising();
    }
};


void setup() {

    Serial.begin(115200);
    delay(1000);

    Serial.println();
    Serial.println("MachineGuard BLE Test");
    Serial.println("---------------------");

    // Initialize BLE
    BLEDevice::init(DEVICE_NAME);

    // Create BLE server
    bleServer = BLEDevice::createServer();

    bleServer->setCallbacks(new ServerCallbacks());

    // Create UART service
    BLEService* uartService =
        bleServer->createService(SERVICE_UUID);

    // Create TX characteristic
    txCharacteristic =
        uartService->createCharacteristic(
            CHARACTERISTIC_UUID_TX,
            BLECharacteristic::PROPERTY_NOTIFY
        );

    // Enable notification support
    txCharacteristic->addDescriptor(
        new BLE2902()
    );

    // Start service
    uartService->start();

    // Start advertising
    BLEDevice::startAdvertising();

    Serial.println("BLE initialized");
    Serial.println("Device name: MachineGuard");
    Serial.println("Waiting for connection...");
}


void loop() {

    if (deviceConnected) {

        if (millis() - lastTransmission >=
            transmissionInterval) {

            lastTransmission = millis();

            packetCounter++;

            // Construct message
            String message =
                "MachineGuard BLE Test | Packet: " +
                String(packetCounter);

            // Send notification
            txCharacteristic->setValue(
                message.c_str()
            );

            txCharacteristic->notify();

            // Serial debugging
            Serial.print("Sent: ");
            Serial.println(message);
        }
    }

    delay(10);
}
