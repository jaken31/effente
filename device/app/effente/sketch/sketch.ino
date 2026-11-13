// effente device sketch (UNO Q microcontroller): sensors in, screen and button.
//
// - MPU6050 or MPU-6500 (Wire2, 0x68): accelerometer +-8 g, gyroscope +-250 deg/s, 1 kHz,
//   through the sensor's own FIFO so no sample is missed. Every ~25 ms one Bridge notification
//   "imu" carries a batch: (first sample number, micros() when read, samples, accel bytes,
//   gyro bytes, FIFO overflows so far). Accelerometer: every sample (6 bytes each). Gyroscope:
//   every 10th sample (100 a second), since the 115200-baud Bridge line cannot carry both at
//   1 kHz. Big-endian int16 counts, as the sensor gives them.
// - OLED (Wire2, 0x3C): redrawn as fast as the bus allows (full frames, long writes, 1 MHz for the
//   screen only). The Linux side calls "screen" with the layout: up to 8 lines, where {vib}, {peak}
//   and {temp} are filled in live from every sample and a line "{bar}" is a vibration level bar.
//   So the live numbers move at the screen's own rate, not the Bridge line's.
// - Button on D2 to ground (internal pull-up): each press sends a "button" notification.
// - The IMU's temperature sensor (TEMP_OUT) in every batch, as raw counts (deg C: MPU-6500 raw / 333.87 + 21,
//   MPU-6050 raw / 340 + 36.53; the Linux side converts, device/intake/source.py imu_temp_c).
// - Status once every 5 s on the serial monitor.
// Track 1 owns firmware; this is track 2's sensor sketch until theirs replaces it.

#include <Arduino_RouterBridge.h>
#include <Wire.h>

TwoWire &BUS = Wire2;
const uint8_t MPU_ADDR = 0x68, OLED_ADDR = 0x3C;  // not "MPU": that name is the chip's memory protection unit
const uint8_t OLED_COL_OFFSET = 0;                 // 2 for an SH1106 controller
const int BUTTON_PIN = 9;  // D9 to GND (the board as wired, 13 Nov)
const uint8_t GYRO_EVERY = 10;     // 1 kHz / 10 = 100 gyro samples a second
const uint16_t MAX_BATCH = 30;     // samples per notification: 180 + 18 bytes, inside the 256-byte RPC buffer
const uint32_t READ_EVERY_US = 25000;

// ------------------------------------------------------------------ MPU6050
bool mpuWrite(uint8_t reg, uint8_t val) {
  BUS.beginTransmission(MPU_ADDR);
  BUS.write(reg);
  BUS.write(val);
  return BUS.endTransmission() == 0;
}

int mpuRead(uint8_t reg, uint8_t *buf, uint8_t n) {
  BUS.beginTransmission(MPU_ADDR);
  BUS.write(reg);
  if (BUS.endTransmission(false) != 0) return -1;
  uint8_t got = BUS.requestFrom(MPU_ADDR, n);
  for (uint8_t i = 0; i < got; i++) buf[i] = BUS.read();
  return got;
}

bool mpuOk = false;
uint8_t busClocks = 0;  // clock pulses the last bus recovery needed to free the data line
uint8_t lastBusClocks = 0, busTries = 0;      // for the heartbeat: the last recovery, and how many there were
bool sdaWasHigh = true, sclWasHigh = true;    // the two lines' levels, released, at the last recovery

// Free a bus left mid-transfer. Restarting the app resets this microcontroller wherever it is, but the
// sensor and the screen stay powered: one caught halfway through a reply keeps holding the data line low,
// and nothing reaches them until the power is cut (12 Nov). So, before every start: release both lines,
// pulse the clock until the data line is let go (at most 16 times), then a STOP so every device knows the
// transfer is over. Wire2 is the chip's I2C3: clock PC0 = A5, data PC1 = A4. BUS.begin() afterwards
// re-applies the I2C pin setup (the core's Wire::begin re-applies the default pinctrl state).
void busRecover() {
  const uint32_t HALF_US = 10;  // about 50 kHz
  pinMode(A4, INPUT_PULLUP);    // data, released
  pinMode(A5, INPUT_PULLUP);    // clock, released
  delayMicroseconds(HALF_US);
  sdaWasHigh = digitalRead(A4) == HIGH;
  sclWasHigh = digitalRead(A5) == HIGH;  // low here: a device is holding the clock (pulsing can't help)
  busTries++;
  busClocks = 0;
  while (busClocks < 16 && digitalRead(A4) == LOW) {
    pinMode(A5, OUTPUT);
    digitalWrite(A5, LOW);
    delayMicroseconds(HALF_US);
    pinMode(A5, INPUT_PULLUP);
    delayMicroseconds(HALF_US);
    busClocks++;
  }
  pinMode(A4, OUTPUT);          // STOP: the data line rises while the clock is high
  digitalWrite(A4, LOW);
  delayMicroseconds(HALF_US);
  pinMode(A5, INPUT_PULLUP);
  delayMicroseconds(HALF_US);
  pinMode(A4, INPUT_PULLUP);
  delayMicroseconds(HALF_US);
  lastBusClocks = busClocks;
}
uint32_t overflows = 0, readErrors = 0;  // FIFO restarts: full, or after a failed read
uint8_t whoAmI = 0;
uint16_t fifoBytes = 512;  // FIFO size: 1024 on the MPU-6050, 512 on the MPU-6500 and MPU-9250

void fifoReset() {
  mpuWrite(0x6A, 0x04);  // USER_CTRL: reset the FIFO
  delay(1);
  mpuWrite(0x6A, 0x40);  // USER_CTRL: FIFO on
}

bool mpuBegin() {
  uint8_t who = 0;
  mpuRead(0x75, &who, 1);
  whoAmI = who;
  fifoBytes = who == 0x68 ? 1024 : 512;
  // DLPF_CFG 1 with SMPLRT_DIV 0 gives exactly 1 kHz on both the MPU-6050 (WHO_AM_I 0x68) and the
  // MPU-6500 that many "MPU6050" boards carry (0x70; this board's). With DLPF_CFG 0 the 6500 runs
  // its FIFO at about 1,445 a second. Accelerometer low-pass: 184 Hz (6050) or 218 Hz (6500).
  bool ok = mpuWrite(0x6B, 0x01)    // PWR_MGMT_1: wake, clock from the X gyro
            && mpuWrite(0x1A, 0x01) // CONFIG: DLPF_CFG 1 (1 kHz internal rate)
            && mpuWrite(0x19, 0)    // SMPLRT_DIV: 1 kHz / 1 = 1 kHz
            && mpuWrite(0x1B, 0x00) // GYRO_CONFIG: +-250 deg/s
            && mpuWrite(0x1C, 0x10) // ACCEL_CONFIG: +-8 g
            && mpuWrite(0x23, 0x78); // FIFO_EN: accelerometer and gyroscope (12 bytes per sample)
  if (who == 0x70) mpuWrite(0x1D, 0x00);  // ACCEL_CONFIG2 (6500 only): 218 Hz low-pass, 1 kHz
  fifoReset();
  Monitor.print("MPU6050 WHO_AM_I 0x");
  Monitor.println(who, HEX);
  return ok && (who == 0x68 || who == 0x98 || who == 0x70);
}

uint16_t fifoCount() {
  uint8_t b[2];
  if (mpuRead(0x72, b, 2) != 2) return 0;
  return (b[0] << 8) | b[1];
}

// ------------------------------------------------------------------ OLED
const uint8_t FONT[96][5] = {
  {0x00,0x00,0x00,0x00,0x00},{0x00,0x00,0x5F,0x00,0x00},{0x00,0x07,0x00,0x07,0x00},{0x14,0x7F,0x14,0x7F,0x14},
  {0x24,0x2A,0x7F,0x2A,0x12},{0x23,0x13,0x08,0x64,0x62},{0x36,0x49,0x55,0x22,0x50},{0x00,0x05,0x03,0x00,0x00},
  {0x00,0x1C,0x22,0x41,0x00},{0x00,0x41,0x22,0x1C,0x00},{0x08,0x2A,0x1C,0x2A,0x08},{0x08,0x08,0x3E,0x08,0x08},
  {0x00,0x50,0x30,0x00,0x00},{0x08,0x08,0x08,0x08,0x08},{0x00,0x60,0x60,0x00,0x00},{0x20,0x10,0x08,0x04,0x02},
  {0x3E,0x51,0x49,0x45,0x3E},{0x00,0x42,0x7F,0x40,0x00},{0x42,0x61,0x51,0x49,0x46},{0x21,0x41,0x45,0x4B,0x31},
  {0x18,0x14,0x12,0x7F,0x10},{0x27,0x45,0x45,0x45,0x39},{0x3C,0x4A,0x49,0x49,0x30},{0x01,0x71,0x09,0x05,0x03},
  {0x36,0x49,0x49,0x49,0x36},{0x06,0x49,0x49,0x29,0x1E},{0x00,0x36,0x36,0x00,0x00},{0x00,0x56,0x36,0x00,0x00},
  {0x08,0x14,0x22,0x41,0x00},{0x14,0x14,0x14,0x14,0x14},{0x00,0x41,0x22,0x14,0x08},{0x02,0x01,0x51,0x09,0x06},
  {0x32,0x49,0x79,0x41,0x3E},{0x7E,0x11,0x11,0x11,0x7E},{0x7F,0x49,0x49,0x49,0x36},{0x3E,0x41,0x41,0x41,0x22},
  {0x7F,0x41,0x41,0x22,0x1C},{0x7F,0x49,0x49,0x49,0x41},{0x7F,0x09,0x09,0x01,0x01},{0x3E,0x41,0x41,0x51,0x32},
  {0x7F,0x08,0x08,0x08,0x7F},{0x00,0x41,0x7F,0x41,0x00},{0x20,0x40,0x41,0x3F,0x01},{0x7F,0x08,0x14,0x22,0x41},
  {0x7F,0x40,0x40,0x40,0x40},{0x7F,0x02,0x04,0x02,0x7F},{0x7F,0x04,0x08,0x10,0x7F},{0x3E,0x41,0x41,0x41,0x3E},
  {0x7F,0x09,0x09,0x09,0x06},{0x3E,0x41,0x51,0x21,0x5E},{0x7F,0x09,0x19,0x29,0x46},{0x46,0x49,0x49,0x49,0x31},
  {0x01,0x01,0x7F,0x01,0x01},{0x3F,0x40,0x40,0x40,0x3F},{0x1F,0x20,0x40,0x20,0x1F},{0x7F,0x20,0x18,0x20,0x7F},
  {0x63,0x14,0x08,0x14,0x63},{0x03,0x04,0x78,0x04,0x03},{0x61,0x51,0x49,0x45,0x43},{0x00,0x00,0x7F,0x41,0x41},
  {0x02,0x04,0x08,0x10,0x20},{0x41,0x41,0x7F,0x00,0x00},{0x04,0x02,0x01,0x02,0x04},{0x40,0x40,0x40,0x40,0x40},
  {0x00,0x01,0x02,0x04,0x00},{0x20,0x54,0x54,0x54,0x78},{0x7F,0x48,0x44,0x44,0x38},{0x38,0x44,0x44,0x44,0x20},
  {0x38,0x44,0x44,0x48,0x7F},{0x38,0x54,0x54,0x54,0x18},{0x08,0x7E,0x09,0x01,0x02},{0x08,0x14,0x54,0x54,0x3C},
  {0x7F,0x08,0x04,0x04,0x78},{0x00,0x44,0x7D,0x40,0x00},{0x20,0x40,0x44,0x3D,0x00},{0x00,0x7F,0x10,0x28,0x44},
  {0x00,0x41,0x7F,0x40,0x00},{0x7C,0x04,0x18,0x04,0x78},{0x7C,0x08,0x04,0x04,0x78},{0x38,0x44,0x44,0x44,0x38},
  {0x7C,0x14,0x14,0x14,0x08},{0x08,0x14,0x14,0x18,0x7C},{0x7C,0x08,0x04,0x04,0x08},{0x48,0x54,0x54,0x54,0x20},
  {0x04,0x3F,0x44,0x40,0x20},{0x3C,0x40,0x40,0x20,0x7C},{0x1C,0x20,0x40,0x20,0x1C},{0x3C,0x40,0x30,0x40,0x3C},
  {0x44,0x28,0x10,0x28,0x44},{0x0C,0x50,0x50,0x50,0x3C},{0x44,0x64,0x54,0x4C,0x44},{0x00,0x08,0x36,0x41,0x00},
  {0x00,0x00,0x7F,0x00,0x00},{0x00,0x41,0x36,0x08,0x00},{0x08,0x08,0x2A,0x1C,0x08},{0x00,0x00,0x00,0x00,0x00},
};

uint8_t fb[8][128];
uint8_t onScreen[8][128];          // what the screen shows now: only pages that differ are sent
bool screenKnown = false;          // false until every page has been sent once (or after the screen came back)
bool oledOk = false;
const uint32_t MPU_I2C_HZ = 400000;     // the MPU-6500 is rated for 400 kHz
// The screen shares the bus with the MPU. At 1 MHz the MPU's readings came back corrupted on the board
// (12 Nov: 2 g of "vibration" and 0.2-0.5 g of gravity at rest), so the whole bus stays at 400 kHz.
const uint32_t OLED_I2C_HZ = 400000;
const uint32_t FRAME_EVERY_MS = 0;      // as fast as the bus allows (the user): drawn a page per pass, so the MPU
                                        // is still read in between, and only pages that changed are sent
uint16_t oledChunk = 128;               // data bytes per I2C write; falls back to 32, then 16
uint32_t frames = 0, frameUs = 0, oledErrors = 0;

void oledCmds(const uint8_t *c, uint8_t n) {  // several commands in one transmission
  BUS.beginTransmission(OLED_ADDR);
  BUS.write(0x00);
  for (uint8_t i = 0; i < n; i++) BUS.write(c[i]);
  BUS.endTransmission();
}

bool oledBegin() {
  BUS.beginTransmission(OLED_ADDR);
  if (BUS.endTransmission() != 0) return false;
  const uint8_t init[] = {0xAE, 0xD5, 0xF0, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14, 0xA1, 0xC8,
                          0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF};
  for (uint8_t c : init) oledCmds(&c, 1);  // 0xD5 0xF0: fastest internal refresh
  return true;
}

// One page (an eighth of the screen, about 3.5 ms at 400 kHz) per loop pass, never the whole screen at
// once: a whole frame held the bus for about 27 ms, and with the sensor read on top the MPU-6500's 512-byte
// FIFO (42 ms of samples) overflowed and the samples after it came out of step (12 Nov, on the board).
bool oledPage(uint8_t page) {  // false if any write failed: the page is then sent again
  BUS.setClock(OLED_I2C_HZ);
  bool ok = true;
  const uint8_t pos[3] = {(uint8_t)(0xB0 + page), (uint8_t)(0x00 + (OLED_COL_OFFSET & 0x0F)),
                          (uint8_t)(0x10 + (OLED_COL_OFFSET >> 4))};
  oledCmds(pos, 3);
  for (uint16_t x = 0; x < 128; x += oledChunk) {
    BUS.beginTransmission(OLED_ADDR);
    BUS.write(0x40);
    for (uint16_t i = 0; i < oledChunk && x + i < 128; i++) BUS.write(fb[page][x + i]);
    if (BUS.endTransmission() != 0) {
      ok = false;
      oledErrors++;
      if (oledChunk > 16) oledChunk = oledChunk > 32 ? 32 : 16;  // the bus refused a long write
    }
  }
  BUS.setClock(MPU_I2C_HZ);
  return ok;
}

void textLine(uint8_t line, const char *s, uint8_t len) {
  memset(fb[line], 0, 128);
  for (uint8_t i = 0; i < len && i < 21; i++) {
    uint8_t c = (s[i] < 32 || s[i] > 127) ? '?' : s[i];
    memcpy(&fb[line][i * 6], FONT[c - 32], 5);
  }
}

// ---- live values, from every sample (not from the Linux side, so they update every frame)
float grav[3] = {0, 0, 1};
bool gravInit = false;
float sumSq = 0, peakNow = 0, shownRms2 = 0, shownPeak = 0, imuTempC = NAN;
float fSum[3] = {0, 0, 0}, fSumSq[3] = {0, 0, 0};  // this frame's samples, per axis: for the shown level
uint32_t nSq = 0, peakHeldAt = 0;

void onSample(const uint8_t *s) {
  float a[3], d2 = 0;
  for (int i = 0; i < 3; i++) a[i] = (int16_t)((s[2 * i] << 8) | s[2 * i + 1]) / 4096.0f;
  if (!gravInit) { for (int i = 0; i < 3; i++) grav[i] = a[i]; gravInit = true; }
  for (int i = 0; i < 3; i++) {
    grav[i] += 0.002f * (a[i] - grav[i]);  // about half a second: gravity and tilt
    float d = a[i] - grav[i];
    d2 += d * d;
  }
  sumSq += d2;
  for (int i = 0; i < 3; i++) {
    fSum[i] += a[i];
    fSumSq[i] += a[i] * a[i];
  }
  nSq++;
  float m = sqrtf(d2);
  if (m > peakNow) peakNow = m;
}

void updateLive(float dt) {
  if (nSq) {
    // The shown level is how much this frame's samples vary around their own mean, per axis: nothing
    // carries over from earlier frames, so it drops the moment the shaking stops. (Subtracting the slow
    // gravity estimate instead left a second or so of "vibration" after shaking or tilting the board,
    // while the estimate caught up.) A light 50 ms average keeps the number readable. Display only: the
    // data sent to the Linux side (and so the model and the safety checks) is never smoothed.
    float var = 0;
    for (int i = 0; i < 3; i++) {
      float mean = fSum[i] / nSq;
      float v = fSumSq[i] / nSq - mean * mean;
      var += v > 0 ? v : 0;
    }
    float a = dt / 0.8f;  // steadied over about 0.8 s (the user: a tad more than 0.6 s)
    shownRms2 += (a > 1 ? 1 : a) * (var - shownRms2);
  }
  sumSq = 0;
  for (int i = 0; i < 3; i++) fSum[i] = fSumSq[i] = 0;
  nSq = 0;
  if (peakNow >= shownPeak || millis() - peakHeldAt > 300) {  // the biggest jolt of the last 0.3 s
    shownPeak = peakNow;
    peakHeldAt = millis();
  }
  peakNow = 0;
}

// ---- the Linux side's layout: up to 8 lines; {vib} {peak} {temp} are filled in live, {bar} is a level bar
char tmpl[8][48];
uint32_t screenUpdates = 0;

char *fnum(char *out, float v, uint8_t dec) {  // by hand: small cores often lack float printf
  if (!(v == v)) { strcpy(out, "--"); return out; }
  char *p = out;
  if (v < 0) { *p++ = '-'; v = -v; }
  uint32_t scale = 1;
  for (uint8_t i = 0; i < dec; i++) scale *= 10;
  uint32_t n = (uint32_t)(v * scale + 0.5f);
  p += sprintf(p, "%lu", (unsigned long)(n / scale));
  if (dec) sprintf(p, ".%0*lu", dec, (unsigned long)(n % scale));
  return out;
}

// log10 without libm: the board's newer Zephyr toolchain links libm's log10f to a missing __errno.
// Exponent plus a quadratic for the mantissa: within 0.0024 of log10, a tenth of a bar pixel.
float log10Approx(float x) {
  union { float f; uint32_t i; } u = {x};
  int e = (int)((u.i >> 23) & 0xFF) - 127;
  u.i = (u.i & 0x007FFFFF) | 0x3F800000;  // the mantissa, in [1, 2)
  float t = u.f - 1.0f;
  return (e + t * (1.34484843f - 0.34484843f * t)) * 0.30103f;
}

void barLine(uint8_t line, float mg) {  // log scale: 1 mg at the left edge, 1000 mg at the right, in a frame
  memset(fb[line], 0, 128);
  float x = mg > 1 ? log10Approx(mg) / 3.0f : 0;
  uint8_t w = (uint8_t)((x > 1 ? 1 : x) * 124);
  for (uint8_t i = 1; i < 127; i++) fb[line][i] = 0x41;             // the frame's top and bottom edges
  fb[line][0] = fb[line][127] = 0x7F;                               // and its ends
  for (uint8_t i = 2; i < 2 + w && i < 126; i++) fb[line][i] |= 0x1C;  // the level
  for (uint8_t t = 42; t < 127; t += 42) fb[line][t] |= 0x22;       // ticks at 10 and 100 mg
}

// Double-size text over two pages (this one and the next), up to 10 characters: the font's 5x7
// glyphs with every pixel doubled. For the number people read from across the room.
void bigLine(uint8_t line, const char *s, uint8_t len) {
  memset(fb[line], 0, 128);
  if (line + 1 < 8) memset(fb[line + 1], 0, 128);
  for (uint8_t i = 0; i < len && i < 10; i++) {
    uint8_t c = (s[i] < 32 || s[i] > 127) ? '?' : s[i];
    for (uint8_t col = 0; col < 5; col++) {
      uint8_t b = FONT[c - 32][col];
      uint16_t tall = 0;
      for (uint8_t k = 0; k < 7; k++)
        if (b & (1 << k)) tall |= (uint16_t)3 << (2 * k);
      for (uint8_t dx = 0; dx < 2; dx++) {
        uint8_t x = i * 12 + col * 2 + dx;
        if (x >= 128) continue;
        fb[line][x] = tall & 0xFF;
        if (line + 1 < 8) fb[line + 1][x] = tall >> 8;
      }
    }
  }
}

// ---- design B ("tiles", the user's pick, 13 Nov): a header, then four boxed readings drawn large.
// The Linux side sends 4 lines: "@<header left>" ('!' first: something is wrong; the '!' isn't drawn),
// the header's right side (the time), the tile that goes with what is wrong ("vib", "peak", "temp", "hz" or
// empty; no longer drawn differently, as the user asked on 13 Nov: no inverted, white backgrounds), and the
// running speed. Vibration, peak and temperature come from the samples here, so they move every frame.
void px(int x, int y, bool on) {
  if (x < 0 || x > 127 || y < 0 || y > 63) return;
  uint8_t bit = 1 << (y & 7);
  if (on) fb[y >> 3][x] |= bit; else fb[y >> 3][x] &= ~bit;
}

int textWidth(const char *s, uint8_t k) {
  int n = strlen(s);
  return n ? n * 6 * k - k : 0;
}

void drawText(int x, int y, const char *s, uint8_t k, bool on) {
  for (int i = 0; s[i]; i++) {
    uint8_t c = (s[i] < 32 || s[i] > 127) ? '?' : s[i];
    for (uint8_t col = 0; col < 5; col++)
      for (uint8_t r = 0; r < 7; r++)
        if (FONT[c - 32][col] & (1 << r))
          for (uint8_t dx = 0; dx < k; dx++)
            for (uint8_t dy = 0; dy < k; dy++) px(x + i * 6 * k + col * k + dx, y + r * k + dy, on);
  }
}

void fillRect(int x, int y, int w, int h, bool on) {
  for (int i = x; i < x + w; i++)
    for (int j = y; j < y + h; j++) px(i, j, on);
}

void outline(int x, int y, int w, int h) {  // a box with its corner pixels left off, so it reads rounded
  for (int i = x + 1; i < x + w - 1; i++) { px(i, y, true); px(i, y + h - 1, true); }
  for (int j = y + 1; j < y + h - 1; j++) { px(x, j, true); px(x + w - 1, j, true); }
}

void composeTiles() {
  memset(fb, 0, sizeof fb);
  char left[24], val[4][12];
  const char *l = tmpl[0] + 1;
  bool alert = l[0] == '!';
  if (alert) l++;
  if (!mpuOk) { l = "NO SENSOR"; alert = true; }
  strncpy(left, l, 23);
  left[23] = 0;
  const char *right = tmpl[1];
  int room = 127 - textWidth(right, 1) - 4;  // keep the left text clear of the time
  while (left[0] && textWidth(left, 1) > room) left[strlen(left) - 1] = 0;
  // Nothing is drawn inverted (the user, 13 Nov: no white backgrounds); the header's words say what is wrong.
  drawText(1, 0, left, 1, true);
  drawText(127 - textWidth(right, 1), 0, right, 1, true);
  for (int x = 0; x < 128; x++) px(x, 8, true);
  float mg = 1000 * sqrtf(shownRms2);
  if (mpuOk) {
    fnum(val[0], mg > 9999 ? 9999 : mg, 0);
    fnum(val[1], 1000 * shownPeak > 9999 ? 9999 : 1000 * shownPeak, 0);
    fnum(val[2], imuTempC * 9.0f / 5.0f + 32.0f, 0);  // the screen shows Fahrenheit (the user); the data stays in C
  } else {
    strcpy(val[0], "--"); strcpy(val[1], "--"); strcpy(val[2], "--");
  }
  strncpy(val[3], tmpl[3][0] ? tmpl[3] : "--", 11);
  val[3][11] = 0;
  static const char *labels[4] = {"VIB", "PEAK", "TEMP", "HZ"};
  static const char *units[4] = {"mg", "mg", "F", ""};
  static const char *keys[4] = {"vib", "peak", "temp", "hz"};
  for (uint8_t i = 0; i < 4; i++) {
    int bx = (i % 2) * 64, by = 10 + (i / 2) * 27;
    outline(bx, by, 63, 26);
    drawText(bx + 3, by + 2, labels[i], 1, true);
    if (units[i][0]) drawText(bx + 60 - textWidth(units[i], 1), by + 2, units[i], 1, true);
    drawText(bx + 32 - textWidth(val[i], 2) / 2, by + 11, val[i], 2, true);
  }
}

// A line starting with '^' is drawn inverted (the header bar); with '#', double size over this line and
// the next (the Linux side leaves the next line empty). A first line starting with '@': design B's tiles.
void compose() {
  if (tmpl[0][0] == '@') {
    composeTiles();
    return;
  }
  char line[64], v[16];
  float mg = 1000 * sqrtf(shownRms2);
  bool skip = false;
  for (uint8_t L = 0; L < 8; L++) {
    if (skip) { skip = false; continue; }  // the second page of a double-size line
    const char *t = tmpl[L];
    bool inverted = t[0] == '^', big = t[0] == '#';
    if (inverted || big) t++;
    if (!mpuOk && strstr(t, "{vib}")) {  // say so, rather than show empty readings
      textLine(L, "SENSOR NOT FOUND", 16);
      if (big && L + 1 < 8) memset(fb[L + 1], 0, 128), skip = true;
      continue;
    }
    if (strcmp(t, "{bar}") == 0) { barLine(L, mg); continue; }
    uint8_t n = 0;
    for (const char *p = t; *p && n < 60;) {
      if (*p == '{') {
        const char *e = strchr(p, '}');
        if (e) {
          if (!strncmp(p, "{vib}", 5)) fnum(v, mg, 0);  // whole numbers on the screen (the user)
          else if (!strncmp(p, "{peak}", 6)) fnum(v, 1000 * shownPeak, 0);  // in mg
          else if (!strncmp(p, "{temp}", 6)) fnum(v, imuTempC, 0);
          else v[0] = 0;
          for (char *q = v; *q && n < 60; q++) line[n++] = *q;
          p = e + 1;
          continue;
        }
      }
      line[n++] = *p++;
    }
    if (big) {
      bigLine(L, line, n);
      skip = true;
    } else {
      textLine(L, line, n);
      if (inverted)
        for (uint8_t x = 0; x < 128; x++) fb[L][x] ^= 0xFF;
    }
  }
}

// Called from the Linux side; runs between loop() passes ("safe"), so the I2C bus is free.
bool screen(MsgPack::str_t text) {
  const char *s = text.c_str();
  uint8_t line = 0;
  const char *start = s;
  for (const char *p = s;; p++) {
    if (*p == '\n' || *p == 0) {
      if (line < 8) {
        uint8_t len = (uint8_t)(p - start);
        if (len > 47) len = 47;
        memcpy(tmpl[line], start, len);
        tmpl[line][len] = 0;
        line++;
      }
      start = p + 1;
      if (*p == 0) break;
    }
  }
  while (line < 8) tmpl[line++][0] = 0;
  screenUpdates++;
  return true;
}

// ------------------------------------------------------------------ main
uint32_t seq = 0, lastRead = 0, lastStatus = 0, batches = 0, presses = 0, notifyUs = 0, lastFrameAt = 0;
bool buttonDown = false;
uint32_t buttonChange = 0;
uint32_t lastSampleMs = 0;  // when the FIFO last gave a sample
uint32_t sensorRestarts = 0;

void setup() {
  Bridge.begin();
  Monitor.begin();
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  delay(300);  // let the sensor and the screen power up before the first look (12 Nov: with both on the bus,
               // the sensor's single check at power-up failed and the sketch never read it again)
  busRecover();  // a device left mid-reply by the restart lets go of the bus
  BUS.begin();
  BUS.setClock(100000);  // the first look at a gentle 100 kHz
  mpuOk = mpuBegin();
  lastSampleMs = millis();
  oledOk = oledBegin();
  BUS.setClock(MPU_I2C_HZ);
  Bridge.provide_safe("screen", screen);
  const char *hello = "@STARTING\n\n\n--";
  screen(MsgPack::str_t(hello));
  Monitor.print("effente sketch: MPU6050 ");
  Monitor.print(mpuOk ? "ok" : "NOT FOUND");
  Monitor.print(", OLED ");
  Monitor.println(oledOk ? "ok" : "NOT FOUND");
}

void readFifo() {
  uint16_t avail = fifoCount();
  if (avail >= fifoBytes - 24) {  // nearly full: data was lost (a full FIFO overwrites bytes); start clean, say so
    overflows++;
    fifoReset();
    return;
  }
  uint16_t n = avail / 12;
  while (n > 0) {
    uint16_t take = n > MAX_BATCH ? MAX_BATCH : n;
    MsgPack::bin_t<uint8_t> acc, gyr;
    acc.reserve(take * 6);
    uint8_t s[12];
    uint16_t got = 0;
    bool failed = false;
    for (; got < take; got++) {
      // FIFO_R_W: one sample (accel xyz, gyro xyz). A short or failed read has still moved the FIFO on,
      // so every later sample would be assembled from the wrong bytes: reset it and start clean instead.
      if (mpuRead(0x74, s, 12) != 12) { failed = true; break; }
      for (int k = 0; k < 6; k++) acc.push_back(s[k]);
      onSample(s);
      if ((seq + got) % GYRO_EVERY == 0) for (int k = 6; k < 12; k++) gyr.push_back(s[k]);
    }
    if (failed) {
      readErrors++;
      overflows++;  // reported like an overflow: samples were lost and the FIFO restarted
      fifoReset();
      n = take = got;  // send what was read, then stop until the next read
    }
    if (take == 0) break;
    lastSampleMs = millis();
    uint32_t t = micros();
    uint8_t tb[2] = {0, 0};
    mpuRead(0x41, tb, 2);  // TEMP_OUT: the IMU's temperature sensor
    int16_t tempRaw = (int16_t)((tb[0] << 8) | tb[1]);
    // The 6500's formula differs from the 6050's (13 Nov: the 6050's read about 15 C too high on our 6500).
    imuTempC = whoAmI == 0x68 ? tempRaw / 340.0f + 36.53f : tempRaw / 333.87f + 21.0f;
    uint32_t n0 = micros();
    Bridge.notify("imu", seq, t, (uint32_t)take, acc, gyr, overflows, tempRaw);
    notifyUs += micros() - n0;
    seq += take;
    batches++;
    n -= take;
  }
}

void loop() {
  static uint32_t lastTry = 0;  // a sensor or screen that didn't answer: look again every 2 s
  // A sensor that answers but gives no samples: a glitch on its wires or power reset it, so it is back in
  // its power-on state (asleep, FIFO off) and reports an empty FIFO for ever (13 Nov, on the board: the
  // samples stopped for good 35 s in, with the sensor still "found"). Set it up again.
  if (mpuOk && millis() - lastSampleMs > 500) {
    mpuOk = false;
    gravInit = false;
    sensorRestarts++;
    lastTry = millis() - 2000;  // now, not in 2 s
  }
  if ((!mpuOk || !oledOk) && millis() - lastTry >= 2000) {
    lastTry = millis();
    busRecover();
    BUS.begin();
    BUS.setClock(100000);
    if (!mpuOk) mpuOk = mpuBegin();
    if (!oledOk) {
      oledOk = oledBegin();
      screenKnown = false;
    }
    BUS.setClock(MPU_I2C_HZ);
    if (mpuOk) {
      lastRead = micros();
      lastSampleMs = millis();  // half a second to give its first sample
    }
  }
  uint32_t now = micros();
  if (mpuOk && now - lastRead >= READ_EVERY_US) {
    lastRead = now;
    readFifo();
  }
  static uint8_t nextPage = 8;  // 8: no frame being drawn
  if (oledOk && nextPage >= 8 && micros() - lastFrameAt >= FRAME_EVERY_MS * 1000) {  // a frame every FRAME_EVERY_MS
    uint32_t at = micros();
    updateLive((at - lastFrameAt) / 1e6f);
    lastFrameAt = at;
    compose();
    nextPage = 0;
    static uint32_t lastFullAt = 0;  // every 2 s, send every page: a garbled write the bus didn't report can't stick
    if (millis() - lastFullAt >= 2000) {
      lastFullAt = millis();
      screenKnown = false;
    }
  }
  while (oledOk && nextPage < 8 && screenKnown && !memcmp(fb[nextPage], onScreen[nextPage], 128))
    nextPage++;  // unchanged: nothing to send
  if (oledOk && nextPage < 8) {  // one changed page per pass, so the FIFO is read in between
    if (oledPage(nextPage))
      memcpy(onScreen[nextPage], fb[nextPage], 128);
    else
      memset(onScreen[nextPage], 0xA5, 128);  // unknown: differs from anything drawn, so it goes again next frame
    nextPage++;
  }
  if (oledOk && nextPage == 8 && lastFrameAt) {
    if (!screenKnown) screenKnown = true;
    frameUs = micros() - lastFrameAt;
    frames++;
    nextPage = 9;  // counted once; 8 or more means no frame in progress
  }
  static uint32_t lastBeat = 0;  // a heartbeat a second, so the Linux side can tell what state this is in
  if (millis() - lastBeat >= 1000) {
    lastBeat = millis();
    Bridge.notify("hb", (uint32_t)millis(), mpuOk, oledOk, (uint32_t)whoAmI, (uint32_t)lastBusClocks,
                  (uint32_t)busTries, sdaWasHigh, sclWasHigh, (uint32_t)readErrors, (uint32_t)oledErrors,
                  (uint32_t)frames);
  }
  bool down = digitalRead(BUTTON_PIN) == LOW;
  if (down != buttonDown && millis() - buttonChange > 30) {  // 30 ms debounce
    buttonDown = down;
    buttonChange = millis();
    if (down) Bridge.notify("button", ++presses);
  }
  if (millis() - lastStatus >= 5000) {
    static uint32_t lastSeq = 0, lastUs = 0;
    uint32_t us = micros();
    float rate = lastUs ? (seq - lastSeq) * 1e6f / (us - lastUs) : 0;
    lastSeq = seq;
    lastUs = us;
    lastStatus = millis();
    // One message, not twenty: each Monitor.print is its own Bridge call, and twenty in a row held the
    // loop long enough for the FIFO to overflow once every report (12 Nov).
    static uint32_t lastFrames = 0;
    char msg[240];
    snprintf(msg, sizeof msg, "WHO_AM_I 0x%02X, rate %d/s, screen %lu fps (frame %lu us, chunk %u, errors %lu), "
             "Bridge sends %lu%% of the time, samples %lu, batches %lu, FIFO restarts %lu (read errors %lu), "
             "screen updates %lu, button presses %lu",
             whoAmI, (int)rate, (unsigned long)((frames - lastFrames) / 5), (unsigned long)frameUs, oledChunk,
             (unsigned long)oledErrors, (unsigned long)(notifyUs / 50000), (unsigned long)seq,
             (unsigned long)batches, (unsigned long)overflows, (unsigned long)readErrors,
             (unsigned long)screenUpdates, (unsigned long)presses);
    lastFrames = frames;
    notifyUs = 0;
    Monitor.println(msg);
    if (busClocks) {  // say once that the bus had to be freed
      Monitor.print("I2C bus was held: freed after ");
      Monitor.print(busClocks);
      Monitor.println(" clock pulses");
      busClocks = 0;
    }
  }
}
