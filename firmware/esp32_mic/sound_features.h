// The sound readings, worked out on the microphone's ESP32.
//
// A SECOND COPY of effente/features.py _sound(), in C++, because the raw sound cannot reach the UNO Q's
// Linux side through its header pins (see esp32_mic.ino). The Python is the reference: this must give the
// same readings from the same samples, and device/intake/tests/test_mic_features.py compiles this file on a
// computer and checks that it does. Change one, change the other, and run that test.
//
// What differs from the Python, on purpose:
//   - the window is 16,384 samples (1.024 s), not 16,000, so the spectrum is a plain power-of-two FFT;
//     _sound() on the same 16,384 samples uses the same length (fast_len(16384) = 16384)
//   - the samples are kept as the top 16 of the mic's 24 bits (a step of 256 counts, under the INMP441's own
//     noise) and scaled back, to fit the window in memory; the readings stay in 24-bit counts
//   - the Hann window is applied in the spectrum (the periodic form), not sample by sample (the symmetric
//     form); single precision throughout
//   - snd_env_order is not here: it needs the running speed from vibration, and the Linux side fills it in
//
// No Arduino in this file, so the same code runs in the test.

#ifndef EFFENTE_SOUND_FEATURES_H
#define EFFENTE_SOUND_FEATURES_H

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <algorithm>

#define SND_N 16384          // samples in a window
#define SND_M (SND_N / 2)
#define SND_RATE_HZ 16000.0
#define SND_BANDS 32
#define SND_SENT 45          // readings worked out here: FEATURE_NAMES' 46 "snd_" entries without snd_env_order
#define SND_ENV_N 1024       // envelope samples (the 2-7 kHz band, averaged down to 1 kHz)
#define SND_BEEP_LEN 800     // 50 ms frames for the beeping reading
#define SND_FRAME 160        // 10 ms frames for clicks and the loudest instant
#define SND_FULL_SCALE 8388607.0  // 24-bit

// Where each reading sits in the 45 sent (the order of FEATURE_NAMES, snd_env_order left out).
enum {
  SND_LEVEL_DB = 0, SND_BAND_0 = 1, SND_CENTROID_HZ = 33, SND_CLICK, SND_TONE_HZ, SND_TONE_DB, SND_FLATNESS,
  SND_PEAK_DB, SND_MAINS_DB, SND_TONE_MOD_DB, SND_ENV_PEAK_HZ, SND_ENV_PEAK_DB, SND_KURT_HF, SND_CLIP_FRAC
};

struct SoundFeatures {
  float *work = nullptr;   // SND_N floats: the samples, their spectrum (SND_M complex), then the 2-7 kHz signal
  float *power = nullptr;  // SND_M + 1 floats: the windowed power spectrum
  float *sinq = nullptr;   // SND_N / 4 + 1 floats: sin(2 pi q / SND_N), a quarter turn
  float env[SND_ENV_N];
  float hannEnv[SND_ENV_N];
  float hannBeep[SND_BEEP_LEN];
  float sinBeep[SND_BEEP_LEN];
  float scratch[512];
  int bandLo[SND_BANDS], bandHi[SND_BANDS];

  // Memory needed besides this struct: about 115 kB, in three blocks. False if there isn't enough.
  bool begin() {
    work = (float *)malloc(sizeof(float) * SND_N);
    power = (float *)malloc(sizeof(float) * (SND_M + 1));
    sinq = (float *)malloc(sizeof(float) * (SND_N / 4 + 1));
    if (!work || !power || !sinq) return false;
    const double two_pi = 6.283185307179586;
    for (int q = 0; q <= SND_N / 4; q++) sinq[q] = (float)sin(two_pi * q / SND_N);
    for (int i = 0; i < SND_ENV_N; i++) hannEnv[i] = (float)(0.5 - 0.5 * cos(two_pi * i / (SND_ENV_N - 1)));
    for (int i = 0; i < SND_BEEP_LEN; i++) {
      hannBeep[i] = (float)(0.5 - 0.5 * cos(two_pi * i / (SND_BEEP_LEN - 1)));
      sinBeep[i] = (float)sin(two_pi * i / SND_BEEP_LEN);
    }
    const double df = SND_RATE_HZ / SND_N;
    for (int b = 0; b < SND_BANDS; b++) {  // np.geomspace(50, 8000, 33): a band is lo <= f < hi
      double lo = 50.0 * pow(160.0, (double)b / SND_BANDS), hi = 50.0 * pow(160.0, (double)(b + 1) / SND_BANDS);
      bandLo[b] = (int)ceil(lo / df - 1e-9);
      bandHi[b] = (int)ceil(hi / df - 1e-9) - 1;
      if (bandHi[b] > SND_M) bandHi[b] = SND_M;
      if (bandHi[b] < bandLo[b]) bandLo[b] = bandHi[b] = (int)floor(sqrt(lo * hi) / df + 0.5);  // too narrow: nearest
    }
    return true;
  }

  // cos and sin of 2 pi j / SND_N, from the quarter table.
  inline void cs(uint32_t j, float &c, float &s) const {
    j &= SND_N - 1;
    uint32_t r = j & (SND_N / 4 - 1);
    switch (j / (SND_N / 4)) {
      case 0: s = sinq[r]; c = sinq[SND_N / 4 - r]; break;
      case 1: s = sinq[SND_N / 4 - r]; c = -sinq[r]; break;
      case 2: s = -sinq[r]; c = -sinq[SND_N / 4 - r]; break;
      default: s = -sinq[SND_N / 4 - r]; c = sinq[r]; break;
    }
  }

  // In-place FFT of m complex numbers (re, im pairs); m a power of two up to SND_M. Forward: e^-i.
  void fft(float *z, int m) const {
    for (int i = 1, j = 0; i < m; i++) {
      int bit = m >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) {
        std::swap(z[2 * i], z[2 * j]);
        std::swap(z[2 * i + 1], z[2 * j + 1]);
      }
    }
    for (int len = 2; len <= m; len <<= 1) {
      int half = len >> 1;
      uint32_t stride = SND_N / len;
      for (int k = 0; k < half; k++) {
        float c, s;
        cs(k * stride, c, s);
        for (int i = k; i < m; i += len) {
          float *a = z + 2 * i, *b = z + 2 * (i + half);
          float tr = b[0] * c + b[1] * s, ti = b[1] * c - b[0] * s;  // b * (c - i s)
          b[0] = a[0] - tr;
          b[1] = a[1] - ti;
          a[0] += tr;
          a[1] += ti;
        }
      }
    }
  }

  // Real FFT of n samples in z, in place: afterwards z holds X[0 .. n/2 - 1] as (re, im) pairs; returns X[n/2].
  float rfft(float *z, int n) const {
    int m = n / 2;
    uint32_t stride = SND_N / n;
    fft(z, m);
    float z0r = z[0], z0i = z[1];
    for (int k = 1; k < m / 2; k++) {
      float *a = z + 2 * k, *b = z + 2 * (m - k);
      float er = 0.5f * (a[0] + b[0]), ei = 0.5f * (a[1] - b[1]);  // (a + conj b) / 2
      float dr = 0.5f * (a[0] - b[0]), di = 0.5f * (a[1] + b[1]);  // (a - conj b) / 2
      float c, s;
      cs(k * stride, c, s);
      float tr = dr * c + di * s, ti = di * c - dr * s;  // d * e^(-2 pi i k / n)
      a[0] = er + ti;
      a[1] = ei - tr;
      b[0] = er - ti;
      b[1] = -ei - tr;
    }
    z[2 * (m / 2) + 1] = -z[2 * (m / 2) + 1];
    z[0] = z0r + z0i;
    z[1] = 0.0f;
    return z0r - z0i;
  }

  // The inverse: z holds X[0 .. n/2 - 1], xm is X[n/2]; afterwards z holds the n samples.
  void irfft(float *z, int n, float xm) const {
    int m = n / 2;
    uint32_t stride = SND_N / n;
    float x0 = z[0];
    for (int k = 1; k < m / 2; k++) {
      float *a = z + 2 * k, *b = z + 2 * (m - k);
      float er = 0.5f * (a[0] + b[0]), ei = 0.5f * (a[1] - b[1]);
      float gr = 0.5f * (a[0] - b[0]), gi = 0.5f * (a[1] + b[1]);
      float c, s;
      cs(k * stride, c, s);
      float pr = gr * c - gi * s, pi = gi * c + gr * s;  // g * e^(+2 pi i k / n): the odd samples' spectrum
      a[0] = er - pi;  // e + i o
      a[1] = ei + pr;
      b[0] = er + pi;  // conj(e) + i conj(o)
      b[1] = -ei + pr;
    }
    z[2 * (m / 2) + 1] = -z[2 * (m / 2) + 1];
    z[0] = 0.5f * (x0 + xm);
    z[1] = 0.5f * (x0 - xm);
    for (int i = 0; i < m; i++) z[2 * i + 1] = -z[2 * i + 1];  // inverse = conj(fft(conj)) / m
    fft(z, m);
    float scale = 1.0f / m;
    for (int i = 0; i < m; i++) {
      z[2 * i] *= scale;
      z[2 * i + 1] *= -scale;
    }
  }

  static float median(float *v, int n) {  // reorders v
    int mid = n / 2;
    std::nth_element(v, v + mid, v + n);
    float hi = v[mid];
    if (n & 1) return hi;
    return 0.5f * (hi + *std::max_element(v, v + mid));
  }

  static inline double toDb(double rms) {  // dB SPL: a full-scale sine is 120 (the INMP441's limit)
    return 20.0 * log10(rms / (SND_FULL_SCALE / 1.4142135623730951) + 1e-12) + 120.0;
  }

  // The readings of the SND_N samples ring[start], ring[start + 1], ... (wrapping at ringLen): 16-bit samples,
  // each the top 16 of the mic's 24 bits. out: SND_SENT floats.
  void compute(const int16_t *ring, uint32_t ringLen, uint32_t start, float *out) {
    const double df = SND_RATE_HZ / SND_N;
    const int clipAt = (int)ceil(0.98 * SND_FULL_SCALE / 256.0);

    // ---- the samples themselves: level, clipping, the 10 ms frames
    int64_t sum = 0, sumsq = 0;
    uint32_t clipped = 0;
    static int32_t frameSum[SND_N / SND_FRAME];
    static int64_t frameSq[SND_N / SND_FRAME];
    const int frames = SND_N / SND_FRAME;
    memset(frameSum, 0, sizeof(frameSum));
    memset(frameSq, 0, sizeof(frameSq));
    uint32_t at = start % ringLen;
    for (int i = 0; i < SND_N; i++) {
      int32_t r = ring[at];
      if (++at == ringLen) at = 0;
      sum += r;
      sumsq += (int64_t)r * r;
      if (r >= clipAt || -r >= clipAt) clipped++;
      int f = i / SND_FRAME;
      if (f < frames) {
        frameSum[f] += r;
        frameSq[f] += (int64_t)r * r;
      }
    }
    const double mean = (double)sum / SND_N;  // in 16-bit steps
    double var = (double)sumsq / SND_N - mean * mean;
    if (var < 0) var = 0;
    out[SND_LEVEL_DB] = (float)toDb(sqrt(var) * 256.0 + 1e-9);
    out[SND_CLIP_FRAC] = (float)clipped / SND_N;
    float emax = 0.0f;
    for (int f = 0; f < frames; f++) {
      double e = ((double)frameSq[f] - 2.0 * mean * frameSum[f] + SND_FRAME * mean * mean) / SND_FRAME * 65536.0;
      scratch[f] = (float)(e > 0 ? e : 0);
      if (scratch[f] > emax) emax = scratch[f];
    }
    out[SND_PEAK_DB] = (float)toDb(sqrt((double)emax) + 1e-9);
    out[SND_CLICK] = (float)log10(((double)emax + 1.0) / ((double)median(scratch, frames) + 1.0));

    // ---- the spectrum (unwindowed), and the Hann-windowed power from it
    at = start % ringLen;
    const float meanF = (float)mean;  // single precision from here on: the ESP32 has no double-precision hardware
    for (int i = 0; i < SND_N; i++) {
      work[i] = ((float)ring[at] - meanF) * 256.0f;
      if (++at == ringLen) at = 0;
    }
    const float xm = rfft(work, SND_N);
    {
      // Hann in the spectrum: Xw[k] = X[k] / 2 - (X[k - 1] + X[k + 1]) / 4, with X[-1] = conj X[1], X[M + 1] = conj X[M - 1]
      float pr = work[2], pi = -work[3];  // X[-1]
      float cr = work[0], ci = work[1];   // X[0]
      for (int k = 0; k <= SND_M; k++) {
        float nr, ni;
        if (k + 1 < SND_M) {
          nr = work[2 * (k + 1)];
          ni = work[2 * (k + 1) + 1];
        } else if (k + 1 == SND_M) {
          nr = xm;
          ni = 0.0f;
        } else {
          nr = work[2 * (SND_M - 1)];
          ni = -work[2 * (SND_M - 1) + 1];
        }
        float wr = 0.5f * cr - 0.25f * (pr + nr), wi = 0.5f * ci - 0.25f * (pi + ni);
        power[k] = wr * wr + wi * wi;
        pr = cr;
        pi = ci;
        cr = nr;
        ci = ni;
      }
    }
    for (int b = 0; b < SND_BANDS; b++) {
      double tot = 0;
      for (int k = bandLo[b]; k <= bandHi[b]; k++) tot += power[k];
      out[SND_BAND_0 + b] = (float)log10(tot + 1.0);
    }
    {
      double num = 0, den = 0;  // 50 Hz <= f < 8000 Hz
      for (int k = (int)ceil(50.0 / df - 1e-9); k < SND_M; k++) {
        num += k * df * power[k];
        den += power[k];
      }
      out[SND_CENTROID_HZ] = (float)(num / (den + 1e-12));
    }
    const int toneLo = (int)ceil(100.0 / df - 1e-9), toneHi = (int)floor(7900.0 / df + 1e-9);
    int toneK = toneLo;
    {
      double tot = 0, logs = 0;
      for (int k = toneLo; k <= toneHi; k++) {
        if (power[k] > power[toneK]) toneK = k;
        tot += power[k];
        logs += logf(power[k] + 1e-12f);
      }
      int n = toneHi - toneLo + 1;
      out[SND_FLATNESS] = (float)(exp(logs / n) / (tot / n + 1e-12));
    }
    const float tonePower = power[toneK];
    const double toneHz = toneK * df;
    out[SND_TONE_HZ] = (float)toneHz;
    {
      // Mains hum: 100/150/200 Hz or 120/180/240 Hz, whichever is stronger, over the median of 80-300 Hz.
      int lo = (int)ceil(80.0 / df - 1e-9), hi = (int)floor(300.0 / df + 1e-9), n = hi - lo + 1;
      memcpy(scratch, power + lo, sizeof(float) * n);
      double ref = (double)median(scratch, n) + 1.0, best = -1e300;
      for (int g = 0; g < 2; g++) {
        double base = g ? 60.0 : 50.0, tot = 0;
        for (int mult = 2; mult <= 4; mult++) {
          int a = (int)ceil(base * mult / df - 2.0 - 1e-9), b = (int)ceil(base * mult / df + 2.0 - 1e-9);
          float top = 0.0f;
          for (int k = a; k <= b; k++) top = power[k] > top ? power[k] : top;
          tot += top;
        }
        double hum = 10.0 * log10(tot / 3.0 / ref + 1e-12);
        if (hum > best) best = hum;
      }
      out[SND_MAINS_DB] = (float)best;
    }
    {
      int n = toneHi - toneLo + 1;  // the last use of the power spectrum: the median may reorder it
      double med = median(power + toneLo, n);
      out[SND_TONE_DB] = (float)(10.0 * log10(((double)tonePower + 1.0) / (med + 1.0)));
    }

    // ---- the 2-7 kHz band: its impulsiveness, and the rate its loudness pulses at
    {
      int lo = (int)ceil(2000.0 / df - 1e-9), hi = (int)floor(7000.0 / df + 1e-9);
      for (int k = 0; k < SND_M; k++)
        if (k < lo || k > hi) work[2 * k] = work[2 * k + 1] = 0.0f;
      irfft(work, SND_N, 0.0f);
      const int step = SND_N / SND_ENV_N;  // 16 samples: 1 kHz
      double s2 = 0, s4 = 0, total = 0;
      for (int j = 0; j < SND_ENV_N; j++) {
        float a = 0.0f, q2 = 0.0f, q4 = 0.0f;  // 16 samples at a time in single precision, the totals in double
        for (int i = 0; i < step; i++) {
          float v = work[j * step + i], v2 = v * v;
          a += fabsf(v);
          q2 += v2;
          q4 += v2 * v2;
        }
        s2 += q2;
        s4 += q4;
        env[j] = a / step;
        total += env[j];
      }
      s2 = s2 / SND_N + 1e-24;
      out[SND_KURT_HF] = (float)(s4 / SND_N / (s2 * s2) - 3.0);
      float envMean = (float)(total / SND_ENV_N);
      for (int j = 0; j < SND_ENV_N; j++) env[j] = (env[j] - envMean) * hannEnv[j];
      rfft(env, SND_ENV_N);
      const double dfe = 1000.0 / SND_ENV_N;
      int elo = (int)ceil(2.0 / dfe - 1e-9), ehi = (int)floor(400.0 / dfe + 1e-9), n = ehi - elo + 1;
      int best = elo;
      for (int k = elo; k <= ehi; k++) {
        scratch[k - elo] = sqrtf(env[2 * k] * env[2 * k] + env[2 * k + 1] * env[2 * k + 1]);
        if (scratch[k - elo] > scratch[best - elo]) best = k;
      }
      double top = scratch[best - elo];
      double prom = top / ((double)median(scratch, n) + 1e-12);
      out[SND_ENV_PEAK_HZ] = (float)(best * dfe);
      out[SND_ENV_PEAK_DB] = (float)(10.0 * log10(prom > 1e-12 ? prom : 1e-12));
    }

    // ---- beeping: how much the strongest tone's loudness changes over 50 ms frames
    {
      const double dfb = SND_RATE_HZ / SND_BEEP_LEN;  // 20 Hz
      int kb = (int)ceil(toneHz / dfb - 0.5 - 1e-9);  // the nearest bin; halfway goes down, as np.argmin does
      if (kb < 0) kb = 0;
      int b0 = kb > 0 ? kb - 1 : 0, b1 = kb + 1;
      if (b1 > SND_BEEP_LEN / 2) b1 = SND_BEEP_LEN / 2;
      double tmin = 1e300, tmax = 0;
      at = start % ringLen;
      for (int f = 0; f < SND_N / SND_BEEP_LEN; f++) {
        float re[3] = {0, 0, 0}, im[3] = {0, 0, 0};
        uint32_t ph[3] = {0, 0, 0};
        for (int i = 0; i < SND_BEEP_LEN; i++) {
          float x = ((float)ring[at] - meanF) * 256.0f * hannBeep[i];
          if (++at == ringLen) at = 0;
          for (int b = b0; b <= b1; b++) {
            uint32_t &q = ph[b - b0];
            re[b - b0] += x * sinBeep[(q + SND_BEEP_LEN / 4) % SND_BEEP_LEN];
            im[b - b0] += x * sinBeep[q];
            q += b;
            if (q >= SND_BEEP_LEN) q -= SND_BEEP_LEN;
          }
        }
        double track = 1.0;
        for (int b = b0; b <= b1; b++) track += (double)re[b - b0] * re[b - b0] + (double)im[b - b0] * im[b - b0];
        if (track < tmin) tmin = track;
        if (track > tmax) tmax = track;
      }
      double mod = 10.0 * log10(tmax / tmin);
      out[SND_TONE_MOD_DB] = (float)(mod < 40.0 ? mod : 40.0);
    }
  }
};

#endif
