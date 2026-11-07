
#include <Wire.h>
#include "HT_SSD1306Wire.h"

// Configure the OLED display
static SSD1306Wire display(
    0x3c,
    500000,
    SDA_OLED,
    SCL_OLED,
    GEOMETRY_128_64,
    RST_OLED
);

// Enable OLED power
void VextON() {
    pinMode(Vext, OUTPUT);
    digitalWrite(Vext, LOW);
}

void setup() {
    Serial.begin(115200);

    // Power the OLED
    VextON();
    delay(100);

    // Initialize display
    display.init();

    // Set default font
    display.setFont(ArialMT_Plain_10);

    // Clear previous content
    display.clear();

    // Draw text
    display.setTextAlignment(TEXT_ALIGN_LEFT);
    
    display.clear();

    //draw_test_1();
    draw_test_2();

    // Transfer framebuffer to OLED
    display.display();

    Serial.println("OLED initialized successfully");
}

void draw_test_1() {
    display.drawString(0, 0, "Hello Heltec!");
    display.drawString(0, 15, "ESP32-S3");
    display.drawString(0, 30, "OLED is working.");
}

void draw_test_2() {
    // Rectangle outline
    display.drawRect(0, 0, 40, 30);

    // Filled rectangle
    display.fillRect(50, 0, 30, 30);

    // Horizontal line
    display.drawHorizontalLine(0, 40, 128);

    // Vertical line
    display.drawVerticalLine(64, 40, 24);

    // Circle outline
    display.drawCircle(100, 20, 12);

    // Filled circle
    display.fillCircle(20, 55, 7);
}

void loop() {
    // Nothing to do yet
}
