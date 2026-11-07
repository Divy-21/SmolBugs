
#include <Wire.h>
#include "HT_SSD1306Wire.h"

static SSD1306Wire display(
    0x3c,
    500000,
    SDA_OLED,
    SCL_OLED,
    GEOMETRY_128_64,
    RST_OLED
);

float temperature = 24.5;
float humidity = 42.0;
float vibration = 0.25;

unsigned long previousUpdate = 0;
const unsigned long updateInterval = 500;

// Sensor warning thresholds
const float TEMP_THRESHOLD = 35.0;
const float HUMIDITY_THRESHOLD = 70.0;
const float VIBRATION_THRESHOLD = 0.45;

void VextON() {
    pinMode(Vext, OUTPUT);
    digitalWrite(Vext, LOW);
}

void setup() {
    Serial.begin(115200);

    VextON();
    delay(100);

    display.init();
    display.setContrast(255);
}

void updateSimulatedSensors() {
    // Generate changing temperature
    temperature = 22.0 + (millis() / 1000 % 10) * 0.3;

    // Generate changing humidity
    humidity = 40.0 + (millis() / 1500 % 15);

    // Generate changing vibration
    vibration = 0.10 + (millis() / 500 % 10) * 0.05;
}

void drawDashboard() {
    char msg_normal[] = "-------";
    char msg_warning[] = "BAD";

    display.clear();

    display.setFont(ArialMT_Plain_10);
    display.setTextAlignment(TEXT_ALIGN_LEFT);

    // Header
    display.drawString(0, 0, "MACHINEGUARD");
    display.drawHorizontalLine(0, 12, 128);

    // Temperature
    display.drawString(
        0, 16,
        "Temp: " + String(temperature, 1) + " C"
    );

    display.setTextAlignment(TEXT_ALIGN_RIGHT);
    display.drawString(
        128, 16,
        temperature > TEMP_THRESHOLD ? msg_warning : msg_normal
    );

    // Humidity
    display.setTextAlignment(TEXT_ALIGN_LEFT);
    display.drawString(
        0, 28,
        "Humidity: " + String(humidity, 1) + "%"
    );

    display.setTextAlignment(TEXT_ALIGN_RIGHT);
    display.drawString(
        128, 28,
        humidity > HUMIDITY_THRESHOLD ? msg_warning : msg_normal
    );

    // Vibration
    display.setTextAlignment(TEXT_ALIGN_LEFT);
    display.drawString(
        0, 40,
        "Vibration: " + String(vibration, 2) + " g"
    );

    display.setTextAlignment(TEXT_ALIGN_RIGHT);
    display.drawString(
        128, 40,
        vibration > VIBRATION_THRESHOLD ? msg_warning : msg_normal
    );

    // Refresh physical OLED
    display.display();
}

void loop() {
    unsigned long currentTime = millis();

    if (currentTime - previousUpdate >= updateInterval) {
        previousUpdate = currentTime;

        updateSimulatedSensors();
        drawDashboard();

        Serial.println(
            "Temperature: " + String(temperature) +
            " | Humidity: " + String(humidity) +
            " | Vibration: " + String(vibration)
        );
    }
}
