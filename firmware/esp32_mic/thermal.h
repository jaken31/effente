// The thermal camera, on the microphone's ESP32: an MLX90640 on I2C, its frames out over WiFi.
//
// Why here and not on the UNO Q: its header pins (I2C as much as UART) go to its microcontroller, whose
// line to the Linux side is full (one 115-byte sound set a second already costs accelerometer samples,
// measured 13 Nov), and a frame is 1.5 kB. WiFi reaches the Linux side, and a dashboard, directly.
//
// Wiring: MLX90640 VIN -> 3V3, GND -> GND, SDA -> GPIO 8, SCL -> GPIO 9 (its own bus; nothing shared).
//
// Setup: copy secrets.example.h to secrets.h and fill in the WiFi. Without secrets.h this file compiles to
// nothing and the microphone works as before. The microphone never waits for WiFi or for the camera.
//
// Frames go out as they come (2 a second at the 4 Hz setting: the camera fills a frame in two halves):
//
//   TCP port 8767, raw: connect and frames follow, each
//     "EFT1" | sequence (u32) | millis() at the frame's end (u32) | rows (u8) = 24 | columns (u8) = 32 | 0 (u16)
//     | 768 int16, hundredths of a degree C, row by row          (little-endian, 1,552 bytes)
//     The UNO Q's Linux side reads this (device/intake/thermal_link.py) for the heat readings.
//   HTTP port 8768, for a dashboard:
//     GET /frame.json   the newest frame: {"seq":..,"ms":..,"rows":24,"cols":32,"min":..,"max":..,"c":[768 numbers]}
//     GET /stream       the same JSON for every frame, as server-sent events ("data: {...}"), until closed
//     Both allow any origin, so a page served from elsewhere can read them.
//
// A client that can't keep up misses frames; nobody waits for it. Up to 3 raw and 3 HTTP clients.
//
// NOT RUN YET (13 Nov): compiles for the ESP32-S3 with the Adafruit MLX90640 library, but written without
// the camera or the WiFi's details at hand. Unverified: the pins, the camera's settings, the frame rate,
// and whether WiFi disturbs the microphone's timing.

#ifndef EFFENTE_THERMAL_H
#define EFFENTE_THERMAL_H

#if __has_include("secrets.h")
#include "secrets.h"
#include <WiFi.h>
#include <Wire.h>
#include <Adafruit_MLX90640.h>
#define HAVE_THERMAL 1

const int PIN_TH_SDA = 8, PIN_TH_SCL = 9;
const uint16_t THERMAL_RAW_PORT = 8767, THERMAL_HTTP_PORT = 8768;
const int TH_ROWS = 24, TH_COLS = 32, TH_PIXELS = TH_ROWS * TH_COLS;
const size_t TH_HEADER = 16, TH_FRAME = TH_HEADER + 2 * TH_PIXELS;
const int TH_CLIENTS = 3;

static Adafruit_MLX90640 thCam;
static bool thOk = false;
static float thC[TH_PIXELS];
static uint8_t thFrame[TH_FRAME];
static char thJson[TH_PIXELS * 7 + 160];
static uint32_t thSeq = 0, thFrames = 0, thFailed = 0;
static WiFiServer thRaw(THERMAL_RAW_PORT), thHttp(THERMAL_HTTP_PORT);
static WiFiClient thRawClients[TH_CLIENTS], thSseClients[TH_CLIENTS];
static char thIp[20] = "";  // this board's address once WiFi is up (read by the sender, for the UART and USB lines)

static void thWifiKeep() {
  static uint32_t lastTry = 0;
  static bool wasUp = false, serving = false;
  if (WiFi.status() == WL_CONNECTED) {
    if (!wasUp) {
      wasUp = true;
      strlcpy(thIp, WiFi.localIP().toString().c_str(), sizeof thIp);
      if (!serving) {
        thRaw.begin();
        thHttp.begin();
        serving = true;
      }
    }
    return;
  }
  if (wasUp) {
    wasUp = false;
    thIp[0] = 0;
  }
  if (lastTry == 0 || millis() - lastTry > 20000) {  // the first time, and again if it hasn't joined in 20 s
    lastTry = millis() | 1;
    WiFi.disconnect();
#ifdef WIFI_EAP_USERNAME  // a network with a user name and password each (WPA2-Enterprise, PEAP), such as a campus's
    WiFi.begin(WIFI_SSID, WPA2_AUTH_PEAP, WIFI_EAP_USERNAME, WIFI_EAP_USERNAME, WIFI_PASS);
#else
    WiFi.begin(WIFI_SSID, WIFI_PASS);
#endif
  }
}

static bool thCameraBegin() {
  if (!thCam.begin(MLX90640_I2CADDR_DEFAULT, &Wire)) return false;
  thCam.setMode(MLX90640_CHESS);
  thCam.setResolution(MLX90640_ADC_18BIT);
  thCam.setRefreshRate(MLX90640_4_HZ);  // 2 whole frames a second; faster needs a faster bus than 400 kHz
  return true;
}

static void thPack(uint32_t ms) {
  memcpy(thFrame, "EFT1", 4);
  memcpy(thFrame + 4, &thSeq, 4);
  memcpy(thFrame + 8, &ms, 4);
  thFrame[12] = TH_ROWS;
  thFrame[13] = TH_COLS;
  thFrame[14] = thFrame[15] = 0;
  float lo = 1e9f, hi = -1e9f;
  for (int i = 0; i < TH_PIXELS; i++) {
    float c = thC[i];
    if (!(c > -100.0f && c < 500.0f)) c = -273.15f;  // a pixel the camera's maths could not give: absolute zero says so
    else {
      if (c < lo) lo = c;
      if (c > hi) hi = c;
    }
    int16_t v = (int16_t)lroundf(c * 100.0f);
    memcpy(thFrame + TH_HEADER + 2 * i, &v, 2);
  }
  int n = snprintf(thJson, sizeof thJson, "{\"seq\":%lu,\"ms\":%lu,\"rows\":%d,\"cols\":%d,\"min\":%.1f,\"max\":%.1f,\"c\":[",
                   (unsigned long)thSeq, (unsigned long)ms, TH_ROWS, TH_COLS, lo, hi);
  for (int i = 0; i < TH_PIXELS && n < (int)sizeof thJson - 12; i++) {
    int16_t v;
    memcpy(&v, thFrame + TH_HEADER + 2 * i, 2);
    n += snprintf(thJson + n, sizeof thJson - n, i ? ",%.1f" : "%.1f", v / 100.0f);
  }
  snprintf(thJson + n, sizeof thJson - n, "]}");
}

static void thServe(bool fresh) {
  // new raw clients: frames follow
  WiFiClient c = thRaw.accept();
  if (c) {
    c.setNoDelay(true);
    bool kept = false;
    for (int i = 0; i < TH_CLIENTS && !kept; i++)
      if (!thRawClients[i] || !thRawClients[i].connected()) {
        thRawClients[i].stop();
        thRawClients[i] = c;
        kept = true;
      }
    if (!kept) c.stop();
  }
  // new HTTP clients: one request each
  WiFiClient h = thHttp.accept();
  if (h) {
    h.setTimeout(300);
    String req = h.readStringUntil('\n');
    while (h.connected()) {  // the rest of the request's header, up to its empty line
      String line = h.readStringUntil('\n');
      if (line.length() <= 1) break;
    }
    if (req.startsWith("GET /stream")) {
      h.print("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
              "Access-Control-Allow-Origin: *\r\nConnection: keep-alive\r\n\r\n");
      bool kept = false;
      for (int i = 0; i < TH_CLIENTS && !kept; i++)
        if (!thSseClients[i] || !thSseClients[i].connected()) {
          thSseClients[i].stop();
          thSseClients[i] = h;
          kept = true;
        }
      if (!kept) h.stop();
    } else if (req.startsWith("GET /frame.json") || req.startsWith("GET / ")) {
      const char *body = thFrames ? thJson : "{\"seq\":null}";
      h.printf("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n"
               "Content-Length: %u\r\nConnection: close\r\n\r\n", (unsigned)strlen(body));
      h.print(body);
      h.stop();
    } else {
      h.print("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
      h.stop();
    }
  }
  if (!fresh) return;
  for (int i = 0; i < TH_CLIENTS; i++) {
    WiFiClient &r = thRawClients[i];
    if (r && r.connected()) {
      if (r.availableForWrite() >= (int)TH_FRAME) r.write(thFrame, TH_FRAME);  // behind: it misses this frame
    } else if (r) r.stop();
    WiFiClient &s = thSseClients[i];
    if (s && s.connected()) {
      size_t len = strlen(thJson);
      if (s.availableForWrite() >= (int)len + 16) {
        s.print("data: ");
        s.write((const uint8_t *)thJson, len);
        s.print("\n\n");
      }
    } else if (s) s.stop();
  }
}

// Its own task: WiFi, the camera, and whoever is reading. Nothing here is on the microphone's path.
static void thermalTask(void *) {
  Wire.begin(PIN_TH_SDA, PIN_TH_SCL, 400000);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  uint32_t lastLook = 0;
  while (true) {
    thWifiKeep();
    if (!thOk && (lastLook == 0 || millis() - lastLook > 3000)) {  // no camera yet: look again every 3 s
      lastLook = millis() | 1;
      thOk = thCameraBegin();
    }
    bool fresh = false;
    if (thOk) {
      if (thCam.getFrame(thC) == 0) {  // waits for both halves of a frame: about 0.5 s
        thSeq++;
        thFrames++;
        thPack(millis());
        fresh = true;
      } else if (++thFailed % 20 == 0) {
        thOk = false;  // it stopped answering: start it again
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(100));
    }
    if (thIp[0]) thServe(fresh);
  }
}

static void thermalBegin() {
  xTaskCreatePinnedToCore(thermalTask, "thermal", 16384, NULL, 2, NULL, 0);
}

#else  // no secrets.h: no WiFi, no camera
#define HAVE_THERMAL 0
static void thermalBegin() {}
#endif

#endif
