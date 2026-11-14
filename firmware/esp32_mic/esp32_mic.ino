// effente microphone front end (ESP32-S3-N16R8): INMP441 in over I2S, sound readings out over UART.
//
// Wiring (13 Nov):
//   INMP441 VDD -> 3V3, GND -> GND, L/R -> GND (left channel)
//   INMP441 SCK -> GPIO 5, WS -> GPIO 4, SD -> GPIO 6
//   GPIO 17 (TX) -> UNO Q D0 (RX), GPIO 18 (RX) -> UNO Q D1 (TX, optional), GND -> GND
//
// It starts by itself: from power-on it samples and sends, with nothing to press and nothing to wait for.
// The first readings go out about 1.2 s after power (one full window of sound).
//
// The UNO Q's header pins go to its microcontroller, whose line to the Linux side cannot carry raw sound.
// So this board does the sound maths itself. About four times a second (every 4,096 samples) it works out
// the sound readings of the last 1.024 s (sound_features.h, a copy of effente/features.py _sound()) and sends
// on the UART (115200 baud), one after the other:
//
//   1. A binary frame for the model, little-endian, 189 bytes:
//        0xEF 0x53 | sequence (u16) | age (u16): ms from the window's last sample to the start of sending
//        | count (u8) = 45 | 45 float32, the "snd_" readings in FEATURE_NAMES order without snd_env_order
//        | CRC-16/CCITT-FALSE over everything after the two start bytes (u16)
//      It goes out 8 bytes every 4 ms (about 95 ms in all): the UNO Q's sketch is busy for up to about 20 ms
//      at a time, and its serial buffer must hold what arrives meanwhile.
//   2. The text line the screen uses (as the test version sent, so the sketch on master still shows it):
//        MIC,<n>,<ok>,<level dBFS>,<peak dBFS>,<strongest tone Hz>,<samples>
//      The level and the tone are now the readings' own (snd_level_db - 120, snd_tone_hz), not the test
//      version's zero-crossing estimate. ok = 1 when the samples are not all one value.
//
// The same text line goes to USB, with how long one set of readings took to work out.
//
// The sound maths is checked against the Python on a computer (device/intake/tests/test_mic_features.py), and
// this sketch compiles for the ESP32-S3 (arduino-esp32 3.3.12). IT HAS NOT RUN ON THE BOARD YET (13 Nov):
// unverified there are the time one set takes (it must stay under 256 ms; the USB line prints it), and that
// the UNO Q's sketch receives the paced frames whole. The I2S setup and the pins are the test version's,
// which did run on the hardware.

#include <ESP_I2S.h>
#include <math.h>
#include "sound_features.h"

const int PIN_SCK = 5, PIN_WS = 4, PIN_SD = 6;
const int PIN_TX = 17, PIN_RX = 18;
const uint32_t RATE_HZ = 16000;
const size_t READ = 256;               // samples per I2S read: 16 ms
const uint32_t HOP = 4096;             // new samples between two sets of readings: 0.256 s
const uint32_t RING = SND_N + 2 * HOP; // samples kept: one window, and room to keep listening while it is worked on
const size_t FRAME = 2 + 2 + 2 + 1 + 4 * SND_SENT + 2;

I2SClass I2S;
bool i2sOk = false;

static int16_t *ring = nullptr;            // the newest RING samples, the top 16 of the mic's 24 bits
static volatile uint32_t written = 0;      // samples put in the ring since start
static volatile uint32_t ringPos = 0;      // where the next sample goes
static volatile uint32_t writtenAtUs = 0;  // micros() just after the newest of them was read
static volatile int32_t peak24 = 0;        // largest |sample - offset| since the last set, in 24-bit counts
static volatile bool alive = false;        // the samples are not all one value
static portMUX_TYPE stampLock = portMUX_INITIALIZER_UNLOCKED;

static SoundFeatures sound;

struct Outgoing {  // one set, ready for the UART
  uint8_t frame[FRAME];
  char line[96];
  uint32_t windowEndUs;
};
static QueueHandle_t outbox;
static uint32_t n = 0, skipped = 0;

// ---------------------------------------------------------------- the microphone
static void captureTask(void *) {
  static int32_t buf[READ];
  uint32_t w = 0, pos = 0;
  int32_t dc = 0;  // the mic's steady offset, followed slowly, so the peak is of the sound alone
  while (true) {
    size_t got = i2sOk ? I2S.readBytes((char *)buf, sizeof buf) / 4 : 0;
    uint32_t t_us = micros();
    if (got == 0) {
      delay(10);
      continue;
    }
    int32_t lo = INT32_MAX, hi = INT32_MIN, top = 0;
    for (size_t i = 0; i < got; i++) {
      int32_t v = buf[i] >> 8;  // the INMP441 gives 24 bits in the top of each 32-bit word
      if (v < lo) lo = v;
      if (v > hi) hi = v;
      dc += (v - dc) >> 10;
      int32_t a = v - dc;
      if (a < 0) a = -a;
      if (a > top) top = a;
      int32_t s = (v + 128) >> 8;  // the top 16 bits, rounded
      ring[pos] = (int16_t)(s > 32767 ? 32767 : s);
      if (++pos == RING) pos = 0;
    }
    w += got;
    if (top > peak24) peak24 = top;
    alive = hi != lo;  // a missing microphone reads as one unchanging value
    portENTER_CRITICAL(&stampLock);
    written = w;
    ringPos = pos;
    writtenAtUs = t_us;
    portEXIT_CRITICAL(&stampLock);
  }
}

// ---------------------------------------------------------------- sending
static uint16_t crc16(const uint8_t *p, size_t len) {  // CRC-16/CCITT-FALSE
  uint16_t crc = 0xFFFF;
  while (len--) {
    crc ^= (uint16_t)(*p++) << 8;
    for (int b = 0; b < 8; b++) crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
  }
  return crc;
}

// Sends each set: the frame a little at a time, then the text line. Its own task, so the maths never waits for the wire.
static void sendTask(void *) {
  static Outgoing o;
  uint16_t seq = 0;
  while (true) {
    if (xQueueReceive(outbox, &o, portMAX_DELAY) != pdTRUE) continue;
    uint32_t age = (micros() - o.windowEndUs) / 1000;
    if (age > 65535) age = 65535;
    o.frame[0] = 0xEF;
    o.frame[1] = 0x53;
    o.frame[2] = seq & 0xFF;
    o.frame[3] = seq >> 8;
    o.frame[4] = age & 0xFF;
    o.frame[5] = age >> 8;
    o.frame[6] = SND_SENT;
    uint16_t crc = crc16(o.frame + 2, 5 + 4 * SND_SENT);
    o.frame[FRAME - 2] = crc & 0xFF;
    o.frame[FRAME - 1] = crc >> 8;
    seq++;
    for (size_t i = 0; i < FRAME; i += 8) {
      Serial1.write(o.frame + i, FRAME - i < 8 ? FRAME - i : 8);
      vTaskDelay(pdMS_TO_TICKS(4));
    }
    Serial1.println(o.line);
  }
}

// ---------------------------------------------------------------- start and loop
void setup() {
  Serial.begin(115200);  // USB: for a person to read
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);  // never wait for a USB listener: with nobody reading, each print held the loop
                             // for seconds, and the UNO Q got a line every 4 s, not 4 a second (13 Nov)
#endif
  Serial1.begin(115200, SERIAL_8N1, PIN_RX, PIN_TX);  // to the UNO Q
  ring = (int16_t *)calloc(RING, sizeof(int16_t));
  outbox = xQueueCreate(2, sizeof(Outgoing));
  if (ring == nullptr || outbox == NULL || !sound.begin()) {
    while (true) {  // say so for ever: nothing else can work
      Serial.println("not enough memory for the sound window and its spectrum");
      Serial1.println("MIC,0,0,-200.0,-200.0,0,0");
      delay(1000);
    }
  }
  I2S.setPins(PIN_SCK, PIN_WS, -1, PIN_SD);
  i2sOk = I2S.begin(I2S_MODE_STD, RATE_HZ, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT);
  xTaskCreatePinnedToCore(captureTask, "mic", 4096, NULL, 5, NULL, 0);
  xTaskCreatePinnedToCore(sendTask, "send", 4096, NULL, 4, NULL, 0);
}

void loop() {
  static uint32_t doneAt = 0, lastWorkMs = 0, lastSaid = 0;
  static float readings[SND_SENT];
  static Outgoing o;
  portENTER_CRITICAL(&stampLock);
  uint32_t w = written, pos = ringPos, atUs = writtenAtUs;
  portEXIT_CRITICAL(&stampLock);
  if (w < SND_N || w - doneAt < HOP) {  // no full window yet, or nothing new enough
    if (!i2sOk && millis() - lastSaid > 250) {  // no microphone driver: keep saying so, as the test version did
      lastSaid = millis();
      Serial1.println("MIC,0,0,-200.0,-200.0,0,0");
      Serial.println("MIC,0,0,-200.0,-200.0,0,0 (I2S did not start)");
    }
    delay(2);
    return;
  }
  if (doneAt && w - doneAt >= 2 * HOP) skipped += (w - doneAt) / HOP - 1;  // the maths fell behind: use the newest
  doneAt = w;
  uint32_t t0 = millis();
  sound.compute(ring, RING, (pos + RING - SND_N) % RING, readings);  // the capture task writes ahead of this window
  lastWorkMs = millis() - t0;

  int32_t p = peak24;
  peak24 = 0;
  double peakDb = p > 0 ? 20 * log10(p / 8388608.0) : -200;
  memcpy(o.frame + 7, readings, 4 * SND_SENT);
  snprintf(o.line, sizeof o.line, "MIC,%lu,%d,%.1f,%.1f,%.0f,%u", (unsigned long)n++, alive ? 1 : 0,
           readings[SND_LEVEL_DB] - 120.0f, peakDb, readings[SND_TONE_HZ], (unsigned)SND_N);
  o.windowEndUs = atUs;
  if (xQueueSend(outbox, &o, 0) != pdTRUE) skipped++;  // the wire is behind: drop this set, never wait
  Serial.printf("%s | one set took %lu ms, %lu skipped\n", o.line, (unsigned long)lastWorkMs, (unsigned long)skipped);
}
