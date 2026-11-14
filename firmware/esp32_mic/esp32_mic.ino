// effente microphone front end (ESP32-S3-N16R8): INMP441 in over I2S, a short summary out over UART.
//
// Wiring (13 Nov):
//   INMP441 VDD -> 3V3, GND -> GND, L/R -> GND (left channel)
//   INMP441 SCK -> GPIO 5, WS -> GPIO 4, SD -> GPIO 6
//   GPIO 17 (TX) -> UNO Q D0 (RX), GPIO 18 (RX) -> UNO Q D1 (TX, optional), GND -> GND
//
// This is the TEST version: it only shows that the microphone is there and that the UART link works.
// Four times a second it sends one text line on the UART (115200 baud) and the same line on USB:
//   MIC,<n>,<ok>,<level dBFS>,<peak dBFS>,<strongest tone Hz>,<samples read>
// ok = 1 when the samples look like a real microphone (not all the same value). The tone is a rough
// estimate from zero crossings, not the shared pipeline's snd_tone_hz. Nothing here is a feature the
// model uses yet: what the device sends for sound is still to be agreed by the team (unverified).

#include <ESP_I2S.h>
#include <math.h>

const int PIN_SCK = 5, PIN_WS = 4, PIN_SD = 6;
const int PIN_TX = 17, PIN_RX = 18;
const uint32_t RATE_HZ = 16000;
const size_t BLOCK = 4000;  // a quarter of a second

I2SClass I2S;
static int32_t buf[BLOCK];
uint32_t n = 0;
bool i2sOk = false;

void setup() {
  Serial.begin(115200);                              // USB: for a person to read
  Serial.setTxTimeoutMs(0);  // never wait for a USB listener: with nobody reading, each print held the loop
                             // for seconds, and the UNO Q got a line every 4 s, not 4 a second (13 Nov)
  Serial1.begin(115200, SERIAL_8N1, PIN_RX, PIN_TX); // to the UNO Q
  I2S.setPins(PIN_SCK, PIN_WS, -1, PIN_SD);
  i2sOk = I2S.begin(I2S_MODE_STD, RATE_HZ, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT);
}

void loop() {
  size_t got = 0;
  if (i2sOk) got = I2S.readBytes((char *)buf, sizeof buf) / 4;
  else delay(250);
  // The INMP441 gives 24 bits in the top of each 32-bit word.
  double sum = 0, sumSq = 0;
  int32_t lo = INT32_MAX, hi = INT32_MIN;
  for (size_t i = 0; i < got; i++) {
    int32_t v = buf[i] >> 8;
    buf[i] = v;
    sum += v;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  double mean = got ? sum / got : 0, peak = 0;
  uint32_t crossings = 0;
  bool above = false;
  for (size_t i = 0; i < got; i++) {
    double d = buf[i] - mean;
    sumSq += d * d;
    if (fabs(d) > peak) peak = fabs(d);
    bool a = d > 0;
    if (i && a != above) crossings++;
    above = a;
  }
  double rms = got ? sqrt(sumSq / got) : 0;
  const double FULL = 8388608.0;  // 2^23
  double levelDb = rms > 0 ? 20 * log10(rms / FULL) : -200;
  double peakDb = peak > 0 ? 20 * log10(peak / FULL) : -200;
  double toneHz = got ? crossings * (double)RATE_HZ / (2.0 * got) : 0;
  bool ok = got > 0 && hi != lo;  // a missing microphone reads as one unchanging value
  char line[96];
  snprintf(line, sizeof line, "MIC,%lu,%d,%.1f,%.1f,%.0f,%u", (unsigned long)n++, ok ? 1 : 0, levelDb, peakDb,
           toneHz, (unsigned)got);
  Serial1.println(line);
  Serial.println(line);
}
