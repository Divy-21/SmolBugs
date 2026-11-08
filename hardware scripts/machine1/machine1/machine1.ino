#include <Arduino.h>
#include "HT_SSD1306Wire.h"

#include <Wire.h>

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

const unsigned long SENSOR_INTERVAL = 500;

unsigned long lastSensorUpdate = 0;


// =====================================================
// ACCELEROMETER CONFIGURATION
// =====================================================

// MinIMU-9 LSM303D

#define IMU_SDA 2
#define IMU_SCL 4

// The OLED already uses Wire (pins 17/18). Calling Wire.begin() again with other
// pins is ignored on the ESP32, so the IMU gets its own second I2C bus.
#define IMU_WIRE Wire1

#define LSM303D_ADDRESS 0x1D

#define LSM303D_WHO_AM_I 0x0F
#define LSM303D_CTRL1    0x20
#define LSM303D_CTRL2    0x21
#define LSM303D_OUT_X_L  0x28

// +/- 4g sensitivity: 0.122 mg/LSB

const float ACCEL_SENSITIVITY = 0.000122;

bool accelerometerInitialized = false;

float accelerationX = 0.0;
float accelerationY = 0.0;
float accelerationZ = 0.0;


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
// I2C REGISTER FUNCTIONS
// =====================================================

bool writeRegister(
    uint8_t address,
    uint8_t reg,
    uint8_t value
) {

    IMU_WIRE.beginTransmission(address);

    IMU_WIRE.write(reg);
    IMU_WIRE.write(value);

    return IMU_WIRE.endTransmission() == 0;
}


bool readRegisters(
    uint8_t address,
    uint8_t reg,
    uint8_t* buffer,
    size_t length
) {

    IMU_WIRE.beginTransmission(address);

    // Enable register auto-increment.
    IMU_WIRE.write(reg | 0x80);

    if (IMU_WIRE.endTransmission(false) != 0) {
        return false;
    }

    if (IMU_WIRE.requestFrom(address, length) != length) {
        return false;
    }

    for (size_t i = 0; i < length; i++) {
        buffer[i] = IMU_WIRE.read();
    }

    return true;
}


uint8_t readRegister(
    uint8_t address,
    uint8_t reg
) {

    IMU_WIRE.beginTransmission(address);

    IMU_WIRE.write(reg);

    if (IMU_WIRE.endTransmission(false) != 0) {
        return 0;
    }

    if (IMU_WIRE.requestFrom(address, (uint8_t)1) != 1) {
        return 0;
    }

    return IMU_WIRE.read();
}


// =====================================================
// I2C SCAN (debug: prints every device on the IMU bus)
// =====================================================

void scanIMUBus() {

    Serial.println("Scanning IMU I2C bus...");

    int found = 0;

    for (uint8_t address = 1; address < 127; address++) {

        IMU_WIRE.beginTransmission(address);

        if (IMU_WIRE.endTransmission() == 0) {

            Serial.print("Found device at 0x");
            Serial.println(address, HEX);

            found++;
        }
    }

    // MinIMU-9 v3 should show 0x1D (LSM303D) and 0x6B (L3GD20H).
    if (found == 0) {
        Serial.println("No devices found: check VIN, GND, SDA->GPIO2, SCL->GPIO4");
    }
}


// =====================================================
// ACCELEROMETER INITIALIZATION
// =====================================================

bool initializeAccelerometer() {

    Serial.println("Initializing LSM303D...");

    uint8_t deviceID = readRegister(
        LSM303D_ADDRESS,
        LSM303D_WHO_AM_I
    );

    Serial.print("LSM303D WHO_AM_I: 0x");
    Serial.println(deviceID, HEX);

    if (deviceID != 0x49) {

        Serial.println("ERROR: LSM303D not detected");

        return false;
    }

    // CTRL1 = 0xA7
    // 1600 Hz output data rate (fast enough for vibration).
    // Enable X, Y and Z axes.

    if (!writeRegister(
        LSM303D_ADDRESS,
        LSM303D_CTRL1,
        0xA7
    )) {
        return false;
    }

    // CTRL2 = 0x08
    // Accelerometer full scale: +/- 4g.

    if (!writeRegister(
        LSM303D_ADDRESS,
        LSM303D_CTRL2,
        0x08
    )) {
        return false;
    }

    Serial.println("LSM303D initialized");
    Serial.println("Range: +/- 4g");

    return true;
}


// =====================================================
// ACCELEROMETER READING
// =====================================================

bool readAccelerometer() {

    uint8_t buffer[6];

    if (!readRegisters(
        LSM303D_ADDRESS,
        LSM303D_OUT_X_L,
        buffer,
        6
    )) {
        return false;
    }

    int16_t rawX =
        (int16_t)((buffer[1] << 8) | buffer[0]);

    int16_t rawY =
        (int16_t)((buffer[3] << 8) | buffer[2]);

    int16_t rawZ =
        (int16_t)((buffer[5] << 8) | buffer[4]);

    // Convert raw readings into g.

    accelerationX = rawX * ACCEL_SENSITIVITY;
    accelerationY = rawY * ACCEL_SENSITIVITY;
    accelerationZ = rawZ * ACCEL_SENSITIVITY;

    return true;
}


// =====================================================
// THERMISTOR READING
// =====================================================

uint32_t readThermistorVoltage() {

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


// =====================================================
// SENSOR READING
// =====================================================

void readSensors() {

    // Read thermistor voltage.

    uint32_t voltageMv = readThermistorVoltage();


    // Read accelerometer.

    bool accelOK = false;

    if (accelerometerInitialized) {
        accelOK = readAccelerometer();
    }


    // -------------------------------------------------
    // SERIAL OUTPUT
    // -------------------------------------------------

    Serial.print("Thermistor: ");
    Serial.print(voltageMv);
    Serial.println(" mV");

    Serial.print("Acceleration: ");

    if (accelOK) {

        Serial.print("X=");
        Serial.print(accelerationX, 3);

        Serial.print(" Y=");
        Serial.print(accelerationY, 3);

        Serial.print(" Z=");
        Serial.print(accelerationZ, 3);

        Serial.println(" g");

    } else {

        Serial.println("ERROR");
    }


    // -------------------------------------------------
    // OLED OUTPUT
    // -------------------------------------------------

    display.clear();

    display.drawString(0, 0, "MachineGuard");

    display.drawString(
        0,
        16,
        "Thermistor: " + String(voltageMv) + " mV"
    );

    // All three acceleration axes on one line.

    if (accelOK) {

        String accelLine =
            "X:" + String(accelerationX, 2) +
            " Y:" + String(accelerationY, 2) +
            " Z:" + String(accelerationZ, 2);

        display.drawString(
            0,
            32,
            accelLine
        );

    } else {

        display.drawString(
            0,
            32,
            "Accelerometer: ERROR"
        );
    }

    display.drawString(
        0,
        52,
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
            String(accelerationX, 3) + "," +
            String(accelerationY, 3) + "," +
            String(accelerationZ, 3);

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
    Serial.println("MachineGuard Thermistor + Accelerometer");
    Serial.println("--------------------------------");


    // Initialize OLED.

    VextON();

    delay(100);

    display.init();

    display.clear();

    display.setFont(ArialMT_Plain_10);


    // Initialize ADC.

    analogReadResolution(12);

    analogSetPinAttenuation(
        THERMISTOR_PIN,
        ADC_11db
    );


    // Initialize accelerometer I2C.

    IMU_WIRE.begin(
        IMU_SDA,
        IMU_SCL,
        400000
    );

    scanIMUBus();

    accelerometerInitialized = initializeAccelerometer();


    // Initialize BLE.

    initializeBLE();


    // Initial OLED screen.

    display.drawString(0, 0, "MachineGuard");

    display.drawString(0, 16, "Thermistor + IMU");

    display.drawString(
        0,
        32,
        accelerometerInitialized
            ? "Accelerometer: Ready"
            : "Accelerometer: ERROR"
    );

    display.display();


    Serial.println("Thermistor initialized");

    Serial.println(
        accelerometerInitialized
            ? "Accelerometer initialized"
            : "Accelerometer initialization failed"
    );

    Serial.println("System ready");
}


// =====================================================
// MAIN LOOP
// =====================================================

void loop() {

    if (millis() - lastSensorUpdate >= SENSOR_INTERVAL) {

        lastSensorUpdate = millis();

        readSensors();
    }

    delay(10);  
}
