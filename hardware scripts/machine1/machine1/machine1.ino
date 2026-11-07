// MachineGuard machine1: thermistor + vibration over Bluetooth (BLE)
//
// Characteristics (Nordic UART base UUID):
//   6E400003  temperature  text "sequence,voltage_mV,resistance_ohms" every 500 ms
//   6E400004  vibration    binary START + DATA frames, one window every VIB_INTERVAL_MS
//
// Vibration is capture-then-send: record a 4096-sample window into RAM, then send it
// as 1 START frame + 128 DATA frames of 32 samples each. Frame format: smolbugs_protocol.h
//
// Until the ADXL355 arrives, USE_TEST_SIGNAL generates a synthetic machine vibration
// so the whole Bluetooth path can be tested end to end.

#include <Arduino.h>
#include <Wire.h>
#include "HT_SSD1306Wire.h"

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>


// =====================================================
// MACHINE CONFIGURATION
// =====================================================

const char* MACHINE_ID = "MG-001";


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

const float FIXED_RESISTOR = 75000.0;
const float SUPPLY_VOLTAGE = 3.3;

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

#define CHARACTERISTIC_UUID_VIBRATION \
    "6E400004-B5A3-F393-E0A9-E50E24DCCA9E"

BLEServer* bleServer = nullptr;

BLECharacteristic* txCharacteristic = nullptr;
BLECharacteristic* vibCharacteristic = nullptr;
BLE2902* vibCccd = nullptr;

volatile bool deviceConnected = false;

uint32_t packetCounter = 0;

const size_t BLE_CHUNK_SIZE = 20;


// =====================================================
// BLE CALLBACKS
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

// Counts failed notifications so the frame gap can be tuned.
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
// I2C REGISTER FUNCTIONS
// =====================================================

bool writeRegister(
    uint8_t address,
    uint8_t reg,
    uint8_t value
) {

    Wire.beginTransmission(address);

    Wire.write(reg);
    Wire.write(value);

    return Wire.endTransmission() == 0;
}


bool readRegisters(
    uint8_t address,
    uint8_t reg,
    uint8_t* buffer,
    size_t length
) {

    Wire.beginTransmission(address);

    // Enable register address auto-increment.
    Wire.write(reg | 0x80);

    if (Wire.endTransmission(false) != 0) {
        return false;
    }

    size_t received = Wire.requestFrom(
        address,
        length
    );

    if (received != length) {
        return false;
    }

    for (size_t i = 0; i < length; i++) {
        buffer[i] = Wire.read();
    }

    return true;
}


uint8_t readRegister(
    uint8_t address,
    uint8_t reg
) {

    uint8_t value = 0;

    Wire.beginTransmission(address);

    Wire.write(reg);

    if (Wire.endTransmission(false) != 0) {
        return 0;
    }

    if (Wire.requestFrom(address, (uint8_t)1) != 1) {
        return 0;
    }

    value = Wire.read();

    return value;
}


// =====================================================
// MINIMU-9 INITIALIZATION
// =====================================================

bool initializeIMU() {

    Serial.println();
    Serial.println("Initializing MinIMU-9...");

    // Verify LSM303D identity.

    uint8_t accelID = readRegister(
        LSM303D_ADDRESS,
        LSM303D_WHO_AM_I
    );

    Serial.print("LSM303D WHO_AM_I: 0x");
    Serial.println(accelID, HEX);

    if (accelID != 0x49) {

        Serial.println("ERROR: LSM303D not detected");

        return false;
    }


    // Verify L3GD20H identity.

    uint8_t gyroID = readRegister(
        L3GD20H_ADDRESS,
        L3GD20H_WHO_AM_I
    );

    Serial.print("L3GD20H WHO_AM_I: 0x");
    Serial.println(gyroID, HEX);

    if (gyroID != 0xD7) {

        Serial.println("ERROR: L3GD20H not detected");

        return false;
    }


    // -------------------------------------------------
    // CONFIGURE ACCELEROMETER
    // -------------------------------------------------

    // CTRL1 = 0x57
    //
    // Output data rate: 50 Hz
    // X axis enabled
    // Y axis enabled
    // Z axis enabled

    if (!writeRegister(
        LSM303D_ADDRESS,
        LSM303D_CTRL1,
        0x57
    )) {
        return false;
    }


    // CTRL2 = 0x08
    //
    // Accelerometer full scale: +/- 4g

    if (!writeRegister(
        LSM303D_ADDRESS,
        LSM303D_CTRL2,
        0x08
    )) {
        return false;
    }


    // -------------------------------------------------
    // CONFIGURE GYROSCOPE
    // -------------------------------------------------

    // CTRL1 = 0x0F
    //
    // Output data rate: 95 Hz
    // Power on
    // X, Y, Z axes enabled

    if (!writeRegister(
        L3GD20H_ADDRESS,
        L3GD20H_CTRL1,
        0x0F
    )) {
        return false;
    }


    // CTRL4 = 0x00
    //
    // Full scale: +/- 245 degrees/second

    if (!writeRegister(
        L3GD20H_ADDRESS,
        L3GD20H_CTRL4,
        0x00
    )) {
        return false;
    }


    Serial.println("LSM303D initialized");
    Serial.println("L3GD20H initialized");

    Serial.println("Accelerometer: +/- 4g");
    Serial.println("Gyroscope: +/- 245 dps");

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

    // Combine low and high bytes.

    int16_t rawX =
        (int16_t)((buffer[1] << 8) | buffer[0]);

    int16_t rawY =
        (int16_t)((buffer[3] << 8) | buffer[2]);

    int16_t rawZ =
        (int16_t)((buffer[5] << 8) | buffer[4]);


    // Convert raw values into g.

    accelerationX = rawX * ACCEL_SENSITIVITY;

    accelerationY = rawY * ACCEL_SENSITIVITY;

    accelerationZ = rawZ * ACCEL_SENSITIVITY;

    return true;
}


// =====================================================
// GYROSCOPE READING
// =====================================================

bool readGyroscope() {

    uint8_t buffer[6];

    if (!readRegisters(
        L3GD20H_ADDRESS,
        L3GD20H_OUT_X_L,
        buffer,
        6
    )) {
        return false;
    }

    // Combine low and high bytes.

    int16_t rawX =
        (int16_t)((buffer[1] << 8) | buffer[0]);

    int16_t rawY =
        (int16_t)((buffer[3] << 8) | buffer[2]);

    int16_t rawZ =
        (int16_t)((buffer[5] << 8) | buffer[4]);


    // Convert raw values into degrees/second.

    gyroX = rawX * GYRO_SENSITIVITY;

    gyroY = rawY * GYRO_SENSITIVITY;

    gyroZ = rawZ * GYRO_SENSITIVITY;

    return true;
}


// =====================================================
// READ ALL IMU SENSORS
// =====================================================

bool readIMU() {

    if (!imuInitialized) {
        return false;
    }

    bool accelOK = readAccelerometer();

    bool gyroOK = readGyroscope();

    return accelOK && gyroOK;
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


void updateDisplay(uint32_t voltageMv, float resistance) {

    display.clear();

    display.drawString(0, 0, "MachineGuard");

    display.drawString(
        0,
        13,
        "Voltage: " + String(voltageMv) + " mV"
    );

    if (resistance > 0) {

        display.drawString(
            0,
            26,
            "Resistance: " +
            String(resistance / 1000.0, 2) +
            " kOhm"
        );

    } else {

        display.drawString(0, 26, "Invalid reading");
    }

    display.drawString(
        0,
        39,
        "Vib windows: " + String(vibWindowsSent)
    );

    display.drawString(
        0,
        52,
        deviceConnected ? "BLE: Connected" : "BLE: Waiting"
    );

    display.display();
}


// =====================================================
// JSON TRANSMISSION
// =====================================================

void sendJSON(const char* json) {

    if (!deviceConnected) {
        return;
    }

    size_t length = strlen(json);

    size_t offset = 0;

    while (offset < length && deviceConnected) {

        size_t chunkLength = min(
            BLE_CHUNK_SIZE,
            length - offset
        );

        txCharacteristic->setValue(
            (uint8_t*)(json + offset),
            chunkLength
        );

        txCharacteristic->notify();

        offset += chunkLength;

        delay(10);
    }
}


// =====================================================
// SENSOR DATA AND JSON GENERATION
// =====================================================

void readSensorsAndTransmit() {

    // -------------------------------------------------
    // READ THERMISTOR
    // -------------------------------------------------

    uint32_t voltageMv = readThermistorVoltage();

    float voltage = voltageMv / 1000.0;

    bool thermistorOK =
        voltage > 0.01 &&
        voltage < SUPPLY_VOLTAGE - 0.01;


    // -------------------------------------------------
    // READ IMU
    // -------------------------------------------------

    bool imuOK = readIMU();


    // Device uptime timestamp.

    uint32_t timestampMs = millis();


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
    // SERIAL DEBUG OUTPUT
    // -------------------------------------------------

    Serial.println();
    Serial.println("--------------------------------");

    Serial.print("Timestamp: ");
    Serial.print(timestampMs);
    Serial.println(" ms");

    Serial.print("Thermistor: ");
    Serial.print(voltageMv);
    Serial.print(" mV | ");
    Serial.println(thermistorOK ? "OK" : "ERROR");

    Serial.print("Accelerometer: ");

    if (imuOK) {

        Serial.print("X=");
        Serial.print(accelerationX, 3);

        Serial.print(" Y=");
        Serial.print(accelerationY, 3);

        Serial.print(" Z=");
        Serial.print(accelerationZ, 3);

        Serial.println(" g");

        Serial.print("Gyroscope: ");

        Serial.print("X=");
        Serial.print(gyroX, 2);

        Serial.print(" Y=");
        Serial.print(gyroY, 2);

        Serial.print(" Z=");
        Serial.print(gyroZ, 2);

        Serial.println(" dps");

    } else {

        Serial.println("ERROR: IMU unavailable");
    }


    // -------------------------------------------------
    // JSON GENERATION
    // -------------------------------------------------

    char json[384];

    snprintf(
        json,
        sizeof(json),

        "{\"machine_id\":\"%s\","
        "\"timestamp_ms\":%lu,"
        "\"sensors\":["

            "{\"id\":\"sensor_0\","
            "\"type\":\"thermistor\","
            "\"voltage_mv\":%lu,"

            "\"valid\":%s},"

            "{\"id\":\"sensor_1\","
            "\"type\":\"accelerometer\","
            "\"x_g\":%.3f,"
            "\"y_g\":%.3f,"
            "\"z_g\":%.3f,"
            "\"valid\":%s},"

            "{\"id\":\"sensor_2\","
            "\"type\":\"gyroscope\","
            "\"x_dps\":%.2f,"
            "\"y_dps\":%.2f,"
            "\"z_dps\":%.2f,"
            "\"valid\":%s}"

        "]}\n",

        MACHINE_ID,

        (unsigned long)timestampMs,

        (unsigned long)voltageMv,
        thermistorOK ? "true" : "false",

        accelerationX,
        accelerationY,
        accelerationZ,
        imuOK ? "true" : "false",

        gyroX,
        gyroY,
        gyroZ,
        imuOK ? "true" : "false"
    );


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

    vibWindowsSent++;

    Serial.print("Vibration window ");
    Serial.print(vibWindowId);
    Serial.print(" sent in ");
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
    Serial.println("MachineGuard Thermistor + BLE");
    Serial.println("--------------------------------");


    // -------------------------------------------------
    // INITIALIZE OLED
    // -------------------------------------------------

    VextON();

    delay(100);

    display.init();

    display.clear();

    display.setFont(ArialMT_Plain_10);


    // -------------------------------------------------
    // INITIALIZE ADC
    // -------------------------------------------------

    analogReadResolution(12);

    analogSetPinAttenuation(
        THERMISTOR_PIN,
        ADC_11db
    );


    // -------------------------------------------------
    // INITIALIZE IMU I2C
    // -------------------------------------------------

    Wire.begin(
        IMU_SDA,
        IMU_SCL,
        IMU_I2C_FREQUENCY
    );

    Serial.println("IMU I2C initialized");

    imuInitialized = initializeIMU();


    // -------------------------------------------------
    // INITIALIZE BLE
    // -------------------------------------------------

    initializeBLE();


    // -------------------------------------------------
    // INITIAL DISPLAY
    // -------------------------------------------------

    display.drawString(0, 0, "MachineGuard");

    display.drawString(0, 16, "Thermistor + BLE");

    display.drawString(
        0,
        32,
        imuInitialized ? "IMU: Ready" : "IMU: ERROR"
    );

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

        readSensorsAndTransmit();
    }

    if (millis() - lastVibWindow >= VIB_INTERVAL_MS) {

        lastVibWindow = millis();

        if (vibrationReady()) {
            sendVibrationWindow();
        }
    }

    delay(10);
}
