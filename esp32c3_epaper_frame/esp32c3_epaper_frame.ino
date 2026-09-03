#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <HTTPClient.h>
#include <ArduinoOTA.h>
#include <Preferences.h>

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

// UDP 广播监听端口（默认与服务端一致为 15002）
#define UDP_BROADCAST_PORT 15002

// 默认备用服务器地址（留空则完全依靠局域网 UDP 广播宣告自动绑定）
static const char* DEFAULT_FALLBACK_URL = "";

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
static uint32_t lastWifiRetryMs = 0;
static int forceRefreshSeq = -1;

static WiFiUDP udp;
static Preferences prefs;
static String serverBaseUrl = "";
static bool hasServerUrl = false;
static bool udpListening = false;

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

// 批量优化传输反相数据块
static void transferInverted(const uint8_t* src, size_t length) {
  uint8_t buffer[128];
  size_t offset = 0;
  while (offset < length) {
    size_t chunk = min((size_t)128, length - offset);
    for (size_t i = 0; i < chunk; i++) {
      buffer[i] = ~src[offset + i];
    }
    SPI.writeBytes(buffer, chunk);
    offset += chunk;
  }
}

void uploadFrameFull() {
  setRamArea(0, 0, EPD_W, EPD_H);

  sendCommand(0x24);
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWriteFast(PIN_DC, HIGH);
  digitalWriteFast(PIN_CS, LOW);
  transferInverted(blackPlane, EPD_BYTES);
  digitalWriteFast(PIN_CS, HIGH);
  SPI.endTransaction();

  sendCommand(0x26);
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWriteFast(PIN_DC, HIGH);
  digitalWriteFast(PIN_CS, LOW);
#if RED_PLANE_INVERTED
  transferInverted(redPlane, EPD_BYTES);
#else
  SPI.writeBytes(redPlane, EPD_BYTES);
#endif
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

// NVS 持久化管理
void loadSavedServerUrl() {
  prefs.begin("epaper", false);
  serverBaseUrl = prefs.getString("server_url", DEFAULT_FALLBACK_URL);
  prefs.end();
  if (serverBaseUrl.length() > 0) {
    hasServerUrl = true;
    Serial.printf("[CONFIG] 已从 NVS 加载服务器地址: %s\n", serverBaseUrl.c_str());
  } else {
    Serial.println("[CONFIG] NVS 中无已保存的服务器地址，正在等待局域网 UDP 广播...");
  }
}

void saveServerUrl(const String& newUrl) {
  if (newUrl == serverBaseUrl && hasServerUrl) return;
  serverBaseUrl = newUrl;
  hasServerUrl = true;
  prefs.begin("epaper", false);
  prefs.putString("server_url", serverBaseUrl);
  prefs.end();
  Serial.printf("[CONFIG] 服务器地址已更新并持久化至 NVS: %s\n", serverBaseUrl.c_str());
}

String getFrameUrl() {
  if (!hasServerUrl) return "";
  return serverBaseUrl + "/frame.bin";
}

String getConfigUrl() {
  if (!hasServerUrl) return "";
  return serverBaseUrl + "/api/device/config";
}

bool fetchFrame() {
  if (WiFi.status() != WL_CONNECTED || !hasServerUrl) return false;

  String url = getFrameUrl();
  HTTPClient http;
  http.setTimeout(8000);
  Serial.printf("GET %s\n", url.c_str());
  if (!http.begin(url)) return false;
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

String extractString(const String& text, const char* key) {
  String needle = String("\"") + key + "\":";
  int index = text.indexOf(needle);
  if (index < 0) return "";
  index += needle.length();
  while (index < text.length() && (text[index] == ' ' || text[index] == '\t' || text[index] == '\r' || text[index] == '\n')) {
    index++;
  }
  if (index >= text.length() || text[index] != '"') return "";
  index++; // 跳过开头的引号
  int end = text.indexOf("\"", index);
  if (end < 0) return "";
  return text.substring(index, end);
}

bool pollDeviceConfig(bool* shouldRefresh) {
  *shouldRefresh = false;
  if (WiFi.status() != WL_CONNECTED || !hasServerUrl) return false;

  String url = getConfigUrl();
  HTTPClient http;
  http.setTimeout(8000);
  if (!http.begin(url)) return false;
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

void startUdpBroadcastListener() {
  if (udp.begin(UDP_BROADCAST_PORT)) {
    udpListening = true;
    Serial.printf("[UDP] 广播监听已就绪，端口: %d\n", UDP_BROADCAST_PORT);
  } else {
    Serial.printf("[UDP] 启动广播监听失败，端口: %d\n", UDP_BROADCAST_PORT);
  }
}

bool processUdpBroadcast() {
  if (!udpListening) return false;
  int packetSize = udp.parsePacket();
  if (packetSize <= 0) return false;

  char packetBuffer[512];
  int len = udp.read(packetBuffer, sizeof(packetBuffer) - 1);
  if (len <= 0) return false;
  packetBuffer[len] = '\0';

  String payload = String(packetBuffer);
  Serial.printf("[UDP] 收到广播包 (%d 字节) 来自 %s:%d\n", packetSize, udp.remoteIP().toString().c_str(), udp.remotePort());

  if (payload.indexOf("EPAPER_SERVER") >= 0) {
    String discoveredUrl = extractString(payload, "url");
    if (discoveredUrl.length() > 0) {
      Serial.printf("[UDP] 成功解析服务端 URL: %s\n", discoveredUrl.c_str());
      bool changed = (discoveredUrl != serverBaseUrl);
      saveServerUrl(discoveredUrl);
      return changed;
    }
  }
  return false;
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("connecting wifi: %s\n", WIFI_SSID);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("wifi ip: ");
    Serial.println(WiFi.localIP());
    startUdpBroadcastListener();
  } else {
    Serial.println("wifi connect timeout, will retry in background");
  }
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
  Serial.println("ESP32-C3 epaper frame client (UDP Broadcast Discovery)");

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

  loadSavedServerUrl();
  connectWifi();
  setupOta();

  if (hasServerUrl) {
    bool force = false;
    pollDeviceConfig(&force);
    if (fetchFrame()) {
      displayFrame();
      lastDisplayMs = millis();
    }
  } else {
    Serial.println("等待服务端在网页端点击广播宣告...");
  }
}

void loop() {
  ArduinoOTA.handle();

  uint32_t now = millis();

  // 非阻塞 WiFi 重连守护
  if (WiFi.status() != WL_CONNECTED) {
    if (now - lastWifiRetryMs > 10000UL) {
      lastWifiRetryMs = now;
      Serial.println("WiFi 断开，尝试重连...");
      WiFi.reconnect();
    }
    delay(20);
    return;
  } else if (!udpListening) {
    startUdpBroadcastListener();
  }

  // 监听并解析 UDP 广播数据包
  bool serverChanged = processUdpBroadcast();

  // 如果刚收到新的服务器广播宣告，立即触发刷新
  if (serverChanged) {
    Serial.println("[UDP] 服务器已更新，立即刷新屏幕数据...");
    bool force = false;
    pollDeviceConfig(&force);
    if (fetchFrame()) {
      displayFrame();
      lastDisplayMs = millis();
    }
  }

  // 仅在已拥有服务器地址时进行定时拉取与配置轮询
  if (hasServerUrl) {
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
  }

  delay(20);
}
