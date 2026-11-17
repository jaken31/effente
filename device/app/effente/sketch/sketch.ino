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
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/i2c.h>  // for the thermal camera read (see mlxRead)

TwoWire &BUS = Wire2;
const uint8_t MPU_ADDR = 0x68, OLED_ADDR = 0x3C;  // not "MPU": that name is the chip's memory protection unit
const bool OLED_FLIP = true;   // the picture turned 180 degrees: the screen sits upside down in the case (the user, 14 Nov)
const uint8_t OLED_COL_OFFSET = 0;                 // 2 for an SH1106 controller
const int BUTTON_PIN = 9;  // D9 to GND (the board as wired, 13 Nov)
const uint8_t GYRO_EVERY = 10;     // 1 kHz / 10 = 100 gyro samples a second
const uint16_t MAX_BATCH = 30;     // samples per notification: 180 + 18 bytes, inside the 256-byte RPC buffer
const uint32_t READ_EVERY_US = 25000;
// The Bridge line's speed. The default is 115200, which the sensor's batches fill to 65 %, so anything more
// costs accelerometer samples. A faster line needs the board's router started at the same speed: a drop-in
// for arduino-router.service with --serial-baudrate (device/app/README.md, "A faster Bridge line"). The two
// must match, or nothing arrives. Set to 115200 for a board whose router was not changed.
const unsigned long BRIDGE_BAUD = 460800;

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

// The screen's settings: its size (64 rows), where the picture starts, which way it is drawn, no scrolling,
// no zoom, and on. Sent at the start and again with every full redraw (every 2 s): the picture's bytes are
// sent again and again, but these were sent once, so a write garbled on the bus that the screen took as a
// setting stayed for good. On 14 Nov, after a burst of bus errors (2,280 failed writes), the picture stayed
// the wrong size until the app was started again. Whether a garbled setting was the cause is not proven
// (unverified); sending them again costs about 2 ms every 2 s and nothing flickers (no display-off here).
void oledSettings() {
  const uint8_t set[] = {0xD5, 0xF0,   // clock: fastest internal refresh
                         0xA8, 0x3F,   // 64 rows
                         0xD3, 0x00,   // no vertical shift
                         0x40,         // the picture starts at row 0
                         0x8D, 0x14,   // charge pump on
                         0x20, 0x02,   // page addressing, as oledPage writes
                         // which way up: 0xA1 0xC8 is the module's own way up; 0xA0 0xC0 is turned 180 degrees
                         (uint8_t)(OLED_FLIP ? 0xA0 : 0xA1), (uint8_t)(OLED_FLIP ? 0xC0 : 0xC8),
                         0xDA, 0x12,   // row wiring of a 128 x 64 panel (0x02 here draws every row twice)
                         0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40,  // contrast, pre-charge, VCOMH
                         0x2E,         // no scrolling
                         0xD6, 0x00,   // no zoom (SSD1306; taken as "column 0" by a controller without it)
                         0xA4, 0xA6,   // show the picture, not inverted
                         0xAF};        // on
  for (uint8_t c : set) oledCmds(&c, 1);
}

bool oledBegin() {
  BUS.beginTransmission(OLED_ADDR);
  if (BUS.endTransmission() != 0) return false;
  const uint8_t off = 0xAE;
  oledCmds(&off, 1);
  oledSettings();
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

// ---- microphone: an ESP32-S3 reads the INMP441 and sends a text line 4 times a second on its UART
// (firmware/esp32_mic): "MIC,<n>,<ok>,<level dBFS>,<peak dBFS>,<tone Hz>,<samples>". It arrives on D0, which
// on this core (0.55.2) is Serial1 (usart1); Serial is the Bridge's monitor and Serial2 the Bridge line.
// Screen only for now: nothing of it goes to the Linux side as a feature (still to be agreed by the team).
char micLine[64];
uint8_t micLen = 0;
bool micOk = false;
float micDb = NAN, micToneHz = NAN;
// The tone as the screen shows it: smoothed, TONE_KEEP of what it showed and the rest from each new reading (the user,
// 14 Nov), so the number doesn't jump about. Only the screen: the readings sent on are untouched.
const float TONE_KEEP = 0.8f;  // it was 0.6; smoother still (the user, 14 Nov)
// The six tiles' numbers are replaced all at once, at the moment a layout arrives from the Linux side (10 a
// second: it works the velocity and the speed out that often from the newest second of vibration, and sends a
// layout each time). Those two come in the layout, so only then can all six be new at the same instant (the
// user, 14 Nov: every tile at exactly the same time, and quicker). If no layout comes for TILE_WAIT_MS (the
// Linux side busy or not up yet) they are replaced anyway, so the screen never stands still.
const uint32_t TILE_WAIT_MS = 300;
bool layoutNew = false;
float micToneShown = NAN;
uint32_t micLines = 0, micAt = 0;
char espIp[20] = "";
bool espTell = false;
uint32_t espSaidMs = 0;
uint32_t micFastAt = 0;  // when the last LVL line came: the ESP32's quick level, about 11 a second (its full
                         // firmware only), so the SND tile moves as fast as the IMU's tiles do

float parseNum(const char *&p) {  // a number, then past the comma after it (by hand: no strtod on this core)
  bool neg = *p == '-';
  if (neg) p++;
  float v = 0;
  while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
  if (*p == '.') {
    p++;
    for (float s = 0.1f; *p >= '0' && *p <= '9'; s *= 0.1f) v += (*p++ - '0') * s;
  }
  if (*p == ',') p++;
  return neg ? -v : v;
}

// The ESP32's full firmware also sends the sound readings the model uses, as a binary frame before each text
// line: 0xEF 0x53 | sequence (u16) | age in ms (u16) | count (u8) = 45 | 45 float32 | CRC-16/CCITT-FALSE of
// everything after the two start bytes (u16), little-endian, 189 bytes, sent 8 bytes every 4 ms so the serial
// buffer never has to hold much. A whole frame with a good CRC goes to the Linux side as a notification
// "snd" (see sndSend): (micros() when its last byte came, age, sequence, the 180 bytes of readings);
// device/intake/source.py on_sound takes it from there. 0xEF never occurs in a text line, so the two share the wire. With the test
// firmware (text lines only) nothing here ever fires. Added 13 Nov; not yet run with the full firmware.
const uint8_t SND_COUNT = 45;
const uint16_t SND_PAYLOAD = 4 * SND_COUNT, SND_FRAME = 7 + SND_PAYLOAD + 2;
uint8_t sndBuf[SND_FRAME], sndOut[SND_PAYLOAD];
uint16_t sndLen = 0, sndSeq = 0, sndAge = 0;  // sndLen 0: not inside a frame
uint32_t sndAtUs = 0, sndStartMs = 0, sndFrames = 0, sndBad = 0;
bool sndReady = false;

uint16_t sndCrc(const uint8_t *p, uint16_t n) {
  uint16_t crc = 0xFFFF;
  while (n--) {
    crc ^= (uint16_t)(*p++) << 8;
    for (uint8_t b = 0; b < 8; b++) crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
  }
  return crc;
}

bool sndByte(uint8_t b) {  // true: the byte belonged to a binary frame
  if (sndLen && millis() - sndStartMs > 500) sndLen = 0;  // a frame that stopped half way: back to text
  if (!sndLen) {
    if (b != 0xEF) return false;
    sndBuf[0] = b;
    sndLen = 1;
    sndStartMs = millis();
    return true;
  }
  sndBuf[sndLen++] = b;
  if ((sndLen == 2 && b != 0x53) || (sndLen == 7 && b != SND_COUNT)) {  // not a frame after all
    sndLen = 0;
    sndBad++;
    return true;
  }
  if (sndLen < SND_FRAME) return true;
  sndLen = 0;
  uint16_t crc = (uint16_t)sndBuf[SND_FRAME - 2] | ((uint16_t)sndBuf[SND_FRAME - 1] << 8);
  if (sndCrc(sndBuf + 2, 5 + SND_PAYLOAD) != crc) {
    sndBad++;
    return true;
  }
  sndAtUs = micros();
  sndSeq = (uint16_t)sndBuf[2] | ((uint16_t)sndBuf[3] << 8);
  sndAge = (uint16_t)sndBuf[4] | ((uint16_t)sndBuf[5] << 8);
  memcpy(sndOut, sndBuf + 7, SND_PAYLOAD);
  sndReady = true;  // a newer frame replaces one not sent on yet
  sndFrames++;
  return true;
}

// Every set goes on to the Linux side as it is (180 bytes, about 4 a second: under 1 kB/s). That needs the
// Bridge line at 460800 baud (BRIDGE_BAUD): at the default 115200 the sensor's batches fill the line to 65 %,
// and these sets on top cost about 9 % of the accelerometer's samples (measured on the board, 13 Nov). For a
// board left at 115200, set SND_EVERY_MS to 1000 (one set a second) and FRAME_EVERY to 4 in the ESP32's firmware.
const uint32_t SND_EVERY_MS = 0;
uint32_t sndSentMs = 0;

bool sndSend() {  // called after a read of the sensor's FIFO, when there is the most time before it fills; true: sent
  if (espTell) {  // rare and small (about 25 bytes)
    espTell = false;
    espSaidMs = millis();
    Bridge.notify("esp", MsgPack::str_t(espIp));
    return true;
  }
  if (!sndReady || (SND_EVERY_MS && millis() - sndSentMs < SND_EVERY_MS)) return false;
  sndSentMs = millis();
  sndReady = false;
  MsgPack::bin_t<uint8_t> v;
  v.reserve(SND_PAYLOAD);
  for (uint16_t i = 0; i < SND_PAYLOAD; i++) v.push_back(sndOut[i]);
  Bridge.notify("snd", sndAtUs, (uint32_t)sndAge, (uint32_t)sndSeq, v);  // 180 + about 25 bytes: inside the RPC buffer
  return true;
}

void micPoll() {
  while (Serial1.available()) {
    char c = Serial1.read();
    if (sndByte((uint8_t)c)) {
      micLen = 0;
      continue;
    }
    if (c == '\n') {
      micLine[micLen] = 0;
      micLen = 0;
      if (!strncmp(micLine, "MIC,", 4)) {
        const char *p = micLine + 4;
        parseNum(p);                 // the line's number
        micOk = parseNum(p) > 0.5f;  // the ESP32 saw a real microphone
        float db = parseNum(p);      // over the last second; the LVL lines' level is fresher when they come
        if (!micFastAt || millis() - micFastAt > 500) micDb = db;
        parseNum(p);                 // peak
        micToneHz = parseNum(p);
        micToneShown = (micToneShown == micToneShown && millis() - micAt < 2000)
                           ? TONE_KEEP * micToneShown + (1.0f - TONE_KEEP) * micToneHz : micToneHz;
        micLines++;
        micAt = millis();
      } else if (!strncmp(micLine, "IP,", 3)) {
        // The ESP32's WiFi address (every 5 s, once it has the thermal camera's WiFi up): the Linux side
        // fetches the camera's frames from it. Passed on when it changes, and every 30 s besides.
        if (strlen(micLine + 3) < sizeof espIp && (strcmp(espIp, micLine + 3) || millis() - espSaidMs > 30000)) {
          strcpy(espIp, micLine + 3);
          espTell = true;
        }
      } else if (!strncmp(micLine, "LVL,", 4)) {
        const char *p = micLine + 4;
        float db = parseNum(p);
        // a quarter of the new reading, three quarters of the old (it was half and half; the user, 14 Nov: the
        // decibels a little smoother). Only the screen: the sound readings sent on are untouched.
        micDb = (micFastAt && millis() - micFastAt < 500 && micDb == micDb) ? 0.75f * micDb + 0.25f * db : db;
        micFastAt = millis();
      }
    } else if (c != '\r') {
      if (micLen < 63) micLine[micLen++] = c;
      else micLen = 0;  // too long to be ours: drop it
    }
  }
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

// ---- design B ("tiles", the user's pick, 13 Nov): a header, then boxed readings (four large ones at first;
// six smaller ones since the microphone was added, see composeTiles).
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
  char left[24], val[6][12];
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
  // Six tiles, three across and two down, all the same size (the user, 13 Nov: the IMU's readings smaller,
  // the microphone's about the same size). Top row: vibration, peak, temperature. Bottom row: running
  // speed, sound level, strongest tone. A tile's number is at most 3 characters at double size, so big
  // values change unit: 1000 mg and over is shown in g, 1000 Hz and over in kHz ("k").
  const char *units[6] = {"mg", "", "F", "", "dB", "Hz"};
  float mg = 1000 * sqrtf(shownRms2);
  if (mpuOk) {
    if (mg >= 999.5f) { fnum(val[0], mg / 1000, mg < 9950 ? 1 : 0); units[0] = "g"; }
    else fnum(val[0], mg, 0);
    fnum(val[2], imuTempC * 9.0f / 5.0f + 32.0f, 0);  // the screen shows Fahrenheit (the user); the data stays in C
  } else {
    strcpy(val[0], "--"); strcpy(val[2], "--");
  }
  // The second tile was the peak, which jumped about too much to read (the user, 14 Nov). It is the vibration
  // velocity in mm/s now, the usual measure of how hard a machine shakes (the fixed limit is 11 mm/s). The
  // Linux side writes it (line 5 of the layout), with one decimal under 10; its unit is the tile's label.
  strncpy(val[1], tmpl[4][0] ? tmpl[4] : "0", 11);
  val[1][11] = 0;
  if (strlen(val[1]) > 3) val[1][3] = 0;
  strncpy(val[3], tmpl[3][0] ? tmpl[3] : "0", 11);  // the running speed, as the Linux side wrote it; 0 when none
  val[3][11] = 0;
  if (strlen(val[3]) > 3) {  // too wide for a tile: drop the decimals ("29.8" -> "29")
    char *dot = strchr(val[3], '.');
    if (dot) *dot = 0;
    val[3][3] = 0;
  }
  // The microphone. The ESP32 sends dBFS: decibels below the loudest the microphone can give, so always
  // negative, and nearer 0 when louder. Shown instead as a positive number that goes up with loudness:
  // dBFS + 120, the INMP441 datasheet's figure (94 dB SPL reads -26 dBFS). An estimate, not checked
  // against a sound meter (unverified).
  if (!micLines || millis() - micAt > 2000 || !micOk) {
    strcpy(val[4], "--"); strcpy(val[5], "--");
  } else {
    float db = micDb + 120.0f;
    fnum(val[4], db < 0 ? 0 : db, 0);
    float tone = micToneShown == micToneShown ? micToneShown : micToneHz;
    if (tone >= 999.5f) { fnum(val[5], tone / 1000, tone < 9950 ? 1 : 0); units[5] = "k"; }
    else fnum(val[5], tone, 0);
  }
  static const char *labels[6] = {"VIB", "MM/S", "TEMP", "HZ", "SND", "TONE"};
  // All six numbers change together (see TILE_WAIT_MS). Each is still worked out as before; this only chooses
  // when what is shown is replaced.
  static char shownVal[6][12];
  static const char *shownUnit[6] = {"", "", "", "", "", ""};
  static uint32_t tilesAt = 0;
  if (!tilesAt || layoutNew || millis() - tilesAt >= TILE_WAIT_MS) {
    tilesAt = millis() | 1;
    layoutNew = false;
    for (uint8_t i = 0; i < 6; i++) {
      strncpy(shownVal[i], val[i], 11);
      shownVal[i][11] = 0;
      shownUnit[i] = units[i];
    }
  }
  for (uint8_t i = 0; i < 6; i++) {
    strcpy(val[i], shownVal[i]);
    units[i] = shownUnit[i];
  }
  for (uint8_t i = 0; i < 6; i++) {
    int bx = (i % 3) * 43, by = 8 + (i / 3) * 28;  // 42 wide, 28 high: y 8..35 and 36..63
    outline(bx, by, 42, 28);
    drawText(bx + 3, by + 2, labels[i], 1, true);
    if (units[i][0]) drawText(bx + 40 - textWidth(units[i], 1), by + 2, units[i], 1, true);
    drawText(bx + 21 - textWidth(val[i], 2) / 2, by + 11, val[i], 2, true);
  }
}

// A line starting with '^' is drawn inverted (the header bar); with '#', double size over this line and
// the next (the Linux side leaves the next line empty). A first line starting with '@': design B's tiles.
// ---- thermal camera: an MLX90640 (24 x 32 pixels) on this bus at 0x33 (the user, 14 Nov).
// The picture on the screen is RELATIVE: lighter means warmer within the picture, and no temperature is
// worked out here. Each pixel's reading has its own fixed offset taken off (from the camera's own memory);
// the rest of the maker's calibration (sensitivity, the chip's own temperature, supply voltage) is left out,
// so a number in degrees would be wrong and none is shown.
// The degrees are worked out on the Linux side (device/intake/mlx90640.py) from what goes there as read:
//   "mlx_ee" (epoch, first word, words)     the camera's whole calibration memory, 832 words from 0x2400, a
//                                           piece every half second, round and round (the Linux side may have
//                                           started after this did). epoch: how many times it has been read
//   "mlx" (frame, part, subpage, words)     every half-frame: the 384 pixels the camera has just measured
//                                           (it measures in two halves), in pixel order, in parts 0 to 3 of
//                                           96 pixels; part 4 is the camera's words 0x0700, 0x0708, 0x070A,
//                                           0x0720, 0x0728, 0x072A and its control register 0x800D
// Words are sent high byte first, as the camera gives them. A half-frame is 5 notifications of about 215
// bytes, 4 times a second: about 4.3 kB/s on the Bridge line, which needs it at 460800 baud (BRIDGE_BAUD).
// UNVERIFIED: what this costs in accelerometer samples has not been measured.
// Read in small pieces, one per pass of loop() (at most 32 words, about 2 ms at 400 kHz), so the IMU's
// FIFO is still read in between: a whole frame in one go would hold the bus for about 40 ms.
const uint8_t MLX_ADDR = 0x33;
bool showThermal = false;               // which screen: the readings, or the heat picture. The button swaps them
                                        // (the user, 14 Nov; before that they swapped by themselves every 10 s)
const bool MLX_MIRROR = true;           // true if the picture comes out left-right swapped in the case
bool mlxOk = false, mlxHave = false;
uint8_t mlxState = 0;                   // 0 not found yet, 1 reading its memory, 2 waiting for a frame, 3 reading one
uint16_t mlxWord = 0, mlxChunk = 32;    // words done of the part being read; words per read (halved if refused)
uint8_t mlxFailRun = 0;
uint16_t mlxWord0 = 0;        // the first word of the frame as the camera gave it
uint32_t mlxFrames = 0, mlxErrors = 0, mlxPollAt = 0, mlxTryAt = 0;
uint16_t mlxEeAll[832];                 // the camera's whole calibration memory (0x2400 to 0x273F), as read
uint16_t *const mlxEe = mlxEeAll + 16;  // words 0x10 to 0x1F: the offsets' scales and parts
bool mlxEeCheck = false;                // reading the memory a second time, to compare
uint32_t mlxEpoch = 0;                  // times the memory has been read whole (and read the same twice)
int16_t mlxOffset[768], mlxRaw[768], mlxShown[768];
uint8_t mlxSub = 0;                     // which half the frame being read is (the camera's status, bit 0)
uint16_t mlxCtrl = 0;                   // the camera's control register as set
uint16_t mlxAux[6];                     // 0x0700, 0x0708, 0x070A, 0x0720, 0x0728, 0x072A of the frame being read
// A half-frame on its way to the Linux side: its 384 pixels as the camera gave them, then mlxAux and mlxCtrl.
uint16_t mlxOut[384 + 7];
uint8_t mlxOutSub = 0, mlxOutPart = 5;  // part 5: nothing waiting
uint32_t mlxOutFrame = 0, mlxSent = 0, mlxSkipped = 0, mlxMixed = 0;

// The camera must be asked and answered in ONE transfer (the register's address, then a repeated start, then
// the read). This core's Wire always ends the write with a stop (its endTransmission ignores the argument),
// which the IMU puts up with but the camera does not: it then answered with words that never changed, and the
// picture was nonsense (14 Nov, on the board). So this read goes to Zephyr's own I2C call, on Wire2's device.
static const struct device *const mlxBus = DEVICE_DT_GET(DT_PHANDLE_BY_IDX(DT_PATH(zephyr_user), i2cs, 2));

bool mlxRead(uint16_t reg, uint16_t n, uint16_t *out) {
  uint8_t ask[2] = {(uint8_t)(reg >> 8), (uint8_t)(reg & 0xFF)};
  uint8_t got[64];
  if (n > 32) return false;
  if (i2c_write_read(mlxBus, MLX_ADDR, ask, 2, got, n * 2) != 0) return false;
  for (uint16_t i = 0; i < n; i++) out[i] = ((uint16_t)got[2 * i] << 8) | got[2 * i + 1];
  return true;
}

bool mlxWrite(uint16_t reg, uint16_t v) {
  BUS.beginTransmission(MLX_ADDR);
  BUS.write((uint8_t)(reg >> 8));
  BUS.write((uint8_t)(reg & 0xFF));
  BUS.write((uint8_t)(v >> 8));
  BUS.write((uint8_t)(v & 0xFF));
  return BUS.endTransmission() == 0;
}

void mlxFail() {
  mlxErrors++;
  if (mlxChunk > 8) mlxChunk /= 2;  // the bus refused a long read
  if (++mlxFailRun >= 5) {          // gone: look for it again every 2 s, and show the readings meanwhile
    mlxFailRun = 0;
    mlxState = 0;
    mlxOk = false;
    mlxHave = false;
  }
}

int mlxNibble(uint16_t w, uint8_t k) {  // a signed 4-bit part of a word
  int v = (w >> (4 * k)) & 0xF;
  return v > 7 ? v - 16 : v;
}

void mlxTake();

void mlxPoll() {
  uint32_t now = millis();
  uint16_t buf[32];
  if (mlxState == 0) {
    if (now - mlxTryAt < 2000) return;
    mlxTryAt = now;
    uint16_t ctrl;
    if (!mlxRead(0x800D, 1, &ctrl)) return;       // not there
    mlxCtrl = (ctrl & ~(7u << 7)) | (3u << 7);    // 4 half-frames a second
    mlxWrite(0x800D, mlxCtrl);
    mlxWord = 0;
    mlxEeCheck = false;
    mlxState = 1;
    return;
  }
  if (mlxState == 1) {
    // The camera's whole calibration memory, read twice: the Linux side works the degrees out from it, so
    // one wrong word would make every temperature wrong for as long as this runs. The second read must
    // give the same words; if not, both are done again.
    uint16_t n = 832 - mlxWord < mlxChunk ? 832 - mlxWord : mlxChunk;
    if (!mlxRead(0x2400 + mlxWord, n, buf)) { mlxFail(); return; }
    mlxFailRun = 0;
    if (!mlxEeCheck) {
      memcpy(mlxEeAll + mlxWord, buf, n * 2);
    } else if (memcmp(mlxEeAll + mlxWord, buf, n * 2) != 0) {
      mlxErrors++;
      mlxWord = 0;
      mlxEeCheck = false;
      return;
    }
    mlxWord += n;
    if (mlxWord < 832) return;
    mlxWord = 0;
    if (!mlxEeCheck) { mlxEeCheck = true; return; }
    // each pixel's fixed offset (the datasheet's offset formula), for the picture on the screen
    int remScale = mlxEe[0] & 0xF, colScale = (mlxEe[0] >> 4) & 0xF, rowScale = (mlxEe[0] >> 8) & 0xF;
    int ref = (int16_t)mlxEe[1];
    for (uint16_t p = 0; p < 768; p++) {
      int row = p / 32, col = p % 32;
      int rem = mlxEeAll[64 + p] >> 10;
      if (rem > 31) rem -= 64;
      mlxOffset[p] = (int16_t)(ref + mlxNibble(mlxEe[2 + row / 4], row % 4) * (1 << rowScale)
                               + mlxNibble(mlxEe[8 + col / 4], col % 4) * (1 << colScale) + rem * (1 << remScale));
    }
    mlxEpoch++;
    mlxState = 2;
    mlxOk = true;
    return;
  }
  if (mlxState == 2) {  // a new half-frame ready?
    if (now - mlxPollAt < 20) return;
    mlxPollAt = now;
    uint16_t status;
    if (!mlxRead(0x8000, 1, &status)) { mlxFail(); return; }
    mlxFailRun = 0;
    if (status & 0x0008) {
      mlxWrite(0x8000, 0x0030);  // taken: the camera may measure the next one
      mlxSub = status & 1;
      mlxWord = 0;
      mlxState = 3;
    }
    return;
  }
  if (mlxState == 4 || mlxState == 5) {  // the frame's other readings: 0x0700 to 0x070A, then 0x0720 to 0x072A
    if (!mlxRead(mlxState == 4 ? 0x0700 : 0x0720, 11, buf)) { mlxFail(); return; }
    mlxFailRun = 0;
    if (mlxState == 4) {
      mlxAux[0] = buf[0];
      mlxAux[1] = buf[8];
      mlxAux[2] = buf[10];
      mlxState = 5;
      return;
    }
    mlxAux[3] = buf[0];
    mlxAux[4] = buf[8];
    mlxAux[5] = buf[10];
    // The camera does not wait: if it finished its next half-frame while this one was being read, some of
    // what was read is the next one's. Such a frame still does for the picture, but its numbers are not sent.
    uint16_t status;
    bool whole = mlxRead(0x8000, 1, &status) && !(status & 0x0008);
    mlxTake();
    if (!whole) mlxMixed++;
    else if (mlxOutPart < 5) mlxSkipped++;  // the last one is still going out
    else {
      bool chess = mlxCtrl & 0x1000;
      uint16_t n = 0;
      for (uint16_t p = 0; p < 768 && n < 384; p++) {
        uint8_t row = p / 32, col = p % 32;
        if (((chess ? row + col : row) & 1) == mlxSub) mlxOut[n++] = (uint16_t)(mlxRaw[p] + mlxOffset[p]);  // as read
      }
      memcpy(mlxOut + 384, mlxAux, sizeof mlxAux);
      mlxOut[390] = mlxCtrl;
      mlxOutSub = mlxSub;
      mlxOutFrame++;
      mlxOutPart = 0;
    }
    mlxState = 2;
    return;
  }
  uint16_t k = 768 - mlxWord < mlxChunk ? 768 - mlxWord : mlxChunk;
  if (!mlxRead(0x0400 + mlxWord, k, buf)) { mlxFail(); return; }
  if (mlxWord == 0) mlxWord0 = buf[0];
  for (uint16_t i = 0; i < k; i++) mlxRaw[mlxWord + i] = (int16_t)((int16_t)buf[i] - mlxOffset[mlxWord + i]);
  mlxFailRun = 0;
  mlxWord += k;
  if (mlxWord >= 768) {
    mlxState = 4;
    // A whole frame read with nothing refused: the next size up again. Without this one bad stretch on the
    // bus left the reads at 8 words for good, and the picture at about half its rate (seen 16 Nov).
    if (mlxChunk < 32) mlxChunk *= 2;
  }
}

// One notification for the Linux side, if one is due: the next part of a half-frame, or else, every half
// second, the next piece of the calibration memory. Called where the sound sets go out (after a read of the
// sensor's FIFO), and only when no sound set went out in that pass, so a pass sends one of them at most.
void mlxSend() {
  static uint32_t eeSentMs = 0;
  static uint16_t eeAt = 0;
  uint32_t a = 0;
  const uint16_t *from;
  uint16_t n;
  if (mlxOutPart < 5) {
    from = mlxOut + 96 * mlxOutPart;
    n = mlxOutPart < 4 ? 96 : 7;
  } else if (mlxOk && millis() - eeSentMs >= 500) {
    eeSentMs = millis();
    from = mlxEeAll + eeAt;
    n = 832 - eeAt < 96 ? 832 - eeAt : 96;
    a = eeAt;
    eeAt = eeAt + n >= 832 ? 0 : eeAt + n;
    MsgPack::bin_t<uint8_t> v;
    v.reserve(n * 2);
    for (uint16_t i = 0; i < n; i++) { v.push_back(from[i] >> 8); v.push_back(from[i] & 0xFF); }
    Bridge.notify("mlx_ee", mlxEpoch, a, v);
    return;
  } else {
    return;
  }
  MsgPack::bin_t<uint8_t> v;
  v.reserve(n * 2);
  for (uint16_t i = 0; i < n; i++) { v.push_back(from[i] >> 8); v.push_back(from[i] & 0xFF); }
  Bridge.notify("mlx", mlxOutFrame, (uint32_t)mlxOutPart, (uint32_t)mlxOutSub, v);  // 192 + about 20 bytes
  if (++mlxOutPart == 5) mlxSent++;
}

// A frame has been read whole. Two things before it is shown (14 Nov: the first version's picture
// "didn't really seem to work"):
//  - The camera measures its pixels in two halves, like the two colours of a chessboard, and the halves sit
//    at slightly different levels, which draws a chessboard over everything. The halves are brought to the
//    same average.
//  - Each frame is averaged with the one shown before it, which halves the flicker of the noise.
int mlxLo2 = 0, mlxHi2 = 0;   // the picture's coolest and warmest readings (the heartbeat, for checking)
int mlxRange = 0;  // warmest minus coolest of the picture shown, in raw counts (the heartbeat)

void mlxTake() {
  long sum[2] = {0, 0};
  for (int p = 0; p < 768; p++) sum[((p / 32) + (p % 32)) & 1] += mlxRaw[p];
  int shift = (int)((sum[0] - sum[1]) / 384);
  for (int p = 0; p < 768; p++) {
    int v = mlxRaw[p] + ((((p / 32) + (p % 32)) & 1) ? shift : 0);
    mlxShown[p] = mlxHave ? (int16_t)((mlxShown[p] + v) / 2) : (int16_t)v;
  }
  mlxHave = true;
  mlxFrames++;
}

// The heat picture, over the WHOLE screen (the user, 14 Nov): no header and no words. The camera's 32 x 24
// pixels are stretched to 128 x 64 and smoothed, and drawn in dots: the more dots, the warmer. The stretch
// is 4 times across and 2.67 times down, so things look about 1.5 times wider than they are; every pixel of
// the camera is on the screen. The scale runs from the coolest to the warmest reading in view (leaving out
// the single most extreme of each), so the picture is relative: it says where it is warmer, not how warm.
uint8_t mlxFb[8][128];
uint32_t mlxDrawn = 0;  // the frame number mlxFb was drawn from

void composeThermal() {
  if (!mlxHave) {
    memset(fb, 0, sizeof fb);
    drawText(22, 20, "HEAT CAMERA", 1, true);
    drawText(43, 34, "WAITING", 1, true);
    return;
  }
  if (mlxDrawn != mlxFrames) {  // worked out once per camera frame, not once per screen frame
    mlxDrawn = mlxFrames;
    int lo = 32767, lo2 = 32767, hi = -32768, hi2 = -32768;
    for (int i = 0; i < 768; i++) {
      int v = mlxShown[i];
      if (v < lo) { lo2 = lo; lo = v; } else if (v < lo2) lo2 = v;
      if (v > hi) { hi2 = hi; hi = v; } else if (v > hi2) hi2 = v;
    }
    mlxRange = hi2 - lo2;
    mlxLo2 = lo2;
    mlxHi2 = hi2;
    int span = mlxRange < 16 ? 16 : mlxRange;  // a view all at one temperature: not noise blown up to full scale
    static const uint8_t bayer[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};
    uint32_t perCount = ((uint32_t)17 << 16) / (16u * (uint32_t)span);
    memset(mlxFb, 0, sizeof mlxFb);
    for (int oy = 0; oy < 64; oy++) {
      int syf = (2 * oy + 1) * 48 - 128;  // the camera row this screen row falls on, in 256ths
      if (syf < 0) syf = 0;
      int r0 = syf >> 8, fy = syf & 255, r1 = r0 + 1;
      if (r1 > 23) r1 = 23;
      for (int ox = 0; ox < 128; ox++) {
        int sxf = (2 * ox + 1) * 32 - 128;
        if (sxf < 0) sxf = 0;
        int c0 = sxf >> 8, fx = sxf & 255, c1 = c0 + 1;
        if (c1 > 31) c1 = 31;
        int a0 = MLX_MIRROR ? 31 - c0 : c0, a1 = MLX_MIRROR ? 31 - c1 : c1;
        // 32-bit sums only, and a multiply where a divide was: with 64-bit divides the whole picture took
        // long enough that the IMU lost samples while this screen was up (14 Nov: 900 a second, overflows).
        int32_t top = ((int32_t)mlxShown[r0 * 32 + a0] * (256 - fx) + (int32_t)mlxShown[r0 * 32 + a1] * fx) >> 4;
        int32_t bot = ((int32_t)mlxShown[r1 * 32 + a0] * (256 - fx) + (int32_t)mlxShown[r1 * 32 + a1] * fx) >> 4;
        int32_t d = ((top * (256 - fy) + bot * fy) >> 8) - 16 * lo2;       // above the coolest, times 16
        if (d < 0) d = 0;
        if (d > 16 * span) d = 16 * span;
        int32_t level = (int32_t)(((uint32_t)d * perCount) >> 16);         // 0 (coolest) to 16 (warmest)
        if (level > bayer[oy & 3][ox & 3]) mlxFb[oy >> 3][ox] |= (uint8_t)(1 << (oy & 7));
      }
    }
  }
  memcpy(fb, mlxFb, sizeof fb);
}

void compose() {
  if (mlxOk && showThermal) {
    composeThermal();
    return;
  }
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
extern volatile bool slipTest;
bool screen(MsgPack::str_t text) {
  const char *s = text.c_str();
  if (s[0] == 1 && strncmp(s + 1, "SLIP", 4) == 0) {  // not a layout: the out-of-step test
    slipTest = true;
    return true;
  }
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
  layoutNew = true;  // the beat the tiles change on
  return true;
}

// ------------------------------------------------------------------ main
uint32_t seq = 0, lastRead = 0, lastStatus = 0, batches = 0, presses = 0, notifyUs = 0, lastFrameAt = 0;
bool buttonDown = false, buttonHeld = false;
const uint32_t BUTTON_HOLD_MS = 1000;
uint32_t buttonChange = 0;
uint32_t lastSampleMs = 0;  // when the FIFO last gave a sample
uint32_t sensorRestarts = 0;
// Samples out of step: the FIFO gives 12 bytes a sample, and if a byte is ever lost or doubled on the bus every
// later sample is put together from the wrong bytes, for good. Seen on the board (14 Nov, 03:37): at the
// moment the ESP32 lost its power the readings turned into a steady 9 g with clipping, and stayed so for 13
// minutes until the app was restarted; the sensor itself was fine. Two signs, either one restarts the FIFO:
//   - the FIFO's count is not a whole number of samples on STEP_ODD_READS reads in a row;
//   - gravity (the half-second average of the three axes) is outside STEP_MIN_G..STEP_MAX_G for STEP_BAD_MS.
//     A fixed device always shows about 1 g there, however hard it vibrates.
// Counted in outOfStep (the heartbeat) and as a FIFO restart.
const float STEP_MIN_G = 0.4f, STEP_MAX_G = 2.5f;
const uint32_t STEP_BAD_MS = 1000;
const uint8_t STEP_ODD_READS = 4;
uint32_t outOfStep = 0, gravBadSince = 0, oddCounts = 0;
uint8_t oddRun = 0;
volatile bool slipTest = false;  // a test from the Linux side: lose 5 bytes on purpose (the screen text "\x01SLIP")

void setup() {
  Bridge.begin(BRIDGE_BAUD);
  Monitor.begin();
  Serial1.begin(115200);  // D0 (RX): the ESP32 microphone front end
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

void outOfStepRestart() {
  outOfStep++;
  overflows++;  // reported like an overflow: samples were lost and the FIFO restarted
  fifoReset();
  gravInit = false;
  gravBadSince = 0;
  oddRun = 0;
}

void readFifo() {
  if (slipTest) {  // the test: take 5 bytes out, so every sample after is out of step
    uint8_t junk[5];
    mpuRead(0x74, junk, 5);
    slipTest = false;
  }
  uint16_t avail = fifoCount();
  if (avail >= fifoBytes - 24) {  // nearly full: data was lost (a full FIFO overwrites bytes); start clean, say so
    overflows++;
    fifoReset();
    return;
  }
  if (avail % 12 != 0) {
    oddCounts++;
    if (++oddRun >= STEP_ODD_READS) { outOfStepRestart(); return; }
  } else {
    oddRun = 0;
  }
  float g2 = grav[0] * grav[0] + grav[1] * grav[1] + grav[2] * grav[2];
  if (gravInit && (g2 > STEP_MAX_G * STEP_MAX_G || g2 < STEP_MIN_G * STEP_MIN_G)) {
    if (!gravBadSince) gravBadSince = millis() | 1;
    else if (millis() - gravBadSince > STEP_BAD_MS) { outOfStepRestart(); return; }
  } else {
    gravBadSince = 0;
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
      micPoll();  // the ESP32's bytes, taken as they come (a batch of reads takes about 10 ms)
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
  micPoll();
  mlxPoll();  // the thermal camera, a small piece at a time
  if (!mpuOk) {  // with the sensor running, these go out after its reads (below)
    static uint32_t idleSendAt = 0;
    if (!sndSend() && millis() - idleSendAt >= 25) {
      idleSendAt = millis();
      mlxSend();
    }
  }
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
    if (!sndSend()) mlxSend();
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
      BUS.setClock(OLED_I2C_HZ);
      oledSettings();  // and the screen's settings with it: see oledSettings
      BUS.setClock(MPU_I2C_HZ);
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
                  (uint32_t)frames, (uint32_t)micLines, (int32_t)(micDb == micDb ? micDb * 10 : 0),
                  (uint32_t)outOfStep, (uint32_t)oddCounts, (uint32_t)mlxFrames, (uint32_t)mlxErrors, (uint32_t)(mlxRange < 0 ? 0 : mlxRange),
                  (int32_t)mlxLo2, (int32_t)mlxHi2, (int32_t)mlxWord0, (int32_t)mlxRaw[0], (int32_t)mlxRaw[400],
                  (int32_t)mlxOffset[0], (int32_t)mlxOffset[400], (int32_t)mlxEe[0], (int32_t)mlxEe[1],
                  (int32_t)mlxShown[1], (int32_t)mlxShown[401], (int32_t)mlxChunk, (int32_t)showThermal,
                  (uint32_t)mlxSent, (uint32_t)mlxSkipped, (uint32_t)mlxMixed, mlxEpoch);
  }
  bool down = digitalRead(BUTTON_PIN) == LOW;
  // Held for a second: what a press used to be (start or stop learning), sent once per hold. A short press
  // only swaps the screen, so swapping screens never starts a recording.
  if (buttonDown && !buttonHeld && millis() - buttonChange >= BUTTON_HOLD_MS) {
    buttonHeld = true;
    Bridge.notify("button", ++presses);
  }
  if (down != buttonDown && millis() - buttonChange > 30) {  // 30 ms debounce
    buttonDown = down;
    buttonChange = millis();
    if (down) {
      buttonHeld = false;
    } else if (!buttonHeld) {
      showThermal = !showThermal;  // a short press: the other screen (the user, 14 Nov)
    }
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
