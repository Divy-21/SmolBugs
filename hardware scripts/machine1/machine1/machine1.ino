
#include <Arduino.h>
#include "HT_SSD1306Wire.h"

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// =====================================================
// OLED CONFIGURATION
// =====================================================

static SSD1306Wire display(
    0x3c,
    500000,
    SDA_OLED,
    SCL_OLED,
    GEOMETRY_128_64,
    RST_OLED
);


// =====================================================
// THERMISTOR CONFIGURATION
// =====================================================

const int THERMISTOR_PIN = 1;

const float FIXED_RESISTOR = 75000.0; // 75 kOhm
const float SUPPLY_VOLTAGE = 3.3;     // 3.3 V

const unsigned long SENSOR_INTERVAL = 500;

unsigned long lastSensorUpdate = 0;


// =====================================================
// BLE CONFIGURATION
// =====================================================

#define DEVICE_NAME "MachineGuard"

#define SERVICE_UUID \
    "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"

#define CHARACTERISTIC_UUID_TX \
    "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

BLEServer* bleServer = nullptr;

BLECharacteristic* txCharacteristic = nullptr;

volatile bool deviceConnected = false;

uint32_t packetCounter = 0;


// =====================================================
// BLE CONNECTION CALLBACKS
// =====================================================

class ServerCallbacks : public BLEServerCallbacks {

    void onConnect(BLEServer* server) override {

        deviceConnected = true;

        Serial.println("BLE client connected");
    }

    void onDisconnect(BLEServer* server) override {

        deviceConnected = false;

        Serial.println("BLE client disconnected");

        BLEDevice::startAdvertising();
    }
};


// =====================================================
// OLED POWER
// =====================================================

void VextON() {

    pinMode(Vext, OUTPUT);

    digitalWrite(Vext, LOW);
}


// =====================================================
// BLE INITIALIZATION
// =====================================================

void initializeBLE() {

    BLEDevice::init(DEVICE_NAME);

    bleServer = BLEDevice::createServer();

    bleServer->setCallbacks(
        new ServerCallbacks()
    );

    BLEService* uartService =
        bleServer->createService(SERVICE_UUID);

    txCharacteristic =
        uartService->createCharacteristic(
            CHARACTERISTIC_UUID_TX,
            BLECharacteristic::PROPERTY_NOTIFY
        );

    txCharacteristic->addDescriptor(
        new BLE2902()
    );

    uartService->start();

    BLEDevice::startAdvertising();

    Serial.println("BLE initialized");
    Serial.println("Device name: MachineGuard");
    Serial.println("Waiting for BLE connection...");
}


// =====================================================
// THERMISTOR READING
// =====================================================

uint32_t readThermistorVoltage() {

    // Average 10 ADC readings to reduce random noise.

    uint32_t totalMv = 0;

    const int samples = 10;

    for (int i = 0; i < samples; i++) {

        totalMv += analogReadMilliVolts(
            THERMISTOR_PIN
        );

        delay(2);
    }

    return totalMv / samples;
}


void readThermistor() {

    uint32_t voltageMv = readThermistorVoltage();

    float voltage = voltageMv / 1000.0;

    float resistance = 0;

    // Avoid invalid calculations near the supply rails.

    if (voltage > 0.01 &&
        voltage < SUPPLY_VOLTAGE - 0.01) {

        // Assumes:
        // 3.3 V -> 75 kOhm fixed resistor
        // GPIO1 -> NTC thermistor -> GND

        resistance = FIXED_RESISTOR *
            (voltage / (SUPPLY_VOLTAGE - voltage));
    }


    // -------------------------------------------------
    // SERIAL OUTPUT
    // -------------------------------------------------

    Serial.print("Voltage: ");
    Serial.print(voltageMv);
    Serial.print(" mV | Resistance: ");

    if (resistance > 0) {

        Serial.print(resistance / 1000.0, 2);
        Serial.println(" kOhm");

    } else {

        Serial.println("Invalid reading");
    }


    // -------------------------------------------------
    // OLED OUTPUT
    // -------------------------------------------------

    display.clear();

    display.drawString(0, 0, "MachineGuard");

    display.drawString(0, 13, "Thermistor Monitor");

    display.drawString(
        0,
        27,
        "Voltage: " + String(voltageMv) + " mV"
    );

    if (resistance > 0) {

        display.drawString(
            0,
            41,
            "Resistance: " +
            String(resistance / 1000.0, 2) +
            " kOhm"
        );

    } else {

        display.drawString(
            0,
            41,
            "Invalid reading"
        );
    }

    display.drawString(
        0,
        54,
        deviceConnected ? "BLE: Connected" : "BLE: Waiting"
    );

    display.display();


    // -------------------------------------------------
    // BLE TRANSMISSION
    // -------------------------------------------------

    if (deviceConnected) {

        packetCounter++;

        String message =
            String(packetCounter) + "," +
            String(voltageMv) + "," +
            String((uint32_t)resistance);

        txCharacteristic->setValue(
            message.c_str()
        );

        txCharacteristic->notify();

        Serial.print("BLE sent: ");
        Serial.println(message);
    }
}


// =====================================================
// SETUP
// =====================================================

void setup() {

    Serial.begin(115200);

    delay(1000);

    Serial.println();
    Serial.println("MachineGuard Thermistor + BLE");
    Serial.println("--------------------------------");


    // Initialize OLED

    VextON();

    delay(100);

    display.init();

    display.clear();

    display.setFont(ArialMT_Plain_10);


    // Initialize ADC

    analogReadResolution(12);

    analogSetPinAttenuation(
        THERMISTOR_PIN,
        ADC_11db
    );


    // Initialize BLE

    initializeBLE();


    // Initial OLED screen

    display.drawString(0, 0, "MachineGuard");

    display.drawString(0, 16, "Thermistor + BLE");

    display.drawString(0, 32, "Initializing...");

    display.display();


    Serial.println("Thermistor initialized");
    Serial.println("System ready");
}


// =====================================================
// MAIN LOOP
// =====================================================

void loop() {

    if (millis() - lastSensorUpdate >= SENSOR_INTERVAL) {

        lastSensorUpdate = millis();

        readThermistor();
    }

    delay(10);
}
