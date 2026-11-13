// effente OLED screen: 128x64, one bit a pixel, drawn from a small text layout.
//
// Plain C++ with no Arduino calls, so the sketch draws with it and the laptop preview
// (device/app/preview/) compiles the very same file. What the preview shows is what the screen draws.
//
// The Linux side sends a layout: up to 8 lines, one widget each, stacked top down in 8-pixel rows
// ("pages"). Text is the 5x7 font, 21 characters a row. Widgets:
//
//   text|right      plain text; the part after "|" is right-aligned            1 row
//   #text|right     inverted bar (a header)                                     1 row
//   !text|right     inverted bar that blinks (an alarm)                         1 row
//   *TEXT           big text, twice the size, centred, 10 characters at most    2 rows
//   =label|value|fill|mark   a meter: label, a bar filled fill % (0-100) with a
//                   tick at mark % (optional), and the value text on the right  1 row
//   %fill|text      a progress bar filled fill %, with text on the right        1 row
//   ~abcd...        a spectrum: one bar per character, 'a' empty to 'x' full    3 rows
//   {bar}           live vibration level bar, log scale 1 to 1000 mg            1 row
//   {spark}         live vibration trace, the last 12.8 s, scaled to its peak    2 rows
//   (empty)         a blank row
//
// In any text, {vib} (mg rms), {peak} (g) and {temp} (C) are filled in from every sample, so they
// move at the screen's own rate, not the Bridge line's. Character 127 is drawn as a sigma.
#pragma once
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <stdio.h>

namespace ui {

static const uint8_t W = 128, PAGES = 8, LINES = 8, LINE_LEN = 48;
static const uint8_t SPECTRUM_H = 24;  // pixels; 'a' + 0..23
static const uint8_t SPARK_H = 16;
static const uint16_t BLINK_MS = 400;

static const uint8_t FONT[96][5] = {  // ASCII 32..126, then 127 = sigma
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
  {0x00,0x00,0x7F,0x00,0x00},{0x00,0x41,0x36,0x08,0x00},{0x08,0x08,0x2A,0x1C,0x08},{0x38,0x44,0x44,0x3C,0x04},
};

static uint8_t fb[PAGES][W];          // the frame: fb[page][x], bit 0 the top pixel of the page
static char tmpl[LINES][LINE_LEN];    // the layout from the Linux side
static float spark[W];                // the trace: mg rms, one every 100 ms, oldest at sparkHead
static uint8_t sparkHead = 0;
static uint32_t layouts = 0;

struct Live {
  float mg;      // vibration, mg rms, last quarter second
  float peakG;   // biggest jolt of the last second, g
  float tempC;   // IMU temperature, C
  uint32_t ms;   // a millisecond clock, for blinking
};

// ------------------------------------------------------------------ layout in
static void setLayout(const char *s) {
  uint8_t line = 0;
  const char *start = s;
  for (const char *p = s;; p++) {
    if (*p == '\n' || *p == 0) {
      if (line < LINES) {
        size_t len = (size_t)(p - start);
        if (len > LINE_LEN - 1) len = LINE_LEN - 1;
        memcpy(tmpl[line], start, len);
        tmpl[line][len] = 0;
        line++;
      }
      start = p + 1;
      if (*p == 0) break;
    }
  }
  while (line < LINES) tmpl[line++][0] = 0;
  layouts++;
}

static void pushSpark(float mg) {  // call every 100 ms
  spark[sparkHead] = (mg == mg && mg > 0) ? mg : 0;
  sparkHead = (uint8_t)((sparkHead + 1) % W);
}

// ------------------------------------------------------------------ drawing
static inline void px(int x, int y) {
  if (x >= 0 && x < W && y >= 0 && y < PAGES * 8) fb[y >> 3][x] |= (uint8_t)(1 << (y & 7));
}
static inline void pxXor(int x, int y) {
  if (x >= 0 && x < W && y >= 0 && y < PAGES * 8) fb[y >> 3][x] ^= (uint8_t)(1 << (y & 7));
}
static void hline(int x0, int x1, int y) { for (int x = x0; x <= x1; x++) px(x, y); }
static void vline(int x, int y0, int y1) { for (int y = y0; y <= y1; y++) px(x, y); }

static const uint8_t *glyphOf(char ch) {
  uint8_t c = (uint8_t)ch;
  if (c < 32 || c > 127) c = '?';
  return FONT[c - 32];
}

static void text(uint8_t page, int x, const char *s, int n) {  // n characters at x
  for (int i = 0; i < n; i++, x += 6) {
    if (x < 0 || x > W - 5) continue;
    const uint8_t *g = glyphOf(s[i]);
    for (int k = 0; k < 5; k++) fb[page][x + k] |= g[k];
  }
}

static void textLR(uint8_t page, const char *s, int xLeft) {  // "left|right"
  const char *bar = strchr(s, '|');
  int nl = bar ? (int)(bar - s) : (int)strlen(s);
  if (nl > 21) nl = 21;
  text(page, xLeft, s, nl);
  if (bar) {
    int nr = (int)strlen(bar + 1);
    text(page, W - nr * 6 + (xLeft ? -1 : 0), bar + 1, nr);
  }
}

static void invert(uint8_t page) { for (int x = 0; x < W; x++) fb[page][x] ^= 0xFF; }

static void big(uint8_t page, const char *s) {  // 2x: 10x14 pixels a character, 12 apart
  int n = (int)strlen(s);
  if (n > 10) n = 10;
  int x0 = (W - (n * 12 - 2)) / 2, y0 = page * 8 + 1;
  for (int i = 0; i < n; i++) {
    const uint8_t *g = glyphOf(s[i]);
    for (int k = 0; k < 5; k++)
      for (int r = 0; r < 7; r++)
        if (g[k] & (1 << r)) {
          int x = x0 + i * 12 + k * 2, y = y0 + r * 2;
          px(x, y); px(x + 1, y); px(x, y + 1); px(x + 1, y + 1);
        }
  }
}

static int field(const char *s, int which, char *out, int cap) {  // the which-th "|" field
  for (int i = 0; i < which && s; i++) { s = strchr(s, '|'); if (s) s++; }
  if (!s) { out[0] = 0; return 0; }
  const char *e = strchr(s, '|');
  int n = e ? (int)(e - s) : (int)strlen(s);
  if (n > cap - 1) n = cap - 1;
  memcpy(out, s, n);
  out[n] = 0;
  return n;
}

static int pct(const char *s) {
  int v = 0;
  for (; *s >= '0' && *s <= '9'; s++) v = v * 10 + (*s - '0');
  return v > 100 ? 100 : v;
}

static void meter(uint8_t page, const char *s) {  // label|value|fill|mark
  char label[16], value[16], f[8], m[8];
  int nl = field(s, 0, label, sizeof label), nv = field(s, 1, value, sizeof value);
  field(s, 2, f, sizeof f);
  bool hasMark = field(s, 3, m, sizeof m) > 0;
  if (nl > 8) nl = 8;
  text(page, 0, label, nl);
  text(page, W - nv * 6, value, nv);
  const int x0 = 50, x1 = 94, y = page * 8;
  hline(x0, x1, y + 1); hline(x0, x1, y + 6); vline(x0, y + 1, y + 6); vline(x1, y + 1, y + 6);
  int w = pct(f) * (x1 - x0 - 1) / 100;
  for (int x = x0 + 1; x <= x0 + w; x++) vline(x, y + 2, y + 5);
  if (hasMark) {
    int xm = x0 + pct(m) * (x1 - x0) / 100;
    px(xm, y); px(xm, y + 7);
    for (int yy = y + 2; yy <= y + 5; yy++) pxXor(xm, yy);
  }
}

static void progress(uint8_t page, const char *s) {  // fill|text
  char f[8], label[24];
  field(s, 0, f, sizeof f);
  int nt = field(s, 1, label, sizeof label);
  const int x0 = 0, x1 = W - 1 - (nt ? nt * 6 + 2 : 0), y = page * 8;
  hline(x0, x1, y + 1); hline(x0, x1, y + 6); vline(x0, y + 1, y + 6); vline(x1, y + 1, y + 6);
  int w = pct(f) * (x1 - x0 - 1) / 100;
  for (int x = x0 + 1; x <= x0 + w; x++) vline(x, y + 2, y + 5);
  text(page, W - nt * 6 + 1, label, nt);
}

static void spectrum(uint8_t page, const char *s) {  // up to 21 bars, 5 wide, 6 apart
  int n = (int)strlen(s);
  if (n > 21) n = 21;
  int x0 = (W - (n * 6 - 1)) / 2, yb = page * 8 + SPECTRUM_H - 1;
  for (int i = 0; i < n; i++) {
    int h = s[i] - 'a';
    if (h < 0) h = 0;
    if (h > SPECTRUM_H - 1) h = SPECTRUM_H - 1;
    for (int k = 0; k < 5; k++) vline(x0 + i * 6 + k, yb - h, yb);
  }
}

static void sparkline(uint8_t page) {
  // The trace, from zero to the highest level in it (at least 1 mg), so a change in the last 12.8 s
  // stands out whatever the machine's level: a crisp top edge over a dotted body.
  float top = 1;
  for (int x = 0; x < W; x++) if (spark[x] > top) top = spark[x];
  int yb = page * 8 + SPARK_H - 1;
  for (int x = 0; x < W; x++) {
    int h = (int)(spark[(sparkHead + x) % W] / top * (SPARK_H - 1) + 0.5f);
    px(x, yb - h);
    for (int y = yb - h + 1; y <= yb; y++) if (((x + y) & 1) == 0) px(x, y);
  }
}

static void levelBar(uint8_t page, float mg) {  // log scale: 1 mg at the left edge, 1000 mg at the right
  float x = mg > 1 ? log10f(mg) / 3.0f : 0;
  uint8_t w = (uint8_t)((x > 1 ? 1 : x) * 127);
  for (uint8_t i = 0; i <= w; i++) fb[page][i] = 0x3C;
  for (uint8_t t = 0; t < W; t += 42) fb[page][t] |= 0x81;  // ticks at 1, 10, 100 mg
}

static char *fnum(char *out, float v, uint8_t dec) {  // out: 16 bytes. By hand: small cores often lack float printf
  if (!(v == v) || v > 1e6f || v < -1e6f) { strcpy(out, "--"); return out; }
  char *p = out;
  if (v < 0) { *p++ = '-'; v = -v; }
  uint32_t scale = 1;
  for (uint8_t i = 0; i < dec; i++) scale *= 10;
  uint32_t n = (uint32_t)(v * scale + 0.5f);
  p += snprintf(p, 12, "%lu", (unsigned long)(n / scale));
  if (dec) snprintf(p, 8, ".%0*lu", (int)dec, (unsigned long)(n % scale));
  return out;
}

static void expand(const char *t, char *line, int cap, const Live &live) {  // fill in {vib} {peak} {temp}
  int n = 0;
  char v[16];
  for (const char *p = t; *p && n < cap - 1;) {
    if (*p == '{') {
      const char *e = strchr(p, '}');
      if (e) {
        if (!strncmp(p, "{vib}", 5)) fnum(v, live.mg, live.mg < 100 ? 1 : 0);
        else if (!strncmp(p, "{peak}", 6)) fnum(v, live.peakG, 3);
        else if (!strncmp(p, "{temp}", 6)) fnum(v, live.tempC, 1);
        else v[0] = 0;
        for (char *q = v; *q && n < cap - 1; q++) line[n++] = *q;
        p = e + 1;
        continue;
      }
    }
    line[n++] = *p++;
  }
  line[n] = 0;
}

// ------------------------------------------------------------------ the frame
static void compose(const Live &live) {
  memset(fb, 0, sizeof fb);
  char line[64];
  uint8_t page = 0;
  for (uint8_t L = 0; L < LINES && page < PAGES; L++) {
    const char *t = tmpl[L];
    if (!strcmp(t, "{bar}")) { levelBar(page, live.mg); page += 1; continue; }
    if (!strcmp(t, "{spark}")) { if (page + 2 <= PAGES) sparkline(page); page += 2; continue; }
    expand(t, line, sizeof line, live);
    switch (line[0]) {
      case '#': textLR(page, line + 1, 2); invert(page); page += 1; break;
      case '!':
        textLR(page, line + 1, 2);
        if ((live.ms / BLINK_MS) % 2 == 0) invert(page);
        page += 1;
        break;
      case '*': if (page + 2 <= PAGES) big(page, line + 1); page += 2; break;
      case '=': meter(page, line + 1); page += 1; break;
      case '%': progress(page, line + 1); page += 1; break;
      case '~': if (page + 3 <= PAGES) spectrum(page, line + 1); page += 3; break;
      default: textLR(page, line, 0); page += 1; break;
    }
  }
}

}  // namespace ui
