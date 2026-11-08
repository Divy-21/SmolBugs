#ifndef SMOLBUGS_PROTOCOL_H
#define SMOLBUGS_PROTOCOL_H

#include <Arduino.h>
#include "HT_SSD1306Wire.h"

#include <Wire.h>

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// Vibration windows for training (frame format, and FIFO capture from the LSM303D).
#include "lsm303d_fifo.h"

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
// VIBRATION WINDOW CONFIGURATION
// =====================================================

// Every VIB_INTERVAL_MS, 4096 consecutive samples (2.56 s at 1600 Hz) are recorded
// from the sensor's FIFO, then sent over Bluetooth as 1 START + 128 DATA frames.
// The receiver (ingest/ble_receiver.py --out ...) saves each window for training.

const unsigned long VIB_INTERVAL_MS = 10000;

const uint16_t VIB_SAMPLES = 4096;

const uint8_t VIB_RANGE_G = 4;

// Raw readings are sent as they are; the receiver divides by this
// (1 / 0.000122 g per count, the same scale as ACCEL_SENSITIVITY).
const uint16_t VIB_LSB_PER_G = 8197;

// Pause between frames. Raise it if Serial shows notify errors.
const unsigned long VIB_FRAME_GAP_MS = 6;

static int16_t vibSamples[VIB_SAMPLES][3];   // 24 KB

uint16_t vibWindowId = 0;

uint32_t vibWindowsSent = 0;

volatile uint32_t vibNotifyErrors = 0;

unsigned long lastVibWindow = 0;


// =====================================================
// BLE CONFIGURATION
// =====================================================

#define DEVICE_NAME "MachineGuard"

#define SERVICE_UUID \
    "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"

#define CHARACTERISTIC_UUID_TX \
    "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

#define CHARACTERISTIC_UUID_VIBRATION \
    "6E400004-B5A3-F393-E0A9-E50E24DCCA9E"

BLEServer* bleServer = nullptr;

BLECharacteristic* txCharacteristic = nullptr;

BLECharacteristic* vibCharacteristic = nullptr;

volatile bool deviceConnected = false;

// True once the receiver has turned on vibration notifications.
volatile bool vibSubscribed = false;

// Arduino-ESP32 3.3 and newer use the NimBLE Bluetooth stack on the ESP32-S3: it adds
// the notifications on/off descriptor (0x2902) itself and reports it through
// onSubscribe(). Older versions use Bluedroid, where the descriptor is added by hand.
#if defined(CONFIG_BLUEDROID_ENABLED)
BLE2902* vibCccd = nullptr;
#endif

uint32_t packetCounter = 0;


// =====================================================
// BLE CONNECTION CALLBACKS
// =====================================================

class ServerCallbacks : public BLEServerCallbacks {

    void onConnect(BLEServer* server) override {

        deviceConnected = true;

        vibSubscribed = false;

        Serial.println("BLE client connected");
    }

    void onDisconnect(BLEServer* server) override {

        deviceConnected = false;

        vibSubscribed = false;

        Serial.println("BLE client disconnected");

        BLEDevice::startAdvertising();
    }
};


// Counts failed notifications (so VIB_FRAME_GAP_MS can be tuned)
// and tracks whether the receiver wants vibration windows.
class VibrationCallbacks : public BLECharacteristicCallbacks {

    void onStatus(BLECharacteristic* characteristic, Status s, uint32_t code) override {

        if (s != SUCCESS_NOTIFY && s != SUCCESS_INDICATE) {
            vibNotifyErrors = vibNotifyErrors + 1;
        }
    }

#if defined(CONFIG_NIMBLE_ENABLED)
    void onSubscribe(BLECharacteristic* characteristic, ble_gap_conn_desc* desc, uint16_t subValue) override {

        vibSubscribed = (subValue & 0x0001) != 0;

        Serial.println(vibSubscribed ? "Vibration notifications on" : "Vibration notifications off");
    }
#endif
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

    // A full vibration DATA frame (197 bytes) needs an MTU of at least 200.
    BLEDevice::setMTU(256);

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

    vibCharacteristic =
        uartService->createCharacteristic(
            CHARACTERISTIC_UUID_VIBRATION,
            BLECharacteristic::PROPERTY_NOTIFY
        );

#if defined(CONFIG_BLUEDROID_ENABLED)
    vibCccd = new BLE2902();

    vibCharacteristic->addDescriptor(vibCccd);
#endif

    vibCharacteristic->setCallbacks(
        new VibrationCallbacks()
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
// I2C ACCESS FOR THE FIFO CAPTURE (lsm303d_fifo.h)
// =====================================================

struct ImuBus {

    bool write(uint8_t reg, uint8_t value) {
        return writeRegister(LSM303D_ADDRESS, reg, value);
    }

    bool read(uint8_t reg, uint8_t* buffer, size_t length) {
        return readRegisters(LSM303D_ADDRESS, reg, buffer, length);
    }

    uint32_t micros() {
        return ::micros();
    }
};

ImuBus imuBus;


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

    // CTRL2 = 0x88
    // Anti-alias filter 362 Hz (bits 7:6 = 10). With the old 0x08 it was 773 Hz,
    // which is too close to the 800 Hz limit of 1600 Hz sampling, so higher
    // vibration could fold back into the measured range.
    // Accelerometer full scale: +/- 4g.

    if (!writeRegister(
        LSM303D_ADDRESS,
        LSM303D_CTRL2,
        0x88
    )) {
        return false;
    }

    // FIFO on (bypass mode), used to record vibration windows.

    if (!lsm303d::enableFifo(imuBus)) {
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
// VIBRATION WINDOWS
// =====================================================

bool vibrationWanted() {

#if defined(CONFIG_BLUEDROID_ENABLED)
    vibSubscribed = vibCccd != nullptr && vibCccd->getNotifications();
#endif

    return deviceConnected && vibSubscribed;
}


bool notifyVibrationFrame(const uint8_t* data, size_t length) {

    if (!deviceConnected) {
        return false;
    }

    vibCharacteristic->setValue((uint8_t*)data, length);

    vibCharacteristic->notify();

    delay(VIB_FRAME_GAP_MS);

    return true;
}


void captureAndSendVibrationWindow() {

    const uint32_t firstSampleMs = millis();

    // 1.5x the expected 2.56 s, so a stalled sensor cannot hang the loop.
    const uint32_t timeoutUs =
        (uint32_t)(VIB_SAMPLES / lsm303d::NOMINAL_RATE_HZ * 1.5f * 1e6f);

    lsm303d::Capture c = lsm303d::capture(
        imuBus,
        vibSamples,
        VIB_SAMPLES,
        timeoutUs
    );

    const unsigned long captureMs = millis() - firstSampleMs;

    if (c.error != lsm303d::Error::None) {

        Serial.printf(
            "Vibration capture failed after %u samples: %s\n",
            c.samples,
            lsm303d::errorText(c.error)
        );

        return;
    }

    if (c.rateHz < 0.9f * lsm303d::NOMINAL_RATE_HZ ||
        c.rateHz > 1.1f * lsm303d::NOMINAL_RATE_HZ) {

        Serial.printf("WARNING: measured sample rate %.1f Hz, expected about 1600 Hz\n", c.rateHz);
    }

    const unsigned long sendStart = millis();

    const uint32_t errorsBefore = vibNotifyErrors;

    uint8_t frame[smolbugs::MAX_DATA_FRAME_SIZE];

    size_t length = smolbugs::encodeStart(
        frame,
        vibWindowId,
        c.rateHz,
        VIB_SAMPLES,
        smolbugs::SAMPLES_PER_CHUNK,
        VIB_RANGE_G,
        VIB_LSB_PER_G,
        firstSampleMs
    );

    bool sent = notifyVibrationFrame(frame, length);

    const uint16_t chunks =
        smolbugs::chunkCount(VIB_SAMPLES, smolbugs::SAMPLES_PER_CHUNK);

    for (uint16_t chunk = 0; sent && chunk < chunks; chunk++) {

        const size_t first = (size_t)chunk * smolbugs::SAMPLES_PER_CHUNK;

        const size_t count = min(
            (size_t)smolbugs::SAMPLES_PER_CHUNK,
            (size_t)VIB_SAMPLES - first
        );

        length = smolbugs::encodeData(frame, vibWindowId, chunk, &vibSamples[first], count);

        sent = notifyVibrationFrame(frame, length);
    }

    if (sent) {

        vibWindowsSent++;

        Serial.printf(
            "Vibration window %u: %u samples at %.1f Hz, captured in %lu ms, "
            "sent in %lu ms, notify errors: %lu\n",
            vibWindowId,
            VIB_SAMPLES,
            c.rateHz,
            captureMs,
            millis() - sendStart,
            (unsigned long)(vibNotifyErrors - errorsBefore)
        );

    } else {

        Serial.printf("Vibration window %u not finished: BLE disconnected\n", vibWindowId);
    }

    vibWindowId++;  // uint16_t wraps at 65535, as the protocol expects
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

    // Only record when the receiver is listening: a window takes about 2.6 s
    // to record and 1 s to send, and status messages pause meanwhile.

    if (millis() - lastVibWindow >= VIB_INTERVAL_MS) {

        lastVibWindow = millis();

        if (accelerometerInitialized && vibrationWanted()) {
            captureAndSendVibrationWindow();
        }
    }

    delay(10);
}

#endif // SMOLBUGS_PROTOCOL_H
