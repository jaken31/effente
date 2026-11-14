// The microphone's raw sound, out over WiFi, for a dashboard to draw, play or keep.
//
// The model does not use this: its sound readings go to the UNO Q by the UART wire (esp32_mic.ino). Raw sound is
// far too much for that wire and for the UNO Q's microcontroller behind it, so it leaves here by WiFi only, and
// nothing on the UNO Q depends on it. Needs secrets.h (thermal.h brings WiFi up); without it this file is empty.
//
// The samples are the top 16 of the mic's 24 bits, 16,000 a second, as the ESP32 keeps them (a step of 256
// counts, under the INMP441's own noise), with the mic's small steady offset left in.
//
//   TCP port 8770, two ways, told apart by who speaks first:
//     a program that connects and says nothing gets blocks, 16 ms each (about 33 kB a second):
//       "EFA1" | number of the block's first sample since the ESP32 started (u32) | micros() at its last sample (u32)
//       | rate in Hz (u32) | samples in the block (u16) = 256 | bits (u8) = 16 | 0 (u8) | 256 int16   (little-endian)
//       The sample numbers show a gap if the connection fell behind (then it carries on from the newest sound).
//     a browser or curl that asks "GET /audio.wav" gets an endless WAV stream (16-bit mono), which an <audio>
//       element or a media player plays as it comes: http://<ESP32>:8770/audio.wav
//   HTTP port 8768 (thermal.h's server):
//     GET /audio.json   the newest 1,024 samples (64 ms): {"rate":16000,"bits":16,"n":1024,"first":..,"samples":[...]}
//                       enough to draw the wave; ask again for the next picture
//
// Up to 3 listeners. One that can't keep up is dropped and may connect again.
//
// Compiles for the ESP32-S3; first run on the board 13 Nov (see the commit that added it for what was seen).

#ifndef EFFENTE_AUDIO_WIFI_H
#define EFFENTE_AUDIO_WIFI_H

#if HAVE_THERMAL

const uint16_t AUDIO_PORT = 8770;
const uint32_t AUDIO_BLOCK = 256;
const int AUDIO_CLIENTS = 3;
const uint32_t AUDIO_JSON_N = 1024;

struct AudioClient {
  WiFiClient c;
  bool used = false, wav = false;
  uint32_t next = 0;  // the number of the next sample to send it
};
static WiFiServer auServer(AUDIO_PORT);
static AudioClient auClients[AUDIO_CLIENTS];
static uint32_t auBlocks = 0, auDropped = 0;
static char auJson[AUDIO_JSON_N * 7 + 96];

// Copies `count` samples starting at sample number `first` out of the ring; w and pos are a snapshot of how many
// samples have been written and where the next one goes. The caller makes sure they are still in the ring.
static void auCopy(int16_t *to, uint32_t first, uint32_t count, uint32_t w, uint32_t pos) {
  uint32_t back = w - first;  // how far behind the newest
  uint32_t i = (pos + RING - (back % RING)) % RING;
  for (uint32_t k = 0; k < count; k++) {
    to[k] = ring[i];
    if (++i == RING) i = 0;
  }
}

static const char *audioJsonNow() {  // for GET /audio.json (called from the HTTP server's task)
  static int16_t snap[AUDIO_JSON_N];
  portENTER_CRITICAL(&stampLock);
  uint32_t w = written, pos = ringPos;
  portEXIT_CRITICAL(&stampLock);
  if (w < AUDIO_JSON_N) return "{\"n\":0}";
  auCopy(snap, w - AUDIO_JSON_N, AUDIO_JSON_N, w, pos);
  int n = snprintf(auJson, sizeof auJson, "{\"rate\":%lu,\"bits\":16,\"n\":%lu,\"first\":%lu,\"samples\":[",
                   (unsigned long)RATE_HZ, (unsigned long)AUDIO_JSON_N, (unsigned long)(w - AUDIO_JSON_N));
  for (uint32_t i = 0; i < AUDIO_JSON_N && n < (int)sizeof auJson - 10; i++)
    n += snprintf(auJson + n, sizeof auJson - n, i ? ",%d" : "%d", (int)snap[i]);
  snprintf(auJson + n, sizeof auJson - n, "]}");
  return auJson;
}

static void auAccept(uint32_t w) {
  WiFiClient c = auServer.accept();
  if (!c) return;
  c.setNoDelay(true);
  c.setTimeout(200);
  // a browser or curl speaks first; a program that wants blocks says nothing
  uint32_t t0 = millis();
  while (!c.available() && c.connected() && millis() - t0 < 300) vTaskDelay(pdMS_TO_TICKS(10));
  bool wav = false;
  if (c.available()) {
    String req = c.readStringUntil('\n');
    for (int lines = 0; lines < 40 && c.connected(); lines++) {  // the rest of the request's header
      String line = c.readStringUntil('\n');
      if (line.length() <= 1) break;
    }
    if (!req.startsWith("GET")) {
      c.stop();
      return;
    }
    wav = true;
    c.print("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nCache-Control: no-cache\r\n"
            "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n");
    uint8_t h[44];  // a WAV header for a stream with no end: both sizes at their largest
    uint32_t rate = RATE_HZ, byteRate = RATE_HZ * 2, big = 0xFFFFFFFF, sixteen = 16;
    uint16_t pcm = 1, chans = 1, align = 2, bits = 16;
    memcpy(h, "RIFF", 4);
    memcpy(h + 4, &big, 4);
    memcpy(h + 8, "WAVEfmt ", 8);
    memcpy(h + 16, &sixteen, 4);
    memcpy(h + 20, &pcm, 2);
    memcpy(h + 22, &chans, 2);
    memcpy(h + 24, &rate, 4);
    memcpy(h + 28, &byteRate, 4);
    memcpy(h + 32, &align, 2);
    memcpy(h + 34, &bits, 2);
    memcpy(h + 36, "data", 4);
    memcpy(h + 40, &big, 4);
    c.write(h, sizeof h);
  }
  for (int i = 0; i < AUDIO_CLIENTS; i++) {
    AudioClient &a = auClients[i];
    if (a.used && a.c.connected()) continue;
    a.c.stop();
    a.c = c;
    a.used = true;
    a.wav = wav;
    a.next = w - (w % AUDIO_BLOCK);  // from now on, on a block's edge
    return;
  }
  c.stop();  // full
}

static void audioTask(void *) {
  static uint8_t out[20 + 2 * AUDIO_BLOCK];
  bool serving = false;
  while (true) {
    if (!thIp[0]) {
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }
    if (!serving) {
      auServer.begin();
      serving = true;
    }
    portENTER_CRITICAL(&stampLock);
    uint32_t w = written, pos = ringPos, atUs = writtenAtUs;
    portEXIT_CRITICAL(&stampLock);
    auAccept(w);
    for (int i = 0; i < AUDIO_CLIENTS; i++) {
      AudioClient &a = auClients[i];
      if (!a.used) continue;
      if (!a.c.connected()) {
        a.c.stop();
        a.used = false;
        continue;
      }
      if (w - a.next > RING - 4 * AUDIO_BLOCK) {  // it fell so far behind that the ring has moved on: skip to now
        auDropped += (w - a.next) / AUDIO_BLOCK;
        a.next = w - (w % AUDIO_BLOCK);
      }
      for (int blocks = 0; blocks < 8 && w - a.next >= AUDIO_BLOCK; blocks++) {
        int16_t *samples = (int16_t *)(out + 20);
        auCopy(samples, a.next, AUDIO_BLOCK, w, pos);
        size_t len = 2 * AUDIO_BLOCK;
        const uint8_t *from = out + 20;
        if (!a.wav) {
          uint32_t rate = RATE_HZ, lastUs = atUs - (uint32_t)((uint64_t)(w - (a.next + AUDIO_BLOCK)) * 1000000ULL / RATE_HZ);
          uint16_t count = AUDIO_BLOCK;
          memcpy(out, "EFA1", 4);
          memcpy(out + 4, &a.next, 4);
          memcpy(out + 8, &lastUs, 4);
          memcpy(out + 12, &rate, 4);
          memcpy(out + 16, &count, 2);
          out[18] = 16;
          out[19] = 0;
          from = out;
          len += 20;
        }
        if (a.c.write(from, len) != len) {  // it could not take a block in 200 ms
          a.c.stop();
          a.used = false;
          break;
        }
        a.next += AUDIO_BLOCK;
        auBlocks++;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(8));
  }
}

static void audioBegin() {
  xTaskCreatePinnedToCore(audioTask, "audio", 8192, NULL, 3, NULL, 0);
}

#else
static void audioBegin() {}
#endif

#endif
