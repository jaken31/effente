"""The thermal camera on the UNO Q's own bus: its raw readings turned into degrees C.

The MLX90640 hangs on the microcontroller's I2C bus. The sketch reads it and sends what it read, as
it is, over the Bridge (device/app/effente/sketch/sketch.ino, "thermal camera"); the maker's
calibration is done here, because it needs floating-point maths on 768 pixels and the camera's whole
calibration memory.

What the sketch sends (16-bit words, big-endian, as the camera gives them):

  "mlx_ee"  (epoch, first word, words)   the camera's calibration memory (832 words from 0x2400), in
                                         pieces, over and over; `epoch` goes up each time the sketch
                                         has read the memory again (the camera was found again)
  "mlx"     (frame, part, subpage, words)   one half-frame in five parts. The camera measures its
                                         pixels in two halves ("subpages" 0 and 1); a half-frame
                                         carries only the 384 pixels just measured, in pixel order.
              part 0..3   96 pixels each
              part 4      7 words: the camera's memory at 0x0700 (Ta_Vbe), 0x0708 (CP, subpage 0),
                          0x070A (gain), 0x0720 (Ta_PTAT), 0x0728 (CP, subpage 1), 0x072A (VDD), and
                          control register 0x800D (resolution, chess or interleaved)

`Camera` puts the parts together and hands `on_frame` a whole (24, 32) image in degrees C each time a
half-frame comes in, once both halves have been seen. A pixel the camera's own memory marks as broken
or deviating is NaN (UnoQSource.on_thermal_frame gives it the frame's median).

The maths is the maker's: Melexis's MLX90640 driver (MLX90640_API.c, ExtractParameters and
CalculateTo), written again for NumPy by reading that code. UNVERIFIED against it: the two have not
been run side by side on the same inputs. Emissivity is taken as 0.95 and the reflected
temperature as the camera's own temperature minus 8 degrees C, the maker's suggestion for a sensor in
open air: a shiny surface reads wrong (see FAILURE_MODES.md). The maker gives the accuracy as about
+-1.5 degrees C in the middle of the range; nothing here has been checked against a thermometer.
"""

from __future__ import annotations

import numpy as np

ROWS, COLS, PIXELS = 24, 32, 768
EE_WORDS = 832
PART_PIXELS, PIXEL_PARTS, AUX_PART = 96, 4, 4
EMISSIVITY = 0.95
REFLECTED_BELOW_CAMERA_C = 8.0
SCALE_ALPHA = 1e-6


def _signed(v, bits):
    """A `bits`-wide field as a signed number."""
    v = np.asarray(v, np.int64)
    return np.where(v >= 1 << (bits - 1), v - (1 << bits), v)


def _nibbles(words):
    """Each word's four 4-bit parts, lowest first, signed, in one flat list."""
    return _signed(np.array([(int(w) >> (4 * k)) & 0xF for w in words for k in range(4)]), 4)


class Calibration:
    """The camera's own calibration, from its memory (832 words from 0x2400)."""

    def __init__(self, ee):
        ee = np.asarray(ee, np.int64)
        if ee.shape != (EE_WORDS,):
            raise ValueError(f"the calibration memory is {EE_WORDS} words, got {ee.shape}")
        e = [int(w) for w in ee[:64]]
        pix = ee[64:]
        row, col = np.divmod(np.arange(PIXELS), COLS)

        # supply voltage and the camera's own temperature
        self.k_vdd = 32 * int(_signed(e[51] >> 8, 8))
        self.vdd25 = (((e[51] & 0xFF) - 256) << 5) - 8192
        self.kv_ptat = int(_signed(e[50] >> 10, 6)) / 4096.0
        self.kt_ptat = int(_signed(e[50] & 0x3FF, 10)) / 8.0
        self.v_ptat25 = e[49]
        self.alpha_ptat = (e[16] & 0xF000) / 2.0 ** 14 + 8.0
        self.gain = int(_signed(e[48], 16))
        self.tgc = int(_signed(e[60] & 0xFF, 8)) / 32.0
        self.resolution = (e[56] & 0x3000) >> 12
        self.ks_ta = int(_signed(e[60] >> 8, 8)) / 8192.0

        # the object-temperature ranges
        step = ((e[63] & 0x3000) >> 12) * 10
        ct2 = ((e[63] >> 4) & 0xF) * step
        self.ct = np.array([-40.0, 0.0, ct2, ct2 + ((e[63] >> 8) & 0xF) * step, 400.0])
        ks_to_scale = float(1 << ((e[63] & 0xF) + 8))
        self.ks_to = np.array([int(_signed(e[61] & 0xFF, 8)), int(_signed(e[61] >> 8, 8)),
                               int(_signed(e[62] & 0xFF, 8)), int(_signed(e[62] >> 8, 8))]) / ks_to_scale

        # the two compensation pixels
        kta_scale1 = ((e[56] >> 4) & 0xF) + 8
        kta_scale2 = e[56] & 0xF
        kv_scale = (e[56] >> 8) & 0xF
        cp_scale = ((e[32] >> 12) & 0xF) + 27
        off0 = int(_signed(e[58] & 0x3FF, 10))
        self.cp_offset = np.array([off0, off0 + int(_signed(e[58] >> 10, 6))], float)
        a0 = int(_signed(e[57] & 0x3FF, 10)) / 2.0 ** cp_scale
        self.cp_alpha = np.array([a0, (1 + int(_signed(e[57] >> 10, 6)) / 128.0) * a0])
        self.cp_kta = int(_signed(e[59] & 0xFF, 8)) / 2.0 ** kta_scale1
        self.cp_kv = int(_signed(e[59] >> 8, 8)) / 2.0 ** kv_scale

        # every pixel's sensitivity
        acc_rem, acc_col, acc_row = e[32] & 0xF, (e[32] >> 4) & 0xF, (e[32] >> 8) & 0xF
        alpha = (e[33] + (_nibbles(e[34:40])[row] << acc_row) + (_nibbles(e[40:48])[col] << acc_col)
                 + _signed((pix & 0x03F0) >> 4, 6) * (1 << acc_rem)) / 2.0 ** (((e[32] >> 12) & 0xF) + 30)
        self.alpha = alpha - self.tgc * (self.cp_alpha[0] + self.cp_alpha[1]) / 2

        # every pixel's offset
        occ_rem, occ_col, occ_row = e[16] & 0xF, (e[16] >> 4) & 0xF, (e[16] >> 8) & 0xF
        self.offset = (int(_signed(e[17], 16)) + (_nibbles(e[18:24])[row] << occ_row) + (_nibbles(e[24:32])[col] << occ_col)
                       + _signed(pix >> 10, 6) * (1 << occ_rem)).astype(float)

        # how each pixel's offset moves with the camera's temperature (kta) and with the supply (kv):
        # one value for each of the four kinds of pixel (odd or even row, odd or even column)
        kind = 2 * (row % 2) + col % 2
        kta_rc = _signed(np.array([e[54] >> 8, e[55] >> 8, e[54] & 0xFF, e[55] & 0xFF]), 8)
        self.kta = (kta_rc[kind] + _signed((pix & 0x000E) >> 1, 3) * (1 << kta_scale2)) / 2.0 ** kta_scale1
        kv_t = _signed(np.array([(e[52] >> 12) & 0xF, (e[52] >> 4) & 0xF, (e[52] >> 8) & 0xF, e[52] & 0xF]), 4)
        self.kv = kv_t[kind] / 2.0 ** kv_scale

        # chess or interleaved: the reading pattern the camera was calibrated in, and what to add in the other
        self.calibrated_chess = not (e[10] & 0x0800)
        self.il_chess = np.array([int(_signed(e[53] & 0x3F, 6)) / 16.0, int(_signed((e[53] >> 6) & 0x1F, 5)) / 2.0,
                                  int(_signed((e[53] >> 11) & 0x1F, 5)) / 8.0])

        self.broken = np.flatnonzero(pix == 0)  # no calibration at all: the pixel does not work
        self.deviating = np.flatnonzero((pix != 0) & (pix & 1 == 1))  # the maker's own "do not trust" mark
        self.bad = np.zeros(PIXELS, bool)
        self.bad[self.broken] = True
        self.bad[self.deviating] = True

    def supply_v(self, aux) -> float:
        correction = 2.0 ** self.resolution / 2.0 ** ((aux["control"] >> 10) & 3)
        return (correction * aux["vdd"] - self.vdd25) / self.k_vdd + 3.3

    def camera_c(self, aux) -> float:
        """The camera's own temperature, degrees C."""
        ptat = float(aux["ta_ptat"])
        art = ptat / (ptat * self.alpha_ptat + aux["ta_vbe"]) * 2.0 ** 18
        return (art / (1 + self.kv_ptat * (self.supply_v(aux) - 3.3)) - self.v_ptat25) / self.kt_ptat + 25.0

    def to_c(self, raw, aux, subpage: int, emissivity: float = EMISSIVITY, reflected_c=None):
        """Degrees C for the 768 pixels (row by row), from one half-frame.

        `raw` is 768 signed readings (only this subpage's pixels need be right); `aux` the six other
        readings and the control register, signed where the camera's are: ta_vbe, cp0, gain, ta_ptat,
        cp1, vdd, control. Pixels of the other subpage come back NaN."""
        raw = np.asarray(raw, float)
        vdd, ta = self.supply_v(aux), self.camera_c(aux)
        tr = ta - REFLECTED_BELOW_CAMERA_C if reflected_c is None else reflected_c
        ta4, tr4 = (ta + 273.15) ** 4, (tr + 273.15) ** 4
        ta_tr = tr4 - (tr4 - ta4) / emissivity
        corr = np.empty(4)
        corr[0] = 1 / (1 + self.ks_to[0] * 40)
        corr[1] = 1.0
        corr[2] = 1 + self.ks_to[1] * self.ct[2]
        corr[3] = corr[2] * (1 + self.ks_to[2] * (self.ct[3] - self.ct[2]))
        gain = self.gain / float(aux["gain"])
        chess = bool(aux["control"] & 0x1000)
        other_pattern = chess != self.calibrated_chess
        drift = (1 + self.cp_kta * (ta - 25)) * (1 + self.cp_kv * (vdd - 3.3))
        cp = np.array([aux["cp0"] * gain - self.cp_offset[0] * drift,
                       aux["cp1"] * gain - (self.cp_offset[1] + (self.il_chess[0] if other_pattern else 0.0)) * drift])

        p = np.arange(PIXELS)
        il = (p // 32) % 2
        pattern = il ^ (p % 2) if chess else il
        ir = raw * gain - self.offset * (1 + self.kta * (ta - 25)) * (1 + self.kv * (vdd - 3.3))
        if other_pattern:
            conversion = ((p + 2) // 4 - (p + 3) // 4 + (p + 1) // 4 - p // 4) * (1 - 2 * il)
            ir = ir + self.il_chess[2] * (2 * il - 1) - self.il_chess[1] * conversion
        ir = (ir - self.tgc * cp[subpage]) / emissivity
        alpha = self.alpha * (1 + self.ks_ta * (ta - 25))
        with np.errstate(invalid="ignore", divide="ignore"):
            sx = np.sqrt(np.sqrt(alpha ** 3 * (ir + alpha * ta_tr))) * self.ks_to[1]
            to = np.sqrt(np.sqrt(ir / (alpha * (1 - self.ks_to[1] * 273.15) + sx) + ta_tr)) - 273.15
            rng = np.where(to < self.ct[1], 0, np.where(to < self.ct[2], 1, np.where(to < self.ct[3], 2, 3)))
            to = np.sqrt(np.sqrt(ir / (alpha * corr[rng] * (1 + self.ks_to[rng] * (to - self.ct[rng]))) + ta_tr)) - 273.15
        to[pattern != subpage] = np.nan
        return to


def subpage_pixels(subpage: int, chess: bool):
    """Which of the 768 pixels a half-frame carries, in the order the sketch sends them."""
    p = np.arange(PIXELS)
    il = (p // 32) % 2
    return np.flatnonzero((il ^ (p % 2) if chess else il) == subpage)


def _words(data, signed=True):
    raw = bytes(data)
    return np.frombuffer(raw[:len(raw) - len(raw) % 2], ">i2" if signed else ">u2")


class Camera:
    """Takes the sketch's "mlx_ee" and "mlx" notifications; calls `on_frame(image)` with a (24, 32)
    float32 image in degrees C for every half-frame, once the calibration and both halves are in."""

    CAMERA_C = (-40.0, 125.0)  # outside these the numbers are not a camera's: a bad transfer or a bad memory read
    SUPPLY_V = (2.5, 4.0)

    def __init__(self, on_frame):
        self.on_frame = on_frame
        self.cal: Calibration | None = None
        self.stats = {"calibrated": False, "ee_epoch": None, "half_frames": 0, "of_each_half": [0, 0], "images": 0, "incomplete": 0,
                      "refused": 0, "camera_c": None, "supply_v": None, "bad_pixels": None, "error": None}
        self._ee = np.zeros(EE_WORDS, np.int64)
        self._ee_have = np.zeros(EE_WORDS, bool)
        self._frame = None  # the half-frame being collected
        self._parts: dict[int, np.ndarray] = {}
        self._subpage = 0
        self._image = np.full(PIXELS, np.nan, np.float32)
        self._seen = set()

    def on_ee(self, epoch, first, data) -> None:
        epoch, first = int(epoch), int(first)
        words = _words(data, signed=False)
        if first < 0 or first + len(words) > EE_WORDS:
            return
        if epoch != self.stats["ee_epoch"]:  # the sketch read the memory again: start over
            self.stats.update(ee_epoch=epoch, calibrated=False)
            self.cal = None
            self._ee_have[:] = False
            self._image[:] = np.nan
            self._seen.clear()
        if self.cal is not None:
            return
        self._ee[first:first + len(words)] = words
        self._ee_have[first:first + len(words)] = True
        if self._ee_have.all():
            try:
                self.cal = Calibration(self._ee)
                self.stats.update(calibrated=True, bad_pixels=int(self.cal.bad.sum()), error=None)
            except (ValueError, ZeroDivisionError, OverflowError) as e:
                self.stats["error"] = f"calibration memory: {type(e).__name__}: {e}"
                self._ee_have[:] = False  # wait for the next round

    def on_part(self, frame, part, subpage, data) -> None:
        frame, part, subpage = int(frame), int(part), int(subpage)
        if frame != self._frame:
            if self._parts:
                self.stats["incomplete"] += 1  # a part of the last one never came
            self._frame, self._parts, self._subpage = frame, {}, subpage
        self._parts[part] = _words(data)
        if len(self._parts) < PIXEL_PARTS + 1:
            return
        parts, self._parts = self._parts, {}
        self.stats["half_frames"] += 1
        if self.cal is None or subpage not in (0, 1):
            return
        if any(len(parts.get(i, ())) != PART_PIXELS for i in range(PIXEL_PARTS)) or len(parts.get(AUX_PART, ())) != 7:
            self.stats["refused"] += 1
            return
        a = [int(v) for v in parts[AUX_PART]]
        aux = {"ta_vbe": a[0], "cp0": a[1], "gain": a[2], "ta_ptat": a[3], "cp1": a[4], "vdd": a[5], "control": a[6] & 0xFFFF}
        try:
            camera_c, supply_v = self.cal.camera_c(aux), self.cal.supply_v(aux)
        except ZeroDivisionError:
            camera_c = supply_v = float("nan")
        if not (self.CAMERA_C[0] <= camera_c <= self.CAMERA_C[1] and self.SUPPLY_V[0] <= supply_v <= self.SUPPLY_V[1]
                and aux["gain"] != 0):
            self.stats["refused"] += 1
            self.stats["error"] = f"a half-frame's own readings make no sense (camera {camera_c:.1f} C, supply {supply_v:.2f} V)"
            return
        self.stats.update(camera_c=round(camera_c, 2), supply_v=round(supply_v, 3))
        where = subpage_pixels(subpage, bool(aux["control"] & 0x1000))
        raw = np.zeros(PIXELS)
        raw[where] = np.concatenate([parts[i] for i in range(PIXEL_PARTS)])
        self._image[where] = self.cal.to_c(raw, aux, subpage)[where]
        self._seen.add(subpage)
        self.stats["of_each_half"][subpage] += 1
        if len(self._seen) < 2:
            return
        image = self._image.copy()
        image[self.cal.bad] = np.nan
        self.stats["images"] += 1
        self.on_frame(image.reshape(ROWS, COLS))
