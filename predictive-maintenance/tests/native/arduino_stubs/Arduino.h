// Minimal host stand-ins for the Arduino-ESP32 API used by machine1.ino (testing only).
#pragma once
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string>

#define LOW 0
#define OUTPUT 1
#define ADC_11db 3

#define HEX 16

namespace stub {
extern uint64_t now_us;  // simulated time
extern uint32_t rng;
}

inline uint32_t millis() { return (uint32_t)(stub::now_us / 1000); }
inline uint32_t micros() { return (uint32_t)stub::now_us; }
inline void delay(uint32_t ms) { stub::now_us += (uint64_t)ms * 1000; }
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline void analogReadResolution(int) {}
inline void analogSetPinAttenuation(int, int) {}
inline uint32_t analogReadMilliVolts(int) { return 1582; }
inline uint32_t esp_random() { stub::rng = stub::rng * 1664525u + 1013904223u; return stub::rng; }

class String {
public:
    std::string s;
    String() {}
    String(const char* c) : s(c) {}
    String(const std::string& v) : s(v) {}
    explicit String(int v) : s(std::to_string(v)) {}
    explicit String(unsigned int v) : s(std::to_string(v)) {}
    explicit String(long v) : s(std::to_string(v)) {}
    explicit String(unsigned long v) : s(std::to_string(v)) {}
    String(double v, int decimals) { char b[32]; snprintf(b, sizeof b, "%.*f", decimals, v); s = b; }
    const char* c_str() const { return s.c_str(); }
    String operator+(const String& o) const { return String(s + o.s); }
    String operator+(const char* o) const { return String(s + o); }
    friend String operator+(const char* a, const String& b) { return String(std::string(a) + b.s); }
};

struct SerialStub {
    void begin(int) {}
    template <typename T> void print(T) {}
    template <typename T> void print(T, int) {}
    template <typename T> void println(T) {}
    template <typename T> void println(T, int) {}
    void println() {}
};
extern SerialStub Serial;
