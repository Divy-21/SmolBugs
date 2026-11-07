#pragma once
#include "Arduino.h"
#define SDA_OLED 17
#define SCL_OLED 18
#define RST_OLED 21
#define Vext 36
enum OLEDDISPLAY_GEOMETRY { GEOMETRY_128_64 };
extern const uint8_t ArialMT_Plain_10[];
class SSD1306Wire {
public:
    SSD1306Wire(uint8_t, uint32_t, int, int, OLEDDISPLAY_GEOMETRY, int) {}
    void init() {}
    void clear() {}
    void display() {}
    void setFont(const uint8_t*) {}
    void drawString(int, int, String) {}
};
