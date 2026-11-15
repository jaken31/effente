"""platform/run.py --fresh and the desktop app's --fresh: the stored history is moved, never deleted."""

import importlib.util
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parents[2]


def load(path: Path):
    spec = importlib.util.spec_from_file_location("fresh_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script", ["run.py", "desktop/main.py"])
def test_fresh_moves_the_history_and_keeps_every_file(tmp_path, script):
    set_aside = load(PLATFORM / script).set_aside
    data = tmp_path / "server" / "data"
    (data / "scans").mkdir(parents=True)
    (data / "records.jsonl").write_text("a real record\n")
    (data / "scans" / "one.usdz").write_bytes(b"scan")

    place = set_aside(data)

    assert not data.exists()
    assert place.parent.parent == tmp_path / "server" / "kept" and place.name == "data"
    assert (place / "records.jsonl").read_text() == "a real record\n"
    assert (place / "scans" / "one.usdz").read_bytes() == b"scan"


def test_two_fresh_starts_in_one_second_keep_both_histories(tmp_path):
    set_aside = load(PLATFORM / "run.py").set_aside
    data = tmp_path / "server" / "data"
    places = []
    for text in ("first", "second"):
        data.mkdir(parents=True)
        (data / "records.jsonl").write_text(text)
        places.append(set_aside(data))
    assert [(p / "records.jsonl").read_text() for p in places] == ["first", "second"]
