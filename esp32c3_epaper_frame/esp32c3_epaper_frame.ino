#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoOTA.h>

#define PIN_CS   7
#define PIN_DC   3
#define PIN_RST  2
#define PIN_BUSY 10

#define USE_DEFAULT_SPI_PINS 1
#define PIN_SCK  4
#define PIN_MOSI 6
#define PIN_MISO -1

static const char* WIFI_SSID = "YOUR_WIFI_SSID";
static const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
static const char* OTA_HOSTNAME = "esp32c3-epaper";

// Docker host should map both 15001 and 15002 to the Python service.
static const char* FRAME_URL = "http://YOUR_NAS_IP:15002/frame.bin";
static const char* CONFIG_URL = "http://YOUR_NAS_IP:15002/api/device/config";

static const int EPD_W = 400;
static const int EPD_H = 300;
static const int EPD_BYTES = EPD_W * EPD_H / 8;
static const int FRAME_HEADER_BYTES = 15;
static const int FRAME_TOTAL_BYTES = FRAME_HEADER_BYTES + EPD_BYTES * 2;

// If red is inverted on the physical panel, change this to 1.
#define RED_PLANE_INVERTED 0

static uint8_t blackPlane[EPD_BYTES];
static uint8_t redPlane[EPD_BYTES];
static bool initDone = false;
static bool powered = false;
static uint32_t displayIntervalMs = 60000;
static uint32_t lastDisplayMs = 0;
static uint32_t lastConfigPollMs = 0;
static int forceRefreshSeq = -1;

void digitalWriteFast(int pin, int value) {
  digitalWrite(pin, value);
}

void sendCommand(uint8_t command) {
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWriteFast(PIN_DC, LOW);
  digitalWriteFast(PIN_CS, LOW);
  SPI.transfer(command);
  digitalWriteFast(PIN_CS, HIGH);
  digitalWriteFast(PIN_DC, HIGH);
  SPI.endTransaction();
}

void sendData(uint8_t data) {
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWriteFast(PIN_DC, HIGH);
  digitalWriteFast(PIN_CS, LOW);
  SPI.transfer(data);
  digitalWriteFast(PIN_CS, HIGH);
  SPI.endTransaction();
}

void waitBusy(const char* label, uint32_t timeoutMs = 120000) {
  Serial.printf("busy wait: %s\n", label);
  uint32_t start = millis();
  while (digitalRead(PIN_BUSY) == HIGH) {
    ArduinoOTA.handle();
    delay(10);
    if (millis() - start > timeoutMs) {
      Serial.println("busy timeout");
      return;
    }
  }
}

void hardReset() {
  digitalWriteFast(PIN_RST, HIGH);
  delay(10);
  digitalWriteFast(PIN_RST, LOW);
  delay(10);
  digitalWriteFast(PIN_RST, HIGH);
  delay(10);
}

void setRamArea(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
  x -= x % 8;
  w -= w % 8;
  if (w == 0 || h == 0) return;

  sendCommand(0x11);
  sendData(0x03);

  sendCommand(0x44);
  sendData(x / 8);
  sendData((x + w - 1) / 8);

  sendCommand(0x45);
  sendData(y & 0xFF);
  sendData(y >> 8);
  sendData((y + h - 1) & 0xFF);
  sendData((y + h - 1) >> 8);

  sendCommand(0x4E);
  sendData(x / 8);
  sendCommand(0x4F);
  sendData(y & 0xFF);
  sendData(y >> 8);
}

void epdInitDisplay() {
  if (initDone) return;
  Serial.println("epd init display");
  hardReset();

  sendCommand(0x12);
  delay(10);

  sendCommand(0x01);
  sendData((EPD_H - 1) & 0xFF);
  sendData((EPD_H - 1) >> 8);
  sendData(0x00);

  sendCommand(0x3C);
  sendData(0x05);

  sendCommand(0x18);
  sendData(0x80);

  setRamArea(0, 0, EPD_W, EPD_H);
  initDone = true;
}

void epdSleep() {
  Serial.println("epd sleep");
  if (powered) {
    sendCommand(0x22);
    sendData(0xC3);
    sendCommand(0x20);
    waitBusy("power off", 250);
    powered = false;
  }
  sendCommand(0x10);
  sendData(0x11);
  initDone = false;
}

void updateFull() {
  Serial.println("display full refresh");
  sendCommand(0x1A);
  sendData(0x5A);
  sendData(0x00);
  sendCommand(0x22);
  sendData(0x91);
  sendCommand(0x20);
  delay(2);
  sendCommand(0x22);
  sendData(0xC7);
  sendCommand(0x20);
  waitBusy("full refresh", 25000);
  powered = false;
}

void uploadFrameFull() {
  setRamArea(0, 0, EPD_W, EPD_H);

  sendCommand(0x24);
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWriteFast(PIN_DC, HIGH);
  digitalWriteFast(PIN_CS, LOW);
  for (int i = 0; i < EPD_BYTES; i++) SPI.transfer(~blackPlane[i]);
  digitalWriteFast(PIN_CS, HIGH);
  SPI.endTransaction();

  sendCommand(0x26);
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWriteFast(PIN_DC, HIGH);
  digitalWriteFast(PIN_CS, LOW);
  for (int i = 0; i < EPD_BYTES; i++) {
#if RED_PLANE_INVERTED
    SPI.transfer(~redPlane[i]);
#else
    SPI.transfer(redPlane[i]);
#endif
  }
  digitalWriteFast(PIN_CS, HIGH);
  SPI.endTransaction();
}

void displayFrame() {
  epdInitDisplay();
  uploadFrameFull();
  updateFull();
  epdSleep();
}

bool readExact(WiFiClient* stream, uint8_t* buffer, size_t length) {
  size_t offset = 0;
  uint32_t start = millis();
  while (offset < length) {
    ArduinoOTA.handle();
    int available = stream->available();
    if (available > 0) {
      int readLen = stream->readBytes(buffer + offset, min((size_t)available, length - offset));
      offset += readLen;
      start = millis();
    } else {
      delay(1);
      if (millis() - start > 8000) return false;
    }
  }
  return true;
}

bool fetchFrame() {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  Serial.printf("GET %s\n", FRAME_URL);
  if (!http.begin(FRAME_URL)) return false;
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("frame http error: %d\n", code);
    http.end();
    return false;
  }

  int len = http.getSize();
  if (len > 0 && len != FRAME_TOTAL_BYTES) {
    Serial.printf("bad frame length: %d\n", len);
    http.end();
    return false;
  }

  WiFiClient* stream = http.getStreamPtr();
  uint8_t header[FRAME_HEADER_BYTES];
  if (!readExact(stream, header, sizeof(header))) {
    Serial.println("frame header timeout");
    http.end();
    return false;
  }

  bool ok = header[0] == 'E' && header[1] == 'P' && header[2] == 'D' && header[3] == '1'
            && header[4] == 1
            && header[5] == (EPD_W & 0xFF) && header[6] == (EPD_W >> 8)
            && header[7] == (EPD_H & 0xFF) && header[8] == (EPD_H >> 8)
            && header[9] == 2;
  if (!ok) {
    Serial.println("bad frame header");
    http.end();
    return false;
  }

  if (!readExact(stream, blackPlane, EPD_BYTES) || !readExact(stream, redPlane, EPD_BYTES)) {
    Serial.println("frame body timeout");
    http.end();
    return false;
  }

  http.end();
  return true;
}

int extractInt(const String& text, const char* key, int fallback) {
  String needle = String("\"") + key + "\":";
  int index = text.indexOf(needle);
  if (index < 0) return fallback;
  index += needle.length();
  while (index < text.length() && text[index] == ' ') index++;
  int end = index;
  while (end < text.length() && (isDigit(text[end]) || text[end] == '-')) end++;
  if (end == index) return fallback;
  return text.substring(index, end).toInt();
}

bool pollDeviceConfig(bool* shouldRefresh) {
  *shouldRefresh = false;
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  if (!http.begin(CONFIG_URL)) return false;
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    http.end();
    return false;
  }
  String payload = http.getString();
  http.end();

  int interval = extractInt(payload, "display_interval_seconds", displayIntervalMs / 1000);
  interval = constrain(interval, 30, 3600);
  displayIntervalMs = (uint32_t)interval * 1000UL;

  int seq = extractInt(payload, "force_refresh_seq", forceRefreshSeq);
  if (forceRefreshSeq >= 0 && seq != forceRefreshSeq) {
    *shouldRefresh = true;
  }
  forceRefreshSeq = seq;
  return true;
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("connecting wifi: %s\n", WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("wifi ip: ");
  Serial.println(WiFi.localIP());
}

void setupOta() {
  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA
    .onStart([]() { Serial.println("OTA start"); })
    .onEnd([]() { Serial.println("OTA end"); })
    .onError([](ota_error_t error) { Serial.printf("OTA error %u\n", error); });
  ArduinoOTA.begin();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("ESP32-C3 epaper frame client");

  pinMode(PIN_CS, OUTPUT);
  pinMode(PIN_DC, OUTPUT);
  pinMode(PIN_RST, OUTPUT);
  pinMode(PIN_BUSY, INPUT);
  digitalWriteFast(PIN_CS, HIGH);

#if USE_DEFAULT_SPI_PINS
  SPI.begin();
#else
  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CS);
#endif

  connectWifi();
  setupOta();

  bool force = false;
  pollDeviceConfig(&force);
  if (fetchFrame()) {
    displayFrame();
    lastDisplayMs = millis();
  }
}

void loop() {
  ArduinoOTA.handle();

  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }

  uint32_t now = millis();
  bool force = false;
  if (now - lastConfigPollMs > 10000UL) {
    pollDeviceConfig(&force);
    lastConfigPollMs = now;
  }

  if (force || now - lastDisplayMs >= displayIntervalMs) {
    if (fetchFrame()) {
      displayFrame();
      lastDisplayMs = millis();
    }
  }

  delay(20);
}
