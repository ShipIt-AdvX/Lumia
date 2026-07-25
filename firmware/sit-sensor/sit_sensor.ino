/*
 * Lumia Sit Sensor — ESP32 / Arduino Nano + 压力薄膜 / FSR
 * 椅下受压 -> POST /api/sit {"seated":true|false,"pressure":0..1}
 */

#include <WiFi.h>
#include <HTTPClient.h>

#define PRESSURE_PIN 34  // ADC
#define THRESHOLD 1200   // 按实际校准

const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASS";
const char* LUMIA_HOST = "http://192.168.1.10:8787";

bool lastSeated = false;

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(400);
  Serial.println(WiFi.localIP());
}

void report(bool seated, float pressure) {
  HTTPClient http;
  http.begin(String(LUMIA_HOST) + "/api/sit");
  http.addHeader("Content-Type", "application/json");
  String body = String("{\"seated\":") + (seated ? "true" : "false") +
                ",\"pressure\":" + String(pressure, 3) + "}";
  int code = http.POST(body);
  Serial.printf("sit => %d seated=%d p=%.2f\n", code, seated, pressure);
  http.end();
}

void loop() {
  int raw = analogRead(PRESSURE_PIN);
  float p = constrain(raw / 4095.0f, 0.0f, 1.0f);
  bool seated = raw > THRESHOLD;
  if (seated != lastSeated) {
    report(seated, p);
    lastSeated = seated;
  } else if (seated && (millis() % 15000 < 50)) {
    // 心跳刷新
    report(true, p);
  }
  delay(100);
}
