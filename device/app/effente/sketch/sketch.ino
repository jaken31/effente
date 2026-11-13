// effente device sketch (UNO Q microcontroller): sensors in, screen and button.
//
// - MPU6050 or MPU-6500 (Wire2, 0x68): accelerometer +-8 g, gyroscope +-250 deg/s, 1 kHz,
//   through the sensor's own FIFO so no sample is missed. Every ~25 ms one Bridge notification
//   "imu" carries a batch: (first sample number, micros() when read, samples, accel bytes,
//   gyro bytes, FIFO overflows so far). Accelerometer: every sample (6 bytes each). Gyroscope:
//   every 10th sample (100 a second), since the 115200-baud Bridge line cannot carry both at
//   1 kHz. Big-endian int16 counts, as the sensor gives them.
// - OLED (Wire2, 0x3C; SSD1306 or SSD1309, 128x64): redrawn as fast as the bus allows (full frames,
//   long writes, 1 MHz for the screen only). The Linux side calls "screen" with a layout of widgets
//   (header bars, big text, meters, progress, spectrum, live trace; see oled_ui.h). {vib}, {peak},
//   {temp}, the level bar and the trace are filled in from every sample, so they move at the screen's
//   own rate, not the Bridge line's.
// - Button on D2 to ground (internal pull-up): each press sends a "button" notification.
// - The IMU's temperature sensor (TEMP_OUT) in every batch, as raw counts (deg C = raw / 340 + 36.53).
// - Status once every 5 s on the serial monitor.
// Track 1 owns firmware; this is track 2's sensor sketch until theirs replaces it.

#include <Arduino_RouterBridge.h>
#include <Wire.h>

#include "oled_ui.h"  // the screen's widgets and font, shared with the laptop preview

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
      for (uint16_t i = 0; i < oledChunk && x + i < 128; i++) BUS.write(ui::fb[page][x + i]);
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

// Called from the Linux side with the layout (see oled_ui.h); runs between loop() passes ("safe"),
// so the I2C bus is free.
bool screen(MsgPack::str_t text) {
  ui::setLayout(text.c_str());
  return true;
}

// ------------------------------------------------------------------ main
uint32_t seq = 0, lastRead = 0, lastStatus = 0, batches = 0, presses = 0, notifyUs = 0, lastFrameAt = 0, lastSpark = 0;
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
  ui::setLayout("#effente|starting\n*EFFENTE\n\n{spark}\nvib {vib} mg|pk {peak} g\ntemp {temp} C");
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
    float mg = 1000 * sqrtf(shownRms2);
    if (millis() - lastSpark >= 100) {  // the trace: one column every 100 ms, 12.8 s across
      lastSpark = millis();
      ui::pushSpark(mg);
    }
    ui::Live live = {mg, shownPeak, imuTempC, millis()};
    ui::compose(live);
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
    Monitor.print(ui::layouts);
    Monitor.print(", button presses ");
    Monitor.println(presses);
  }
}
