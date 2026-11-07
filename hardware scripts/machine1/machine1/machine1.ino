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
#include "HT_SSD1306Wire.h"

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

const float FIXED_RESISTOR = 75000.0; // 75 kOhm
const float SUPPLY_VOLTAGE = 3.3;     // 3.3 V

const unsigned long SENSOR_INTERVAL = 500;

unsigned long lastSensorUpdate = 0;


// =====================================================
// VIBRATION CONFIGURATION
// =====================================================

#define USE_TEST_SIGNAL 1   // set to 0 once the ADXL355 driver is added

const float    VIB_SAMPLE_RATE_HZ = 4000.0f;  // replace with measured ODR later
const uint16_t VIB_WINDOW_SAMPLES = 4096;
const uint8_t  VIB_RANGE_G        = 2;
const unsigned long VIB_INTERVAL_MS = 10000;  // one window every 10 s
const unsigned long VIB_FRAME_GAP_MS = 10;    // pause between DATA frames; lower = faster, risk of drops

// 4096 x 3 x 2 bytes = 24 KB, fine for the ESP32-S3's RAM.
static int16_t vibBuffer[VIB_WINDOW_SAMPLES][3];

uint16_t vibWindowId = 0;
uint32_t vibWindowsSent = 0;
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

    updateDisplay(voltageMv, resistance);


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
    }
}


// =====================================================
// VIBRATION CAPTURE
// =====================================================

#if USE_TEST_SIGNAL

// Synthetic machine at 3000 RPM: 1x (50 Hz) imbalance, small 2x, noise,
// and 1 g of gravity on z. Replaces the ADXL355 until it arrives.
void captureWindow(uint32_t* firstSampleMs) {

    *firstSampleMs = millis();

    const uint16_t lsb = smolbugs::lsbPerG(VIB_RANGE_G);
    const float twoPi = 6.2831853f;

    static uint32_t sampleClock = 0;

    for (uint16_t i = 0; i < VIB_WINDOW_SAMPLES; i++) {

        float t = (float)(sampleClock + i) / VIB_SAMPLE_RATE_HZ;

        float noiseX = ((int32_t)(esp_random() % 2001) - 1000) / 100000.0f;
        float noiseY = ((int32_t)(esp_random() % 2001) - 1000) / 100000.0f;
        float noiseZ = ((int32_t)(esp_random() % 2001) - 1000) / 100000.0f;

        float x = 0.05f * sinf(twoPi * 50.0f * t)
                + 0.01f * sinf(twoPi * 100.0f * t) + noiseX;
        float y = 0.035f * sinf(twoPi * 50.0f * t) + noiseY;
        float z = 1.0f + 0.015f * sinf(twoPi * 50.0f * t) + noiseZ;

        vibBuffer[i][0] = smolbugs::gToCounts(x, lsb);
        vibBuffer[i][1] = smolbugs::gToCounts(y, lsb);
        vibBuffer[i][2] = smolbugs::gToCounts(z, lsb);
    }

    // Keep phase continuous between windows (wraps harmlessly).
    sampleClock = (sampleClock + VIB_WINDOW_SAMPLES) % 4000000UL;
}

#else

// TODO when the ADXL355 arrives: fill vibBuffer from the sensor FIFO, paced by
// its data-ready signal, converting each axis with smolbugs::adxl355ToCounts().
void captureWindow(uint32_t* firstSampleMs) {

    *firstSampleMs = millis();
}

#endif


// =====================================================
// VIBRATION TRANSMISSION
// =====================================================

bool vibrationReady() {

    if (!deviceConnected || vibCccd == nullptr || !vibCccd->getNotifications()) {
        return false;  // receiver hasn't subscribed to vibration yet
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

    captureWindow(&firstSampleMs);

    uint8_t frame[smolbugs::MAX_DATA_FRAME_SIZE];

    unsigned long started = millis();
    uint32_t errorsBefore = vibNotifyErrors;

    size_t len = smolbugs::encodeStart(
        frame,
        vibWindowId,
        VIB_SAMPLE_RATE_HZ,
        VIB_WINDOW_SAMPLES,
        smolbugs::SAMPLES_PER_CHUNK,
        VIB_RANGE_G,
        smolbugs::lsbPerG(VIB_RANGE_G),
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
    Serial.println("MachineGuard Thermistor + Vibration + BLE");
    Serial.println("------------------------------------------");


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

    display.drawString(0, 16, "Thermistor + Vibration");

    display.drawString(0, 32, "Initializing...");

    display.display();


#if USE_TEST_SIGNAL
    Serial.println("Vibration: TEST SIGNAL (no ADXL355)");
#endif

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

    if (millis() - lastVibWindow >= VIB_INTERVAL_MS) {

        lastVibWindow = millis();

        if (vibrationReady()) {
            sendVibrationWindow();
        }
    }

    delay(10);
}
