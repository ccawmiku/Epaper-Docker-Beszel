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

// UDP 广播监听端口
#define UDP_BROADCAST_PORT 15002

// 默认备用服务器地址（若留空则依靠局域网 UDP 广播自动配对）
static const char* DEFAULT_FALLBACK_URL = "";

static const int EPD_W = 400;
static const int EPD_H = 300;
static const int EPD_BYTES = EPD_W * EPD_H / 8;
static const int FRAME_HEADER_BYTES = 15;
static const int FRAME_TOTAL_BYTES = FRAME_HEADER_BYTES + EPD_BYTES * 2;

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
static String lastErrorMessage = "";

// 基础 5x7 ASCII 字符点阵表 (0x20..0x5F)
static const uint8_t FONT_5X7[][5] PROGMEM = {
  {0x00,0x00,0x00,0x00,0x00}, // space
  {0x00,0x00,0x5F,0x00,0x00}, // !
  {0x00,0x07,0x00,0x07,0x00}, // "
  {0x14,0x7F,0x14,0x7F,0x14}, // #
  {0x24,0x2A,0x7F,0x2A,0x12}, // $
  {0x23,0x13,0x08,0x64,0x62}, // %
  {0x36,0x49,0x55,0x22,0x50}, // &
  {0x00,0x05,0x03,0x00,0x00}, // '
  {0x00,0x1C,0x22,0x41,0x00}, // (
  {0x00,0x41,0x22,0x1C,0x00}, // )
  {0x14,0x08,0x3E,0x08,0x14}, // *
  {0x08,0x08,0x3E,0x08,0x08}, // +
  {0x00,0x50,0x30,0x00,0x00}, // ,
  {0x08,0x08,0x08,0x08,0x08}, // -
  {0x00,0x60,0x60,0x00,0x00}, // .
  {0x20,0x10,0x08,0x04,0x02}, // /
  {0x3E,0x51,0x49,0x45,0x3E}, // 0
  {0x00,0x42,0x7F,0x40,0x00}, // 1
  {0x42,0x61,0x51,0x49,0x46}, // 2
  {0x21,0x41,0x45,0x4B,0x31}, // 3
  {0x18,0x14,0x12,0x7F,0x10}, // 4
  {0x27,0x45,0x45,0x45,0x39}, // 5
  {0x3C,0x4A,0x49,0x49,0x30}, // 6
  {0x01,0x71,0x09,0x05,0x03}, // 7
  {0x36,0x49,0x49,0x49,0x36}, // 8
  {0x06,0x49,0x49,0x29,0x1E}, // 9
  {0x00,0x36,0x36,0x00,0x00}, // :
  {0x00,0x56,0x36,0x00,0x00}, // ;
  {0x08,0x14,0x22,0x41,0x00}, // <
  {0x14,0x14,0x14,0x14,0x14}, // =
  {0x00,0x41,0x22,0x14,0x08}, // >
  {0x02,0x01,0x51,0x09,0x06}, // ?
  {0x32,0x49,0x79,0x41,0x3E}, // @
  {0x7E,0x11,0x11,0x11,0x7E}, // A
  {0x7F,0x49,0x49,0x49,0x36}, // B
  {0x3E,0x41,0x41,0x41,0x22}, // C
  {0x7F,0x41,0x41,0x22,0x1C}, // D
  {0x7F,0x49,0x49,0x49,0x41}, // E
  {0x7F,0x09,0x09,0x09,0x01}, // F
  {0x3E,0x41,0x49,0x49,0x7A}, // G
  {0x7F,0x08,0x08,0x08,0x7F}, // H
  {0x00,0x41,0x7F,0x41,0x00}, // I
  {0x20,0x40,0x41,0x3F,0x01}, // J
  {0x7F,0x08,0x14,0x22,0x41}, // K
  {0x7F,0x40,0x40,0x40,0x40}, // L
  {0x7F,0x02,0x0C,0x02,0x7F}, // M
  {0x7F,0x04,0x08,0x10,0x7F}, // N
  {0x3E,0x41,0x41,0x41,0x3E}, // O
  {0x7F,0x09,0x09,0x09,0x06}, // P
  {0x3E,0x41,0x51,0x21,0x5E}, // Q
  {0x7F,0x09,0x19,0x29,0x46}, // R
  {0x46,0x49,0x49,0x49,0x31}, // S
  {0x01,0x01,0x7F,0x01,0x01}, // T
  {0x3F,0x40,0x40,0x40,0x3F}, // U
  {0x1F,0x20,0x40,0x20,0x1F}, // V
  {0x3F,0x40,0x38,0x40,0x3F}, // W
  {0x63,0x14,0x08,0x14,0x63}, // X
  {0x07,0x08,0x70,0x08,0x07}, // Y
  {0x61,0x51,0x49,0x45,0x43}, // Z
  {0x00,0x7F,0x41,0x41,0x00}, // [
  {0x02,0x04,0x08,0x10,0x20}, // \
  {0x00,0x41,0x41,0x7F,0x00}, // ]
  {0x04,0x02,0x01,0x02,0x04}, // ^
  {0x40,0x40,0x40,0x40,0x40}, // _
};

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

// 诊断显示绘制相关函数
void drawPixel(int x, int y, bool isRed, bool isBlack) {
  if (x < 0 || x >= EPD_W || y < 0 || y >= EPD_H) return;
  int idx = (y * EPD_W + x) / 8;
  int bit = 7 - ((y * EPD_W + x) % 8);
  if (isBlack) blackPlane[idx] |= (1 << bit);
  else blackPlane[idx] &= ~(1 << bit);
  if (isRed) redPlane[idx] |= (1 << bit);
  else redPlane[idx] &= ~(1 << bit);
}

void drawChar(int x, int y, char c, bool isRed = false, int scale = 2) {
  c = toupper(c);
  if (c < 0x20 || c > '_') c = ' ';
  int charIdx = c - 0x20;
  for (int col = 0; col < 5; col++) {
    uint8_t line = pgm_read_byte(&(FONT_5X7[charIdx][col]));
    for (int row = 0; row < 7; row++) {
      if (line & (1 << row)) {
        for (int dx = 0; dx < scale; dx++) {
          for (int dy = 0; dy < scale; dy++) {
            drawPixel(x + col * scale + dx, y + row * scale + dy, isRed, !isRed);
          }
        }
      }
    }
  }
}

void drawString(int x, int y, const char* str, bool isRed = false, int scale = 2) {
  int curX = x;
  int curY = y;
  while (*str) {
    if (*str == '\n') {
      curY += 7 * scale + 4;
      curX = x;
    } else {
      drawChar(curX, curY, *str, isRed, scale);
      curX += 5 * scale + scale;
    }
    str++;
  }
}

void clearPlanes() {
  memset(blackPlane, 0x00, sizeof(blackPlane));
  memset(redPlane, 0x00, sizeof(redPlane));
}

void displayDiagnosticScreen(const char* title, const char* line1, const char* line2, const char* line3, bool isError = false) {
  clearPlanes();
  for (int x = 20; x < EPD_W - 20; x++) {
    for (int y = 14; y < 17; y++) drawPixel(x, y, isError, false);
    for (int y = 56; y < 59; y++) drawPixel(x, y, isError, false);
  }
  drawString(28, 26, title, isError, 3);
  drawString(28, 80, line1, false, 2);
  drawString(28, 120, line2, false, 2);
  drawString(28, 160, line3, isError, 2);
  drawString(28, 230, "PAIR & LOGS ON WEB:", false, 1);
  drawString(28, 248, "http://<ROUTER_IP>:17001/preview", isError, 2);
  displayFrame();
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

void loadSavedServerUrl() {
  prefs.begin("epaper", false);
  serverBaseUrl = prefs.getString("server_url", DEFAULT_FALLBACK_URL);
  prefs.end();
  if (serverBaseUrl.length() > 0) {
    hasServerUrl = true;
    Serial.printf("[CONFIG] 从 NVS 加载已配对服务器: %s\n", serverBaseUrl.c_str());
  } else {
    Serial.println("[CONFIG] NVS 中无服务器记录，等待局域网 UDP 广播...");
  }
}

void saveServerUrl(const String& newUrl) {
  if (newUrl == serverBaseUrl && hasServerUrl) return;
  serverBaseUrl = newUrl;
  hasServerUrl = true;
  prefs.begin("epaper", false);
  prefs.putString("server_url", serverBaseUrl);
  prefs.end();
  Serial.printf("[CONFIG] 服务器地址已持久化至 NVS: %s\n", serverBaseUrl.c_str());
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
  http.setTimeout(10000);
  Serial.printf("GET %s\n", url.c_str());
  if (!http.begin(url)) {
    lastErrorMessage = "HTTP_BEGIN_FAILED";
    return false;
  }
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("frame http error: %d\n", code);
    lastErrorMessage = "HTTP_" + String(code);
    http.end();
    return false;
  }

  int len = http.getSize();
  if (len > 0 && len != FRAME_TOTAL_BYTES) {
    Serial.printf("bad frame length: %d\n", len);
    lastErrorMessage = "BAD_LEN_" + String(len);
    http.end();
    return false;
  }

  WiFiClient* stream = http.getStreamPtr();
  uint8_t header[FRAME_HEADER_BYTES];
  if (!readExact(stream, header, sizeof(header))) {
    Serial.println("frame header timeout");
    lastErrorMessage = "HEADER_TIMEOUT";
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
    lastErrorMessage = "INVALID_HEADER";
    http.end();
    return false;
  }

  if (!readExact(stream, blackPlane, EPD_BYTES) || !readExact(stream, redPlane, EPD_BYTES)) {
    Serial.println("frame body timeout");
    lastErrorMessage = "BODY_TIMEOUT";
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
  index++;
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
    Serial.printf("[UDP] 正在监听广播端口: %d\n", UDP_BROADCAST_PORT);
  } else {
    Serial.printf("[UDP] 监听端口 %d 失败!\n", UDP_BROADCAST_PORT);
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
      Serial.printf("[UDP] 成功解析服务端: %s\n", discoveredUrl.c_str());
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
    Serial.print("WiFi 已连接，设备 IP: ");
    Serial.println(WiFi.localIP());
    startUdpBroadcastListener();
  } else {
    Serial.println("WiFi 连接超时，将在后台自动重试");
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
  Serial.println("==================================================");
  Serial.println("ESP32-C3 E-Paper Client (v0.1.5 with Diagnostics)");
  Serial.println("==================================================");

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

  if (WiFi.status() != WL_CONNECTED) {
    displayDiagnosticScreen("WIFI CONNECT FAILED", "CHECK SSID & PASSWORD IN INO", ("SSID: " + String(WIFI_SSID)).c_str(), "RETRYING IN BACKGROUND...", true);
    return;
  }

  if (hasServerUrl) {
    bool force = false;
    pollDeviceConfig(&force);
    if (fetchFrame()) {
      displayFrame();
      lastDisplayMs = millis();
    } else {
      displayDiagnosticScreen("CONNECT ERROR", ("SERVER: " + serverBaseUrl).c_str(), ("ERR: " + lastErrorMessage).c_str(), "CLICK 'BROADCAST' ON WEB", true);
    }
  } else {
    // 未配对时直接在墨水屏上显示引导画面，包含 IP 和广播监听端口！
    displayDiagnosticScreen("WAITING BROADCAST", ("WIFI OK, IP: " + WiFi.localIP().toString()).c_str(), "LISTENING: UDP 15002", "CLICK 'BROADCAST' ON WEB", false);
  }
}

void loop() {
  ArduinoOTA.handle();
  uint32_t now = millis();

  // 非阻塞 WiFi 重连守护
  if (WiFi.status() != WL_CONNECTED) {
    if (now - lastWifiRetryMs > 10000UL) {
      lastWifiRetryMs = now;
      Serial.println("[WIFI] 断开连接，尝试重连...");
      WiFi.reconnect();
    }
    delay(20);
    return;
  } else if (!udpListening) {
    startUdpBroadcastListener();
  }

  // 监听并解析 UDP 广播
  bool serverChanged = processUdpBroadcast();

  if (serverChanged) {
    Serial.println("[UDP] 收到新服务器广播宣告，立即刷新屏幕...");
    bool force = false;
    pollDeviceConfig(&force);
    if (fetchFrame()) {
      displayFrame();
      lastDisplayMs = millis();
    } else {
      displayDiagnosticScreen("CONNECT ERROR", ("SERVER: " + serverBaseUrl).c_str(), ("ERR: " + lastErrorMessage).c_str(), "CHECK LOGS ON WEB", true);
    }
  }

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
      } else {
        Serial.printf("[FETCH] 拉取失败: %s\n", lastErrorMessage.c_str());
      }
    }
  }

  delay(20);
}
