"""Reading a GLB file (binary glTF 2.0) with the standard library: what kind of file a scan is, and the
smallest box around a model. Also writes a small box as a GLB, for tests and for the 3D view's sample.

Coordinates are the file's own: right-handed, Y up, in the file's units (docs/scan-and-log-plan.md, 5.4).
"""

import json
import struct
from array import array

GLB_MAGIC = b"glTF"
ZIP_MAGIC = b"PK\x03\x04"  # a USDZ file is a zip file


class BadGlb(ValueError):
    """The file is not a GLB this code can read. The message is a plain sentence."""


def file_format(first_bytes: bytes):
    """'usdz', 'glb' or None, from a file's first four bytes. The Content-Type is never trusted."""
    head = bytes(first_bytes[:4])
    if head == ZIP_MAGIC:
        return "usdz"
    if head == GLB_MAGIC:
        return "glb"
    return None


def read_glb(path):
    """The GLB's JSON part as a dict, and its binary part as bytes (empty if it has none)."""
    with open(path, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[:4] != GLB_MAGIC:
            raise BadGlb("not a GLB file")
        version, length = struct.unpack("<II", header[4:])
        if version != 2:
            raise BadGlb(f"GLB version {version} is not supported, only version 2")
        doc, binary = None, b""
        read = 12
        while read < length:
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                break
            size, kind = struct.unpack("<I4s", chunk_header)
            data = f.read(size)
            if len(data) < size:
                raise BadGlb("the GLB file is cut off")
            read += 8 + size
            if kind == b"JSON" and doc is None:
                try:
                    doc = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise BadGlb("the GLB file's description cannot be read") from None
            elif kind == b"BIN\x00" and not binary:
                binary = data
    if not isinstance(doc, dict):
        raise BadGlb("the GLB file has no description")
    return doc, binary


# ---- 4 x 4 matrices, as 16 numbers in glTF's order (column-major) ----------------------------------------

IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def _multiply(a, b):
    """a times b: b is applied to a point first."""
    return tuple(sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4)) for col in range(4) for row in range(4))


def _node_matrix(node):
    if "matrix" in node:
        return tuple(float(v) for v in node["matrix"])
    tx, ty, tz = node.get("translation", (0.0, 0.0, 0.0))
    x, y, z, w = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    sx, sy, sz = node.get("scale", (1.0, 1.0, 1.0))
    return (
        (1 - 2 * (y * y + z * z)) * sx, (2 * (x * y + z * w)) * sx, (2 * (x * z - y * w)) * sx, 0.0,
        (2 * (x * y - z * w)) * sy, (1 - 2 * (x * x + z * z)) * sy, (2 * (y * z + x * w)) * sy, 0.0,
        (2 * (x * z + y * w)) * sz, (2 * (y * z - x * w)) * sz, (1 - 2 * (x * x + y * y)) * sz, 0.0,
        float(tx), float(ty), float(tz), 1.0,
    )


def _apply(m, p):
    x, y, z = p
    return (m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14])


def _keeps_axes(m, eps=1e-6):
    """True when the matrix only scales and moves (no rotation or shear), so a box stays a box."""
    off = (m[1], m[2], m[4], m[6], m[8], m[9])
    return all(abs(v) <= eps for v in off)


# ---- positions -------------------------------------------------------------------------------------------


def _positions(doc, binary, accessor):
    """One accessor's points as three arrays (xs, ys, zs), or None when they are not plain float triples
    in the GLB's own binary part."""
    if accessor.get("type") != "VEC3" or accessor.get("componentType") != 5126 or "sparse" in accessor:
        return None
    view_index = accessor.get("bufferView")
    if view_index is None:
        return None
    view = doc["bufferViews"][view_index]
    if view.get("buffer", 0) != 0 or "uri" in doc.get("buffers", [{}])[0]:
        return None
    count = int(accessor.get("count", 0))
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    stride = int(view.get("byteStride", 12)) or 12
    if count <= 0 or start + (count - 1) * stride + 12 > len(binary):
        return None
    if stride == 12:
        flat = array("f")
        flat.frombytes(binary[start:start + count * 12])
        if struct.pack("=H", 1) != struct.pack("<H", 1):
            flat.byteswap()  # GLB is little-endian
        return flat[0::3], flat[1::3], flat[2::3]
    xs, ys, zs = array("f"), array("f"), array("f")
    for i in range(count):
        x, y, z = struct.unpack_from("<fff", binary, start + i * stride)
        xs.append(x)
        ys.append(y)
        zs.append(z)
    return xs, ys, zs


def _corners(lo, hi):
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def bounds(path):
    """The smallest box around the model: {"min": [x, y, z], "max": [x, y, z]}, in the file's own
    coordinates, with every node's position, turn and scale applied. Raises BadGlb if there is no mesh.

    Exact for a mesh that is not turned. For a turned mesh every point is read, when the points are plain
    floats in the file; if they are not, the turned box of the mesh is used, which can be a little too big.
    """
    doc, binary = read_glb(path)
    nodes = doc.get("nodes", [])
    meshes = doc.get("meshes", [])
    accessors = doc.get("accessors", [])
    scenes = doc.get("scenes", [])
    if scenes:
        roots = scenes[doc.get("scene", 0)].get("nodes", [])
    else:  # no scene: every node that is nobody's child
        children = {c for n in nodes for c in n.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in children]

    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3

    def take(points):
        for p in points:
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]

    stack = [(i, IDENTITY) for i in roots]
    seen = set()
    while stack:
        index, parent = stack.pop()
        if index in seen or not 0 <= index < len(nodes):
            continue  # a broken file must not loop for ever
        seen.add(index)
        node = nodes[index]
        world = _multiply(parent, _node_matrix(node))
        for child in node.get("children", []):
            stack.append((child, world))
        if "mesh" not in node:
            continue
        for primitive in meshes[node["mesh"]].get("primitives", []):
            a = primitive.get("attributes", {}).get("POSITION")
            if a is None:
                continue
            accessor = accessors[a]
            box = None
            if len(accessor.get("min", ())) == 3 and len(accessor.get("max", ())) == 3:
                box = ([float(v) for v in accessor["min"]], [float(v) for v in accessor["max"]])
            if box and _keeps_axes(world):
                take(_apply(world, c) for c in _corners(*box))
                continue
            points = _positions(doc, binary, accessor)
            if points is not None:
                xs, ys, zs = points
                if _keeps_axes(world):
                    box = ([min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)])
                    take(_apply(world, c) for c in _corners(*box))
                else:
                    take(_apply(world, p) for p in zip(xs, ys, zs))
            elif box:
                take(_apply(world, c) for c in _corners(*box))

    if lo[0] == float("inf"):
        raise BadGlb("the GLB file has no mesh")
    return {"min": [round(v, 6) for v in lo], "max": [round(v, 6) for v in hi]}


# ---- a box, written by code ------------------------------------------------------------------------------


def box_glb(lo=(-0.21, 0.0, -0.09), hi=(0.21, 0.14, 0.09), colour=(0.6, 0.62, 0.65, 1.0)) -> bytes:
    """A GLB file holding one box between two corners: 24 points with normals, 12 triangles, one plain
    material. For tests and for the 3D view's sample, so that no binary file is committed."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    faces = [  # normal, then the four corners, counter-clockwise seen from outside
        ((1, 0, 0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
        ((-1, 0, 0), [(x0, y0, z1), (x0, y1, z1), (x0, y1, z0), (x0, y0, z0)]),
        ((0, 1, 0), [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
        ((0, -1, 0), [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)]),
        ((0, 0, 1), [(x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1)]),
        ((0, 0, -1), [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)]),
    ]
    positions, normals, indices = [], [], []
    for normal, corners in faces:
        base = len(positions) // 3
        for corner in corners:
            positions.extend(corner)
            normals.extend(normal)
        indices.extend((base, base + 1, base + 2, base, base + 2, base + 3))
    position_bytes = struct.pack(f"<{len(positions)}f", *positions)
    normal_bytes = struct.pack(f"<{len(normals)}f", *normals)
    index_bytes = struct.pack(f"<{len(indices)}H", *indices)
    binary = position_bytes + normal_bytes + index_bytes
    binary += b"\x00" * (-len(binary) % 4)
    doc = {
        "asset": {"version": "2.0", "generator": "effente scan_glb.box_glb"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "box"}],
        "meshes": [{"name": "box", "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2,
                                                   "material": 0}]}],
        "materials": [{"name": "plain", "pbrMetallicRoughness": {"baseColorFactor": list(colour),
                                                                 "metallicFactor": 0.0, "roughnessFactor": 0.8}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(position_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(position_bytes), "byteLength": len(normal_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(position_bytes) + len(normal_bytes), "byteLength": len(index_bytes),
             "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 24, "type": "VEC3",
             "min": [float(min(x0, x1)), float(min(y0, y1)), float(min(z0, z1))],
             "max": [float(max(x0, x1)), float(max(y0, y1)), float(max(z0, z1))]},
            {"bufferView": 1, "componentType": 5126, "count": 24, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 36, "type": "SCALAR"},
        ],
    }
    text = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    return b"".join([GLB_MAGIC, struct.pack("<II", 2, total), struct.pack("<I4s", len(text), b"JSON"), text,
                     struct.pack("<I4s", len(binary), b"BIN\x00"), binary])
