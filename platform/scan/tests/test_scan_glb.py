"""platform/scan/scan_glb.py: what kind of file a scan is, and the box around a model."""

import json
import math
import struct

import pytest

from scan_glb import BadGlb, bounds, box_glb, file_format, read_glb


def write(tmp_path, data, name="model.glb"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def rebuilt(data, change):
    """The same GLB with its description changed by `change(doc)`."""
    length = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + length])
    rest = data[20 + length:]
    change(doc)
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    return (b"glTF" + struct.pack("<II", 2, 12 + 8 + len(text) + len(rest))
            + struct.pack("<I4s", len(text), b"JSON") + text + rest)


def test_file_format_reads_the_first_four_bytes():
    assert file_format(b"PK\x03\x04rest of a zip") == "usdz"
    assert file_format(box_glb()) == "glb"
    assert file_format(b"hello, I am text") is None
    assert file_format(b"") is None
    assert file_format(b"PK") is None


def test_box_is_a_valid_glb(tmp_path):
    data = box_glb()
    assert len(data) % 4 == 0 and struct.unpack("<I", data[8:12])[0] == len(data)
    doc, binary = read_glb(write(tmp_path, data))
    assert doc["asset"]["version"] == "2.0"
    assert doc["buffers"][0]["byteLength"] == len(binary)
    assert doc["accessors"][0]["count"] == 24 and doc["accessors"][2]["count"] == 36


def test_bounds_of_the_box(tmp_path):
    path = write(tmp_path, box_glb(lo=(-0.21, 0.0, -0.09), hi=(0.21, 0.14, 0.09)))
    assert bounds(path) == {"min": [-0.21, 0.0, -0.09], "max": [0.21, 0.14, 0.09]}


def test_bounds_apply_a_nodes_move_and_scale(tmp_path):
    def change(doc):
        doc["nodes"][0].update(translation=[1, 2, 3], scale=[2, 2, 2])

    path = write(tmp_path, rebuilt(box_glb(lo=(0, 0, 0), hi=(1, 1, 1)), change))
    assert bounds(path) == {"min": [1, 2, 3], "max": [3, 4, 5]}


def test_bounds_apply_a_parents_move_too(tmp_path):
    def change(doc):
        doc["nodes"] = [{"children": [1], "translation": [10, 0, 0]}, {"mesh": 0, "translation": [0, 5, 0]}]

    path = write(tmp_path, rebuilt(box_glb(lo=(0, 0, 0), hi=(1, 1, 1)), change))
    assert bounds(path) == {"min": [10, 5, 0], "max": [11, 6, 1]}


def test_bounds_of_a_turned_box_come_from_its_points(tmp_path):
    half = math.sqrt(0.5)

    def change(doc):  # a quarter turn about Y: x becomes -z, z becomes x
        doc["nodes"][0]["rotation"] = [0, half, 0, half]

    path = write(tmp_path, rebuilt(box_glb(lo=(0, 0, 0), hi=(1, 2, 3)), change))
    got = bounds(path)
    assert got["min"] == pytest.approx([0, 0, -1], abs=1e-5)
    assert got["max"] == pytest.approx([3, 2, 0], abs=1e-5)


def test_bounds_without_min_and_max_are_read_from_the_points(tmp_path):
    def change(doc):
        del doc["accessors"][0]["min"], doc["accessors"][0]["max"]

    path = write(tmp_path, rebuilt(box_glb(lo=(-1, -2, -3), hi=(1, 2, 3)), change))
    assert bounds(path) == {"min": [-1, -2, -3], "max": [1, 2, 3]}


def test_a_file_that_is_not_a_glb_is_refused(tmp_path):
    with pytest.raises(BadGlb, match="not a GLB"):
        bounds(write(tmp_path, b"PK\x03\x04 a zip, not a glb"))
    with pytest.raises(BadGlb, match="not a GLB"):
        bounds(write(tmp_path, b""))


def test_a_cut_off_glb_is_refused(tmp_path):
    with pytest.raises(BadGlb):
        bounds(write(tmp_path, box_glb()[:60]))


def test_a_glb_with_no_mesh_is_refused(tmp_path):
    def change(doc):
        doc["nodes"] = [{"name": "empty"}]

    with pytest.raises(BadGlb, match="no mesh"):
        bounds(write(tmp_path, rebuilt(box_glb(), change)))


def test_nodes_that_point_at_each_other_do_not_loop(tmp_path):
    def change(doc):
        doc["nodes"] = [{"mesh": 0, "children": [1]}, {"children": [0]}]

    assert bounds(write(tmp_path, rebuilt(box_glb(lo=(0, 0, 0), hi=(1, 1, 1)), change)))["max"] == [1, 1, 1]
