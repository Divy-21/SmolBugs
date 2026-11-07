#include <WiFi.h>
#include <HTTPClient.h>

// Wi-Fi access point credentials
const char* ssid = "MachineGuard";
const char* password = "MachineGuard123";

// Laptop's IP address will go here later
const char* serverURL = "http://192.168.4.2:5000/telemetry";

// Access point configuration
IPAddress localIP(192, 168, 4, 1);
IPAddress gateway(192, 168, 4, 1);
IPAddress subnet(255, 255, 255, 0);

unsigned long lastSendTime = 0;
const unsigned long sendInterval = 1000;

int sequenceNumber = 0;

void setup() {
    Serial.begin(115200);
    delay(1000);

    Serial.println();
    Serial.println("MachineGuard Wi-Fi Test");
    Serial.println("-----------------------");

    // Configure the ESP32 as an access point
    WiFi.mode(WIFI_AP);

    if (!WiFi.softAPConfig(localIP, gateway, subnet)) {
        Serial.println("Failed to configure access point IP");
        return;
    }

    // Start the Wi-Fi access point
    if (!WiFi.softAP(ssid, password, 1, 0, 1)) {
        Serial.println("Failed to start access point");
        return;
    }

    Serial.println("Access point started");
    Serial.print("Network name: ");
    Serial.println(ssid);

    Serial.print("Access point IP: ");
    Serial.println(WiFi.softAPIP());

    Serial.println("Waiting for laptop connection...");
}

void loop() {
    // Check whether a device is connected
    if (WiFi.softAPgetStationNum() == 0) {
        return;
    }

    // Send a message every second
    if (millis() - lastSendTime >= sendInterval) {
        lastSendTime = millis();

        sendTelemetry();
    }
}

void sendTelemetry() {
    WiFiClient client;
    HTTPClient http;

    // Simulated sensor data for now
    float temperature = 25.0 + (sequenceNumber % 10) * 0.2;
    float vibration = 0.30 + (sequenceNumber % 5) * 0.05;

    // Construct a JSON message
    String payload = "{";
    payload += "\"device_id\":\"machine_001\",";
    payload += "\"sequence\":" + String(sequenceNumber) + ",";
    payload += "\"temperature_c\":" + String(temperature, 2) + ",";
    payload += "\"vibration_rms_g\":" + String(vibration, 3);
    payload += "}";

    // Connect to the laptop's HTTP server
    http.begin(client, serverURL);
    http.addHeader("Content-Type", "application/json");

    Serial.print("Sending: ");
    Serial.println(payload);

    int httpResponseCode = http.POST(payload);

    if (httpResponseCode > 0) {
        Serial.print("HTTP response: ");
        Serial.println(httpResponseCode);
    } else {
        Serial.print("HTTP request failed: ");
        Serial.println(http.errorToString(httpResponseCode));
    }

    http.end();

    sequenceNumber++;
}
