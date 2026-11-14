"""The thermal camera's frames, fetched over WiFi from the microphone's ESP32.

The camera (an MLX90640) hangs on the ESP32 (firmware/esp32_mic/thermal.h), because nothing as big as a
frame fits through the UNO Q's microcontroller. The ESP32 serves frames on TCP port 8767: connect, and
frames follow, each

  "EFT1" | sequence (uint32) | the ESP32's millis() at the frame's end (uint32) | rows (uint8) = 24
  | columns (uint8) = 32 | 0 (uint16) | 768 int16, hundredths of a degree C, row by row     (little-endian)

ThermalLink connects to it from the Linux side and hands every frame to `on_frame(frame_c)`, a (24, 32)
float32 array in degrees C. The ESP32's address is whatever `host()` returns at the time (it tells the
sketch its address over the UART; main.py keeps the latest); None means not known yet. When the ESP32 goes
away the link keeps trying, and the pipeline simply has no heat from the camera meanwhile.

Tested against a fake ESP32 only (tests/test_thermal_link.py): not yet run with the camera (13 Nov).
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
SILENT_S = 5.0  # no frame for this long: connect again
NO_PIXEL_C = -200.0  # the ESP32 writes absolute zero for a pixel the camera's maths could not give


def pack_frame(seq: int, ms: int, frame_c) -> bytes:
    """One frame as the ESP32 sends it (for tests)."""
    v = np.round(np.asarray(frame_c, float) * 100.0).astype("<i2")
    return HEADER.pack(MAGIC, seq & 0xFFFFFFFF, ms & 0xFFFFFFFF, ROWS, COLS, 0) + v.tobytes()


class ThermalLink:
    def __init__(self, on_frame, host, port: int = PORT):
        self.on_frame, self.host, self.port = on_frame, host, port
        self.stats = {"connected": False, "peer": None, "connections": 0, "frames": 0, "skipped_bytes": 0, "error": None}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "ThermalLink":
        self._thread = threading.Thread(target=self._run, name="thermal", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.is_set():
            host = self.host() if callable(self.host) else self.host
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
        buf, size, quiet_since = bytearray(), HEADER.size + 2 * ROWS * COLS, time.monotonic()
        while not self._stop.is_set():
            now_host = self.host() if callable(self.host) else self.host
            if now_host != host:
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
            while True:
                i = buf.find(MAGIC)
                if i < 0:
                    self.stats["skipped_bytes"] += max(0, len(buf) - 3)
                    del buf[:max(0, len(buf) - 3)]
                    break
                if i:
                    self.stats["skipped_bytes"] += i
                    del buf[:i]
                if len(buf) < size:
                    break
                _, _, _, rows, cols, _ = HEADER.unpack_from(buf)
                if (rows, cols) != (ROWS, COLS):  # the magic's four bytes inside something else
                    self.stats["skipped_bytes"] += 1
                    del buf[:1]
                    continue
                frame = np.frombuffer(bytes(buf[HEADER.size:size]), "<i2").astype(np.float32).reshape(ROWS, COLS) / 100.0
                del buf[:size]
                quiet_since = time.monotonic()
                self.stats["frames"] += 1
                self.on_frame(frame)
