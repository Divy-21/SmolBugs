// MachineGuard machine1: thermistor + LSM303D accelerometer over Bluetooth (BLE)
//
// Characteristics (Nordic UART base UUID):
//   6E400003  status     text "sequence,voltage_mV,x_g,y_g,z_g" every 500 ms
//   6E400004  vibration  binary START + DATA frames (smolbugs_protocol.h):
//                        one 4096-sample window at ~1600 Hz every VIB_INTERVAL_MS
//
// smolbugs_protocol.h must be in the same folder as this sketch.

#include <Arduino.h>
#include "HT_SSD1306Wire.h"

#include <Wire.h>

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

#include "smolbugs_protocol.h"

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

// FIFO registers, used while capturing vibration windows.
#define LSM303D_CTRL0     0x1F
#define LSM303D_FIFO_CTRL 0x2E
#define LSM303D_FIFO_SRC  0x2F


// =====================================================
// VIBRATION CAPTURE CONFIGURATION
// =====================================================

const float    VIB_NOMINAL_RATE_HZ = 1600.0f; // CTRL1 = 0xA7
const uint16_t VIB_WINDOW_SAMPLES  = 4096;    // 2.56 s at 1600 Hz, 0.39 Hz resolution
const uint8_t  VIB_RANGE_G         = 4;       // CTRL2 full scale
const uint16_t VIB_LSB_PER_G       = 8197;    // 1 / 0.122 mg per LSB at +/-4 g

const unsigned long VIB_INTERVAL_MS  = 10000; // one window every 10 s
const unsigned long VIB_FRAME_GAP_MS = 10;    // pause between DATA frames

// 4096 x 3 x 2 bytes = 24 KB of RAM.
static int16_t vibBuffer[VIB_WINDOW_SAMPLES][3];

uint16_t vibWindowId = 0;
uint32_t vibWindowsSent = 0;
uint32_t vibCaptureFailures = 0;
uint32_t vibNotifyErrors = 0;
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
BLE2902* vibCccd = nullptr;

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

// Counts failed vibration notifications so VIB_FRAME_GAP_MS can be tuned.
class VibrationCallbacks : public BLECharacteristicCallbacks {

    void onStatus(BLECharacteristic* characteristic, Status s, uint32_t code) override {

        if (s != SUCCESS_NOTIFY) {
            vibNotifyErrors++;
        }
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

    // Ask for a large MTU so a 197-byte DATA frame fits in one notification.
    BLEDevice::setMTU(247);

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

    vibCccd = new BLE2902();

    vibCharacteristic->addDescriptor(vibCccd);

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
    // Anti-alias filter 362 Hz (bits 7:6 = 10), full scale +/- 4g.
    // At 1600 Hz the FFT can only represent up to 800 Hz; the filter stops
    // higher vibration from folding back as false peaks.

    if (!writeRegister(
        LSM303D_ADDRESS,
        LSM303D_CTRL2,
        0x88
    )) {
        return false;
    }

    // FIFO off until a vibration window is captured.

    writeRegister(LSM303D_ADDRESS, LSM303D_FIFO_CTRL, 0x00);
    writeRegister(LSM303D_ADDRESS, LSM303D_CTRL0, 0x00);

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
        42,
        "Vib windows: " + String(vibWindowsSent)
    );

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
// VIBRATION CAPTURE (LSM303D FIFO)
// =====================================================

// Records VIB_WINDOW_SAMPLES readings into vibBuffer using the sensor's 32-sample
// FIFO, so readings keep their exact 1600 Hz spacing even if the ESP32 is briefly
// busy. Also measures the sensor's real output rate (it can be a few % off).
bool captureWindow(uint32_t* firstSampleMs, float* measuredRateHz) {

    // Reset the FIFO: bypass clears it, stream mode starts collecting.
    if (!writeRegister(LSM303D_ADDRESS, LSM303D_FIFO_CTRL, 0x00) ||
        !writeRegister(LSM303D_ADDRESS, LSM303D_CTRL0, 0x40) ||       // FIFO_EN
        !writeRegister(LSM303D_ADDRESS, LSM303D_FIFO_CTRL, 0x40)) {   // stream mode
        return false;
    }

    *firstSampleMs = millis();
    uint32_t startUs = micros();
    uint32_t timeoutUs = (uint32_t)(2.0f * VIB_WINDOW_SAMPLES / VIB_NOMINAL_RATE_HZ * 1e6f);

    uint16_t collected = 0;
    bool ok = true;

    while (collected < VIB_WINDOW_SAMPLES) {

        uint8_t src = readRegister(LSM303D_ADDRESS, LSM303D_FIFO_SRC);

        if (src & 0x40) {
            Serial.println("Vibration capture: FIFO overrun (readings lost)");
            ok = false;
            break;
        }

        uint8_t available = src & 0x1F;

        if (available == 0 && !(src & 0x20)) {
            available = 32;  // not empty with a count of 0 means completely full
        }

        for (uint8_t i = 0; i < available && collected < VIB_WINDOW_SAMPLES; i++) {

            uint8_t b[6];

            if (!readRegisters(LSM303D_ADDRESS, LSM303D_OUT_X_L, b, 6)) {
                Serial.println("Vibration capture: I2C read failed");
                ok = false;
                break;
            }

            vibBuffer[collected][0] = (int16_t)((b[1] << 8) | b[0]);
            vibBuffer[collected][1] = (int16_t)((b[3] << 8) | b[2]);
            vibBuffer[collected][2] = (int16_t)((b[5] << 8) | b[4]);
            collected++;
        }

        if (!ok) {
            break;
        }

        if (micros() - startUs > timeoutUs) {
            Serial.println("Vibration capture: timed out");
            ok = false;
            break;
        }
    }

    // Readings still waiting in the FIFO were also produced during this time.
    uint8_t pending = readRegister(LSM303D_ADDRESS, LSM303D_FIFO_SRC) & 0x1F;
    uint32_t elapsedUs = micros() - startUs;

    // Back to bypass so the 500 ms status readings see the latest value.
    writeRegister(LSM303D_ADDRESS, LSM303D_FIFO_CTRL, 0x00);
    writeRegister(LSM303D_ADDRESS, LSM303D_CTRL0, 0x00);

    if (!ok) {
        return false;
    }

    float rate = (collected + pending) / (elapsedUs / 1e6f);

    if (rate < 0.9f * VIB_NOMINAL_RATE_HZ || rate > 1.1f * VIB_NOMINAL_RATE_HZ) {

        Serial.print("Vibration capture: measured rate ");
        Serial.print(rate, 1);
        Serial.println(" Hz looks wrong; using nominal");

        rate = VIB_NOMINAL_RATE_HZ;
    }

    *measuredRateHz = rate;

    return true;
}


// =====================================================
// VIBRATION TRANSMISSION
// =====================================================

bool vibrationReady() {

    if (!accelerometerInitialized || !deviceConnected ||
        vibCccd == nullptr || !vibCccd->getNotifications()) {
        return false;  // no sensor, or the receiver hasn't subscribed
    }

    uint16_t mtu = bleServer->getPeerMTU(bleServer->getConnId());

    if (mtu < smolbugs::MIN_MTU) {

        // A smaller MTU would silently truncate frames, so don't send at all.
        Serial.print("Vibration skipped: MTU ");
        Serial.print(mtu);
        Serial.print(" < ");
        Serial.println(smolbugs::MIN_MTU);

        return false;
    }

    return true;
}


void sendVibrationWindow() {

    uint32_t firstSampleMs = 0;
    float rateHz = VIB_NOMINAL_RATE_HZ;

    if (!captureWindow(&firstSampleMs, &rateHz)) {
        vibCaptureFailures++;
        return;
    }

    uint8_t frame[smolbugs::MAX_DATA_FRAME_SIZE];

    unsigned long started = millis();
    uint32_t errorsBefore = vibNotifyErrors;

    size_t len = smolbugs::encodeStart(
        frame,
        vibWindowId,
        rateHz,
        VIB_WINDOW_SAMPLES,
        smolbugs::SAMPLES_PER_CHUNK,
        VIB_RANGE_G,
        VIB_LSB_PER_G,
        firstSampleMs
    );

    vibCharacteristic->setValue(frame, len);
    vibCharacteristic->notify();
    delay(VIB_FRAME_GAP_MS);

    uint16_t chunks = smolbugs::chunkCount(
        VIB_WINDOW_SAMPLES,
        smolbugs::SAMPLES_PER_CHUNK
    );

    for (uint16_t c = 0; c < chunks && deviceConnected; c++) {

        uint32_t offset = (uint32_t)c * smolbugs::SAMPLES_PER_CHUNK;
        uint32_t remaining = VIB_WINDOW_SAMPLES - offset;
        size_t count = remaining < smolbugs::SAMPLES_PER_CHUNK
            ? remaining
            : smolbugs::SAMPLES_PER_CHUNK;

        len = smolbugs::encodeData(
            frame,
            vibWindowId,
            c,
            &vibBuffer[offset],
            count
        );

        vibCharacteristic->setValue(frame, len);
        vibCharacteristic->notify();

        delay(VIB_FRAME_GAP_MS);
    }

    vibWindowsSent++;

    Serial.print("Vibration window ");
    Serial.print(vibWindowId);
    Serial.print(": ");
    Serial.print(rateHz, 1);
    Serial.print(" Hz, sent in ");
    Serial.print(millis() - started);
    Serial.print(" ms, notify errors: ");
    Serial.println(vibNotifyErrors - errorsBefore);

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

    if (millis() - lastVibWindow >= VIB_INTERVAL_MS) {

        lastVibWindow = millis();

        if (vibrationReady()) {
            sendVibrationWindow();
        }
    }

    delay(10);
}
