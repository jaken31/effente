"""What the ESP32 sends over WiFi: the thermal camera's frames, and the microphone's sound readings.

Nothing big fits through the UNO Q's microcontroller (its line to the Linux side is full: even one small
sound set a second cost accelerometer samples, measured 13 Nov), so the ESP32 that carries the microphone
and the camera (firmware/esp32_mic) serves both over WiFi, and the Linux side connects to it here. The
ESP32's address is whatever `host()` returns at the time (it tells the sketch its address over the UART;
main.py keeps the latest); None means not known yet. When the ESP32 goes away a link keeps trying, and the
pipeline simply has no camera heat, or falls back to the sound sets that come by UART, meanwhile.

  ThermalLink   TCP port 8767: frames follow on connect, each
                  "EFT1" | sequence (uint32) | the ESP32's millis() at the frame's end (uint32) | rows (uint8) = 24
                  | columns (uint8) = 32 | 0 (uint16) | 768 int16, hundredths of a degree C, row by row
                `on_frame(frame_c)`: a (24, 32) float32 array in degrees C
  SoundLink     TCP port 8769: every set of sound readings follows on connect (about 4 a second), each the
                same 189-byte frame the UART carries:
                  0xEF 0x53 | sequence (uint16) | age (uint16): ms from the window's last sample to sending
                  | count (uint8) = 45 | 45 float32 | CRC-16/CCITT-FALSE of everything after the start bytes
                `on_set(age_ms, seq, payload)`: payload is the 180 bytes of readings

All little-endian. Tested against a fake ESP32 (tests/test_thermal_link.py); the camera has not been
connected yet (13 Nov).
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import numpy as np

PORT = 8767
MAGIC = b"EFT1"
HEADER = struct.Struct("<4sIIBBH")
ROWS, COLS = 24, 32
SOUND_PORT = 8769
SOUND_MAGIC = b"\xEF\x53"
SOUND_COUNT = 45
SOUND_FRAME = 7 + 4 * SOUND_COUNT + 2
SILENT_S = 5.0  # nothing for this long: connect again


def pack_frame(seq: int, ms: int, frame_c) -> bytes:
    """One thermal frame as the ESP32 sends it (for tests)."""
    v = np.round(np.asarray(frame_c, float) * 100.0).astype("<i2")
    return HEADER.pack(MAGIC, seq & 0xFFFFFFFF, ms & 0xFFFFFFFF, ROWS, COLS, 0) + v.tobytes()


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE, as the ESP32 and the sketch work it out."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def pack_sound(seq: int, age_ms: int, readings) -> bytes:
    """One set of sound readings as the ESP32 sends it (for tests)."""
    body = struct.pack("<HHB", seq & 0xFFFF, min(int(age_ms), 65535), SOUND_COUNT) + np.asarray(readings, "<f4").tobytes()
    return SOUND_MAGIC + body + struct.pack("<H", crc16(body))


class _EspLink:
    """A thread that connects to the ESP32, reads, and hands what `_take(buf)` finds to the callback."""

    name, port = "esp", 0

    def __init__(self, host, port: int | None = None):
        self.host = host
        if port is not None:
            self.port = port
        self.stats = {"connected": False, "peer": None, "connections": 0, "frames": 0, "skipped_bytes": 0, "error": None}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _host(self):
        return self.host() if callable(self.host) else self.host

    def _run(self) -> None:
        while not self._stop.is_set():
            host = self._host()
            if not host:
                self._stop.wait(1.0)
                continue
            try:
                with socket.create_connection((host, self.port), timeout=3.0) as s:
                    self.stats.update(connected=True, peer=host, connections=self.stats["connections"] + 1, error=None)
                    self._read(s, host)
            except OSError as e:
                self.stats["error"] = f"{type(e).__name__}: {e}"
            finally:
                self.stats["connected"] = False
            self._stop.wait(2.0)

    def _read(self, s, host) -> None:
        s.settimeout(1.0)
        buf, quiet_since = bytearray(), time.monotonic()
        while not self._stop.is_set():
            if self._host() != host:
                return  # the ESP32 has a new address
            try:
                part = s.recv(65536)
            except socket.timeout:
                if time.monotonic() - quiet_since > SILENT_S:
                    return
                continue
            if not part:
                return
            buf += part
            if self._take(buf):
                quiet_since = time.monotonic()

    def _skip(self, buf, n: int) -> None:
        self.stats["skipped_bytes"] += n
        del buf[:n]

    def _take(self, buf) -> bool:  # consume whole frames from buf; True if there was one
        raise NotImplementedError


class ThermalLink(_EspLink):
    name, port = "thermal", PORT

    def __init__(self, on_frame, host, port: int | None = None):
        super().__init__(host, port)
        self.on_frame = on_frame

    def _take(self, buf) -> bool:
        size, any_frame = HEADER.size + 2 * ROWS * COLS, False
        while True:
            i = buf.find(MAGIC)
            if i < 0:
                self._skip(buf, max(0, len(buf) - 3))
                return any_frame
            if i:
                self._skip(buf, i)
            if len(buf) < size:
                return any_frame
            _, _, _, rows, cols, _ = HEADER.unpack_from(buf)
            if (rows, cols) != (ROWS, COLS):  # the magic's four bytes inside something else
                self._skip(buf, 1)
                continue
            frame = np.frombuffer(bytes(buf[HEADER.size:size]), "<i2").astype(np.float32).reshape(ROWS, COLS) / 100.0
            del buf[:size]
            self.stats["frames"] += 1
            any_frame = True
            self.on_frame(frame)


class SoundLink(_EspLink):
    name, port = "sound", SOUND_PORT

    def __init__(self, on_set, host, port: int | None = None):
        super().__init__(host, port)
        self.on_set = on_set

    def _take(self, buf) -> bool:
        any_frame = False
        while True:
            i = buf.find(SOUND_MAGIC)
            if i < 0:
                self._skip(buf, max(0, len(buf) - 1))
                return any_frame
            if i:
                self._skip(buf, i)
            if len(buf) < SOUND_FRAME:
                return any_frame
            body = bytes(buf[2:SOUND_FRAME - 2])
            seq, age_ms, count = struct.unpack_from("<HHB", body)
            if count != SOUND_COUNT or crc16(body) != struct.unpack_from("<H", buf, SOUND_FRAME - 2)[0]:
                self._skip(buf, 1)  # not a frame, or a damaged one: look for the next start
                continue
            del buf[:SOUND_FRAME]
            self.stats["frames"] += 1
            any_frame = True
            self.on_set(age_ms, seq, body[5:])
