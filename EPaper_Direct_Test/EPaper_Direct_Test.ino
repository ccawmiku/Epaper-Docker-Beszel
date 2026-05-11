#include <Arduino.h>
#include <SPI.h>

// Direct SPI test for GDEY042Z98 / SSD1683 4.2 inch 400x300 e-paper.
// No GxEPD2, no Adafruit_GFX. The command sequence mirrors the GxEPD2 GDEY042Z98 driver.
// Full-refresh-only test build.

#define PIN_CS   7
#define PIN_DC   3
#define PIN_RST  2
#define PIN_BUSY 10

// Set to 1 to use the same default SPI pins that the working GxEPD2 sketch uses.
// Set to 0 and edit PIN_SCK/PIN_MOSI if your wiring needs explicit SPI pins.
#define USE_DEFAULT_SPI_PINS 1

// ESP32-C3 common SPI pins when USE_DEFAULT_SPI_PINS is 0.
#define PIN_SCK  4
#define PIN_MOSI 6
#define PIN_MISO -1

static const int EPD_W = 400;
static const int EPD_H = 300;
static const int EPD_BYTES = EPD_W * EPD_H / 8;

// Logical framebuffers: bit 1 means that color is present at that pixel.
static uint8_t blackBuf[EPD_BYTES];

static bool initDone = false;
static bool powered = false;

// Minimal 5x7 ASCII font, columns low-to-high. Only the characters used by this test are included.
struct Glyph {
  char ch;
  uint8_t col[5];
};

static const Glyph FONT[] PROGMEM = {
  {' ', {0x00,0x00,0x00,0x00,0x00}}, {'-', {0x08,0x08,0x08,0x08,0x08}},
  {'.', {0x00,0x60,0x60,0x00,0x00}}, {':', {0x00,0x36,0x36,0x00,0x00}},
  {'/', {0x20,0x10,0x08,0x04,0x02}}, {'%', {0x63,0x13,0x08,0x64,0x63}},
  {'0', {0x3E,0x51,0x49,0x45,0x3E}}, {'1', {0x00,0x42,0x7F,0x40,0x00}},
  {'2', {0x42,0x61,0x51,0x49,0x46}}, {'3', {0x21,0x41,0x45,0x4B,0x31}},
  {'4', {0x18,0x14,0x12,0x7F,0x10}}, {'5', {0x27,0x45,0x45,0x45,0x39}},
  {'6', {0x3C,0x4A,0x49,0x49,0x30}}, {'7', {0x01,0x71,0x09,0x05,0x03}},
  {'8', {0x36,0x49,0x49,0x49,0x36}}, {'9', {0x06,0x49,0x49,0x29,0x1E}},
  {'A', {0x7E,0x11,0x11,0x11,0x7E}}, {'B', {0x7F,0x49,0x49,0x49,0x36}},
  {'C', {0x3E,0x41,0x41,0x41,0x22}}, {'D', {0x7F,0x41,0x41,0x22,0x1C}},
  {'E', {0x7F,0x49,0x49,0x49,0x41}}, {'F', {0x7F,0x09,0x09,0x09,0x01}},
  {'G', {0x3E,0x41,0x49,0x49,0x7A}}, {'H', {0x7F,0x08,0x08,0x08,0x7F}},
  {'I', {0x00,0x41,0x7F,0x41,0x00}}, {'J', {0x20,0x40,0x41,0x3F,0x01}},
  {'K', {0x7F,0x08,0x14,0x22,0x41}}, {'L', {0x7F,0x40,0x40,0x40,0x40}},
  {'M', {0x7F,0x02,0x0C,0x02,0x7F}}, {'N', {0x7F,0x04,0x08,0x10,0x7F}},
  {'O', {0x3E,0x41,0x41,0x41,0x3E}}, {'P', {0x7F,0x09,0x09,0x09,0x06}},
  {'Q', {0x3E,0x41,0x51,0x21,0x5E}}, {'R', {0x7F,0x09,0x19,0x29,0x46}},
  {'S', {0x46,0x49,0x49,0x49,0x31}}, {'T', {0x01,0x01,0x7F,0x01,0x01}},
  {'U', {0x3F,0x40,0x40,0x40,0x3F}}, {'V', {0x1F,0x20,0x40,0x20,0x1F}},
  {'W', {0x7F,0x20,0x18,0x20,0x7F}}, {'X', {0x63,0x14,0x08,0x14,0x63}},
  {'Y', {0x07,0x08,0x70,0x08,0x07}}, {'Z', {0x61,0x51,0x49,0x45,0x43}},
};

enum Ink : uint8_t {
  INK_WHITE,
  INK_BLACK
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

  sendCommand(0x11); // RAM entry mode: x increase, y increase
  sendData(0x03);

  sendCommand(0x44); // X start/end, in bytes
  sendData(x / 8);
  sendData((x + w - 1) / 8);

  sendCommand(0x45); // Y start/end, little endian
  sendData(y & 0xFF);
  sendData(y >> 8);
  sendData((y + h - 1) & 0xFF);
  sendData((y + h - 1) >> 8);

  sendCommand(0x4E); // X address counter
  sendData(x / 8);
  sendCommand(0x4F); // Y address counter
  sendData(y & 0xFF);
  sendData(y >> 8);
}

void epdInitDisplay() {
  if (initDone) return;
  Serial.println("epd init display");
  hardReset();

  sendCommand(0x12); // SWRESET
  delay(10);

  sendCommand(0x01); // driver output control, height - 1
  sendData((EPD_H - 1) & 0xFF);
  sendData((EPD_H - 1) >> 8);
  sendData(0x00);

  sendCommand(0x3C); // border waveform
  sendData(0x05);

  sendCommand(0x18); // built-in temperature sensor
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
  sendCommand(0x10); // deep sleep
  sendData(0x11);
  initDone = false;
}

void updateFull(bool fast = true) {
  Serial.println("display full refresh");
  if (fast) {
    sendCommand(0x1A); // temperature register
    sendData(0x5A);
    sendData(0x00);
    sendCommand(0x22);
    sendData(0x91); // load LUT for temperature value
    sendCommand(0x20);
    delay(2);
    sendCommand(0x22);
    sendData(0xC7);
    sendCommand(0x20);
  } else {
    sendCommand(0x22);
    sendData(0xF7);
    sendCommand(0x20);
  }
  waitBusy("full refresh", 25000);
  powered = false;
}

void uploadFrameFull() {
  setRamArea(0, 0, EPD_W, EPD_H);

  // GDEY042Z98 uses RAM 0x24 for black/white. White is 1, black is 0.
  sendCommand(0x24);
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWriteFast(PIN_DC, HIGH);
  digitalWriteFast(PIN_CS, LOW);
  for (int i = 0; i < EPD_BYTES; i++) SPI.transfer(~blackBuf[i]);
  digitalWriteFast(PIN_CS, HIGH);
  SPI.endTransaction();

  // RAM 0x26 is cleared to white to avoid red-plane interference.
  sendCommand(0x26);
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWriteFast(PIN_DC, HIGH);
  digitalWriteFast(PIN_CS, LOW);
  for (int i = 0; i < EPD_BYTES; i++) SPI.transfer(0x00);
  digitalWriteFast(PIN_CS, HIGH);
  SPI.endTransaction();
}

void displayFull(bool sleepAfter = true) {
  epdInitDisplay();
  uploadFrameFull();
  updateFull(true);
  if (sleepAfter) epdSleep();
}

void clearBuffers(Ink color = INK_WHITE) {
  memset(blackBuf, 0, sizeof(blackBuf));
  if (color == INK_BLACK) memset(blackBuf, 0xFF, sizeof(blackBuf));
}

void setPixel(int x, int y, Ink color) {
  if (x < 0 || x >= EPD_W || y < 0 || y >= EPD_H) return;
  int index = y * EPD_W + x;
  uint8_t mask = 0x80 >> (index & 7);
  int byteIndex = index >> 3;
  blackBuf[byteIndex] &= ~mask;
  if (color == INK_BLACK) blackBuf[byteIndex] |= mask;
}

void drawFastHLine(int x, int y, int w, Ink color) {
  for (int i = 0; i < w; i++) setPixel(x + i, y, color);
}

void drawFastVLine(int x, int y, int h, Ink color) {
  for (int i = 0; i < h; i++) setPixel(x, y + i, color);
}

void fillRect(int x, int y, int w, int h, Ink color) {
  for (int yy = 0; yy < h; yy++) drawFastHLine(x, y + yy, w, color);
}

void drawRect(int x, int y, int w, int h, Ink color) {
  drawFastHLine(x, y, w, color);
  drawFastHLine(x, y + h - 1, w, color);
  drawFastVLine(x, y, h, color);
  drawFastVLine(x + w - 1, y, h, color);
}

void drawLine(int x0, int y0, int x1, int y1, Ink color) {
  int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
  int dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
  int err = dx + dy;
  while (true) {
    setPixel(x0, y0, color);
    if (x0 == x1 && y0 == y1) break;
    int e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

void drawCircle(int x0, int y0, int r, Ink color) {
  int x = -r, y = 0, err = 2 - 2 * r;
  do {
    setPixel(x0 - x, y0 + y, color);
    setPixel(x0 - y, y0 - x, color);
    setPixel(x0 + x, y0 - y, color);
    setPixel(x0 + y, y0 + x, color);
    int e2 = err;
    if (e2 <= y) err += ++y * 2 + 1;
    if (e2 > x || err > y) err += ++x * 2 + 1;
  } while (x < 0);
}

void fillCircle(int x0, int y0, int r, Ink color) {
  for (int y = -r; y <= r; y++) {
    int span = sqrt((float)r * r - (float)y * y);
    drawFastHLine(x0 - span, y0 + y, span * 2 + 1, color);
  }
}

bool getGlyph(char ch, uint8_t cols[5]) {
  if (ch >= 'a' && ch <= 'z') ch -= 32;
  for (size_t i = 0; i < sizeof(FONT) / sizeof(FONT[0]); i++) {
    char c = pgm_read_byte(&FONT[i].ch);
    if (c == ch) {
      for (int j = 0; j < 5; j++) cols[j] = pgm_read_byte(&FONT[i].col[j]);
      return true;
    }
  }
  memset(cols, 0, 5);
  return false;
}

void drawChar(int x, int y, char ch, Ink color, int scale = 1) {
  uint8_t cols[5];
  getGlyph(ch, cols);
  for (int cx = 0; cx < 5; cx++) {
    for (int cy = 0; cy < 7; cy++) {
      if (cols[cx] & (1 << cy)) {
        if (scale == 1) setPixel(x + cx, y + cy, color);
        else fillRect(x + cx * scale, y + cy * scale, scale, scale, color);
      }
    }
  }
}

void drawText(int x, int y, const char* text, Ink color, int scale = 1) {
  int cursor = x;
  while (*text) {
    drawChar(cursor, y, *text, color, scale);
    cursor += 6 * scale;
    text++;
  }
}

void testBWBlocks() {
  Serial.println("test bw blocks");
  clearBuffers();
  fillRect(0, 0, EPD_W, 52, INK_BLACK);
  drawText(18, 14, "DIRECT BW TEST", INK_WHITE, 2);
  fillRect(24, 84, 96, 80, INK_BLACK);
  drawRect(152, 84, 96, 80, INK_BLACK);
  for (int y = 84; y < 164; y += 8) {
    for (int x = 280; x < 376; x += 8) {
      if (((x + y) / 8) & 1) fillRect(x, y, 8, 8, INK_BLACK);
    }
  }
  drawRect(280, 84, 96, 80, INK_BLACK);
  drawText(42, 184, "BLACK", INK_BLACK, 1);
  drawText(176, 184, "WHITE", INK_BLACK, 1);
  drawText(302, 184, "DITHER", INK_BLACK, 1);
  drawRect(20, 226, 360, 42, INK_BLACK);
  fillRect(21, 227, 119, 40, INK_BLACK);
  for (int x = 260; x < 379; x += 4) drawFastVLine(x, 227, 40, INK_BLACK);
  displayFull();
}

void testGeometry() {
  Serial.println("test geometry");
  clearBuffers();
  drawRect(0, 0, EPD_W, EPD_H, INK_BLACK);
  drawText(14, 18, "GEOMETRY", INK_BLACK, 2);
  for (int x = 0; x < EPD_W; x += 40) drawFastVLine(x, 60, 220, INK_BLACK);
  for (int y = 60; y < EPD_H; y += 30) drawFastHLine(0, y, EPD_W, INK_BLACK);
  fillCircle(74, 130, 36, INK_BLACK);
  drawCircle(74, 130, 50, INK_BLACK);
  drawLine(150, 88, 235, 176, INK_BLACK);
  drawLine(235, 88, 150, 176, INK_BLACK);
  fillRect(270, 96, 86, 72, INK_BLACK);
  drawRect(268, 94, 90, 76, INK_BLACK);
  for (int i = 0; i < 16; i++) {
    fillRect(28 + i * 22, 270 - i * 6, 14, i * 6, INK_BLACK);
  }
  displayFull();
}

void testText() {
  Serial.println("test text");
  clearBuffers();
  drawRect(0, 0, EPD_W, EPD_H, INK_BLACK);
  drawText(18, 24, "TEXT TEST", INK_BLACK, 2);
  drawText(18, 76, "BLACK IS WRITTEN FIRST", INK_BLACK, 1);
  drawText(18, 102, "RED PLANE IS KEPT CLEAR", INK_BLACK, 1);
  fillRect(56, 142, 112, 96, INK_BLACK);
  drawCircle(112, 190, 42, INK_WHITE);
  fillCircle(292, 190, 56, INK_BLACK);
  drawRect(236, 164, 112, 52, INK_BLACK);
  drawText(42, 260, "NO RED IN THIS TEST BUILD", INK_BLACK, 1);
  displayFull();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("EPaper direct SPI test, no GxEPD2");

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
}

void loop() {
  testBWBlocks();
  delay(1800);
  testGeometry();
  delay(1800);
  testText();
  delay(7000);
}
