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
// - The IMU's temperature sensor (TEMP_OUT) in every batch, as raw counts (deg C = raw / 340 + 36.53).
// - Status once every 5 s on the serial monitor.
// Track 1 owns firmware; this is track 2's sensor sketch until theirs replaces it.

#include <Arduino_RouterBridge.h>
#include <Wire.h>

TwoWire &BUS = Wire2;
const uint8_t MPU_ADDR = 0x68, OLED_ADDR = 0x3C;  // not "MPU": that name is the chip's memory protection unit
const uint8_t OLED_COL_OFFSET = 0;                 // 2 for an SH1106 controller
const int BUTTON_PIN = 2;
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
uint32_t overflows = 0;
uint8_t whoAmI = 0;

void fifoReset() {
  mpuWrite(0x6A, 0x04);  // USER_CTRL: reset the FIFO
  delay(1);
  mpuWrite(0x6A, 0x40);  // USER_CTRL: FIFO on
}

bool mpuBegin() {
  uint8_t who = 0;
  mpuRead(0x75, &who, 1);
  whoAmI = who;
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
bool oledOk = false;
const uint32_t MPU_I2C_HZ = 400000;     // the MPU-6500 is rated for 400 kHz
const uint32_t OLED_I2C_HZ = 1000000;   // the screen is written faster (set to 400000 if it glitches)
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

void oledFrame() {  // the whole screen in one go: 8 pages, each one command write and one long data write
  uint32_t t0 = micros();
  BUS.setClock(OLED_I2C_HZ);
  for (uint8_t page = 0; page < 8; page++) {
    const uint8_t pos[3] = {(uint8_t)(0xB0 + page), (uint8_t)(0x00 + (OLED_COL_OFFSET & 0x0F)),
                            (uint8_t)(0x10 + (OLED_COL_OFFSET >> 4))};
    oledCmds(pos, 3);
    for (uint16_t x = 0; x < 128; x += oledChunk) {
      BUS.beginTransmission(OLED_ADDR);
      BUS.write(0x40);
      for (uint16_t i = 0; i < oledChunk && x + i < 128; i++) BUS.write(fb[page][x + i]);
      if (BUS.endTransmission() != 0) {
        oledErrors++;
        if (oledChunk > 16) oledChunk = oledChunk > 32 ? 32 : 16;  // the bus refused a long write
      }
    }
  }
  BUS.setClock(MPU_I2C_HZ);
  frameUs = micros() - t0;
  frames++;
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
  nSq++;
  float m = sqrtf(d2);
  if (m > peakNow) peakNow = m;
}

void updateLive(float dt) {
  if (nSq) {
    float a = dt / 0.25f;  // shown level follows the last quarter second
    shownRms2 += (a > 1 ? 1 : a) * (sumSq / nSq - shownRms2);
  }
  sumSq = 0;
  nSq = 0;
  if (peakNow >= shownPeak || millis() - peakHeldAt > 1000) {  // the biggest jolt of the last second
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

void barLine(uint8_t line, float mg) {  // log scale: 1 mg at the left edge, 1000 mg at the right
  memset(fb[line], 0, 128);
  float x = mg > 1 ? log10f(mg) / 3.0f : 0;
  uint8_t w = (uint8_t)((x > 1 ? 1 : x) * 127);
  for (uint8_t i = 0; i <= w; i++) fb[line][i] = 0x3C;
  for (uint8_t t = 0; t < 128; t += 42) fb[line][t] |= 0x81;  // ticks at 1, 10, 100 mg
}

void compose() {
  char line[64], v[16];
  float mg = 1000 * sqrtf(shownRms2);
  for (uint8_t L = 0; L < 8; L++) {
    const char *t = tmpl[L];
    if (strcmp(t, "{bar}") == 0) { barLine(L, mg); continue; }
    uint8_t n = 0;
    for (const char *p = t; *p && n < 60;) {
      if (*p == '{') {
        const char *e = strchr(p, '}');
        if (e) {
          if (!strncmp(p, "{vib}", 5)) fnum(v, mg, 1);
          else if (!strncmp(p, "{peak}", 6)) fnum(v, shownPeak, 3);
          else if (!strncmp(p, "{temp}", 6)) fnum(v, imuTempC, 1);
          else v[0] = 0;
          for (char *q = v; *q && n < 60; q++) line[n++] = *q;
          p = e + 1;
          continue;
        }
      }
      line[n++] = *p++;
    }
    textLine(L, line, n);
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

void setup() {
  Bridge.begin();
  Monitor.begin();
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  BUS.begin();
  BUS.setClock(MPU_I2C_HZ);
  mpuOk = mpuBegin();
  oledOk = oledBegin();
  Bridge.provide_safe("screen", screen);
  const char *hello = "STARTING\nvib  {vib} mg\n{bar}\npeak {peak} g\n\ntemp {temp} C";
  screen(MsgPack::str_t(hello));
  Monitor.print("effente sketch: MPU6050 ");
  Monitor.print(mpuOk ? "ok" : "NOT FOUND");
  Monitor.print(", OLED ");
  Monitor.println(oledOk ? "ok" : "NOT FOUND");
}

void readFifo() {
  uint16_t avail = fifoCount();
  if (avail >= 1008) {  // nearly full: data was lost; start clean and say so
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
    for (uint16_t i = 0; i < take; i++) {
      if (mpuRead(0x74, s, 12) != 12) break;  // FIFO_R_W: one sample (accel xyz, gyro xyz)
      for (int k = 0; k < 6; k++) acc.push_back(s[k]);
      onSample(s);
      if ((seq + i) % GYRO_EVERY == 0) for (int k = 6; k < 12; k++) gyr.push_back(s[k]);
    }
    uint32_t t = micros();
    uint8_t tb[2] = {0, 0};
    mpuRead(0x41, tb, 2);  // TEMP_OUT: the IMU's temperature sensor (deg C = raw / 340 + 36.53)
    int16_t tempRaw = (int16_t)((tb[0] << 8) | tb[1]);
    imuTempC = tempRaw / 340.0f + 36.53f;
    uint32_t n0 = micros();
    Bridge.notify("imu", seq, t, (uint32_t)take, acc, gyr, overflows, tempRaw);
    notifyUs += micros() - n0;
    seq += take;
    batches++;
    n -= take;
  }
}

void loop() {
  uint32_t now = micros();
  if (mpuOk && now - lastRead >= READ_EVERY_US) {
    lastRead = now;
    readFifo();
  }
  if (oledOk) {  // as fast as the bus allows: a full frame every pass between sensor reads
    uint32_t at = micros();
    updateLive((at - lastFrameAt) / 1e6f);
    lastFrameAt = at;
    compose();
    oledFrame();
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
    Monitor.print("WHO_AM_I 0x");
    Monitor.print(whoAmI, HEX);
    Monitor.print(", rate ");
    Monitor.print((int)rate);
    static uint32_t lastFrames = 0;
    Monitor.print("/s, screen ");
    Monitor.print((frames - lastFrames) / 5);
    Monitor.print(" fps (frame ");
    Monitor.print(frameUs);
    Monitor.print(" us, chunk ");
    Monitor.print(oledChunk);
    Monitor.print(", errors ");
    Monitor.print(oledErrors);
    Monitor.print("), Bridge sends ");
    Monitor.print(notifyUs / 50000);
    Monitor.print("% of the time, samples ");
    lastFrames = frames;
    notifyUs = 0;
    Monitor.print(seq);
    Monitor.print(", batches ");
    Monitor.print(batches);
    Monitor.print(", FIFO overflows ");
    Monitor.print(overflows);
    Monitor.print(", screen updates ");
    Monitor.print(screenUpdates);
    Monitor.print(", button presses ");
    Monitor.println(presses);
  }
}
