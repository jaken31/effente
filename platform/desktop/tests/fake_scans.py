"""FakeScans: the `state.scans` interface (INTERFACES.md addition 18) on fixed data, for page tests.

    state = FakeState()
    state.scans.current()        # one scan, ready, of the fake state's machine

The sample of INTERFACES.md addition 20: one scan, `ready`, whose model is a
small box. The box is written by `box_glb()` when a test first asks for it. No
binary file is committed. It is a made-up shape, not a scan of anything.

Actions change only this object and emit the same signals as the real one.
"""

from __future__ import annotations

import copy
import json
import struct
import tempfile
import urllib.parse
from pathlib import Path

from app.data.scans import Scans

SCAN_ID = "scan-20261114155000-fake"
FAKE_BASE = "http://192.168.43.10:8000"  # the contract's example address, not a real laptop
BOX_MIN = (-0.21, 0.0, -0.09)
BOX_MAX = (0.21, 0.14, 0.09)

_cache_dir = None


def box_glb(lo=BOX_MIN, hi=BOX_MAX) -> bytes:
    """A GLB file holding one box from `lo` to `hi`: 24 corners with normals, 12 triangles."""
    positions, normals, indices = [], [], []
    for axis in range(3):
        u, v = (axis + 1) % 3, (axis + 2) % 3
        for sign, side in ((1.0, hi), (-1.0, lo)):
            corners = [(lo[u], lo[v]), (hi[u], lo[v]), (hi[u], hi[v]), (lo[u], hi[v])]
            if sign < 0:
                corners.reverse()  # keep the front of each face outwards
            first = len(positions)
            for cu, cv in corners:
                point = [0.0, 0.0, 0.0]
                point[axis], point[u], point[v] = side[axis], cu, cv
                normal = [0.0, 0.0, 0.0]
                normal[axis] = sign
                positions.append(point)
                normals.append(normal)
            indices += [first, first + 1, first + 2, first, first + 2, first + 3]
    pos = b"".join(struct.pack("<3f", *p) for p in positions)
    nor = b"".join(struct.pack("<3f", *n) for n in normals)
    idx = struct.pack(f"<{len(indices)}H", *indices)
    binary = pos + nor + idx
    binary += b"\x00" * (-len(binary) % 4)
    doc = {
        "asset": {"version": "2.0", "generator": "effente desktop tests"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "box"}],
        "meshes": [{"name": "box", "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": list(lo), "max": list(hi)},
            {"bufferView": 1, "componentType": 5126, "count": len(normals), "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(nor), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos) + len(nor), "byteLength": len(idx), "target": 34963},
        ],
        "buffers": [{"byteLength": len(binary)}],
    }
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<I4s", len(text), b"JSON") + text
            + struct.pack("<I4s", len(binary), b"BIN\x00") + binary)


def box_path() -> Path:
    """The box as a file, written once per process into a temporary folder."""
    global _cache_dir
    if _cache_dir is None:
        _cache_dir = tempfile.TemporaryDirectory(prefix="effente-fake-scan-")
    path = Path(_cache_dir.name) / "model.glb"
    if not path.exists():
        path.write_bytes(box_glb())
    return path


def sample_scan(machine_id: str) -> dict:
    size = len(box_glb())
    return {
        "scan_id": SCAN_ID,
        "machine_id": machine_id,
        "client_id": "FAKE-0001-0001",
        "at": "2026-11-14T15:49:40Z",
        "received_at": "2026-11-14T15:50:00Z",
        "format": "glb",
        "file": {"name": "box.glb", "bytes": size},
        "app": "effente desktop tests",
        "model": {"status": "ready", "reason": "", "bytes": size, "bounds": {"min": list(BOX_MIN), "max": list(BOX_MAX)}},
    }


class FakeScans(Scans):
    def __init__(self, state):
        super().__init__(state)
        self.machine = None  # the machine the sample belongs to; set by fill()
        self._scans: list = []  # newest first
        self.imported: list = []  # paths import_file was called with

    def fill(self) -> None:
        """Load the sample for the fake state's current machine."""
        self.machine = self.state.current_machine()
        self._scans = [sample_scan(self.machine)] if self.machine else []

    # ---- reads ------------------------------------------------------------

    def all(self) -> list:
        return list(self._scans) if self.state.current_machine() == self.machine else []

    def current(self):
        scans = self.all()
        return scans[0] if scans else None

    def model_path(self, scan_id: str):
        for scan in self._scans:
            if scan["scan_id"] == scan_id and scan["model"]["status"] == "ready":
                return str(box_path())
        return None

    def qr_text(self, machine_id: str, name: str = "") -> str:
        q = urllib.parse.quote
        text = f"effente://scan?v=1&base={q(FAKE_BASE, safe='')}&machine={q(machine_id, safe='')}"
        return text + (f"&name={q(name, safe='')}" if name else "")

    # ---- actions ----------------------------------------------------------

    def import_file(self, path) -> None:
        """Nothing is read or sent: the path is remembered and the sample scan shows again."""
        self.imported.append(str(path))
        self.fill()
        self.scans_changed.emit()

    # ---- test helpers -----------------------------------------------------

    def set_status(self, status: str, reason: str = "") -> None:
        """Show the scan as `waiting`, `converting`, `ready` or `failed`, for page tests."""
        scan = copy.deepcopy(sample_scan(self.machine))
        if status != "ready":
            scan["model"] = {"status": status, "reason": reason}
        self._scans = [scan]
        self.scans_changed.emit()
        if status == "ready":
            self.model_ready.emit(scan["scan_id"])

    def clear(self) -> None:
        """No scan yet."""
        self._scans = []
        self.scans_changed.emit()
