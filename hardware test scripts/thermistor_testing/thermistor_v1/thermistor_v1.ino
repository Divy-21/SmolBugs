
#include "HT_SSD1306Wire.h"

// OLED configuration
static SSD1306Wire display(
    0x3c,
    500000,
    SDA_OLED,
    SCL_OLED,
    GEOMETRY_128_64,
    RST_OLED
);

// Thermistor configuration
const int THERMISTOR_PIN = 1;

const float FIXED_RESISTOR = 75000.0; // 75 kOhm
const float SUPPLY_VOLTAGE = 3.3;     // 3.3 V

unsigned long lastUpdate = 0;
const unsigned long updateInterval = 500;

// Enable OLED power
void VextON() {
    pinMode(Vext, OUTPUT);
    digitalWrite(Vext, LOW);
}

void setup() {
    Serial.begin(115200);
    delay(1000);

    // Initialize OLED
    VextON();
    delay(100);

    display.init();
    display.clear();
    display.setFont(ArialMT_Plain_10);

    // Configure ADC
    analogReadResolution(12);
    analogSetPinAttenuation(
        THERMISTOR_PIN,
        ADC_11db
    );

    Serial.println("MachineGuard Test 3");
    Serial.println("Thermistor measurement initialized");

    display.drawString(0, 0, "MachineGuard");
    display.drawString(0, 16, "Test 3: Thermistor");
    display.drawString(0, 32, "Initializing...");
    display.display();
}

void loop() {
    if (millis() - lastUpdate >= updateInterval) {
        lastUpdate = millis();

        readThermistor();
    }
}

void readThermistor() {
    // Read calibrated ADC voltage
    uint32_t voltageMv = analogReadMilliVolts(
        THERMISTOR_PIN
    );

    float voltage = voltageMv / 1000.0;

    // Calculate thermistor resistance
    float resistance = 0;

    if (voltage > 0.01 &&
        voltage < SUPPLY_VOLTAGE - 0.01) {

        resistance = FIXED_RESISTOR *
            (voltage / (SUPPLY_VOLTAGE - voltage));
    }

    // Serial output
    Serial.print("Voltage: ");
    Serial.print(voltageMv);
    Serial.print(" mV | Resistance: ");

    if (resistance > 0) {
        Serial.print(resistance / 1000.0, 2);
        Serial.println(" kOhm");
    } else {
        Serial.println("Invalid reading");
    }

    // OLED output
    display.clear();

    display.drawString(0, 0, "MachineGuard");
    display.drawString(0, 14, "Thermistor Test");

    display.drawString(
        0,
        30,
        "Voltage: " + String(voltageMv) + " mV"
    );

    if (resistance > 0) {
        display.drawString(
            0,
            44,
            "Resistance: " +
            String(resistance / 1000.0, 2) +
            " kOhm"
        );
    } else {
        display.drawString(
            0,
            44,
            "Invalid reading"
        );
    }

    display.display();
}
