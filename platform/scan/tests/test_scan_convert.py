"""platform/scan/scan_convert.py: USDZ to GLB through Blender, here through a stand-in for Blender
(fake_blender.py). The one test that uses a real Blender is skipped on a laptop without it."""

import os
import sys
import threading
import zipfile

import pytest

import scan_convert
from scan_convert import NO_BLENDER, Converter, convert, describe, find_blender
from scan_glb import box_glb, file_format

FAKE = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_blender.py")]


@pytest.fixture
def work(tmp_path):
    """An empty folder for the model, apart from the scan file."""
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture
def usdz(tmp_path):
    """A file that starts like a USDZ (a zip). The stand-in never opens it."""
    path = tmp_path / "scan.usdz"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("scan.usda", "#usda 1.0\n")
    assert file_format(path.read_bytes()) == "usdz"
    return str(path)


def fake(mode):
    return dict(os.environ, FAKE_BLENDER=mode)


# ---- finding Blender -------------------------------------------------------------------------------------


def test_the_environment_variable_comes_first(tmp_path):
    exe = tmp_path / "my-blender"
    exe.write_text("")
    assert find_blender({"EFFENTE_BLENDER": str(exe), "PATH": ""}) == str(exe)


def test_a_wrong_environment_variable_is_not_silently_replaced(tmp_path):
    on_path = tmp_path / ("blender.exe" if os.name == "nt" else "blender")
    on_path.write_text("")
    on_path.chmod(0o755)
    env = {"EFFENTE_BLENDER": str(tmp_path / "missing"), "PATH": str(tmp_path)}
    assert find_blender(env) is None


def test_blender_on_the_path_is_found(tmp_path):
    on_path = tmp_path / ("blender.exe" if os.name == "nt" else "blender")
    on_path.write_text("")
    on_path.chmod(0o755)
    assert os.path.normcase(find_blender({"PATH": str(tmp_path)})) == os.path.normcase(str(on_path))


def test_the_usual_windows_place_is_found_newest_first(tmp_path):
    for version in ("Blender 4.2", "Blender 5.2"):
        folder = tmp_path / "Blender Foundation" / version
        folder.mkdir(parents=True)
        (folder / "blender.exe").write_text("")
    found = find_blender({"PATH": "", "ProgramFiles": str(tmp_path)})
    assert found is not None and "Blender 5.2" in found


def test_no_blender_anywhere():
    if os.path.isfile("/Applications/Blender.app/Contents/MacOS/Blender"):
        pytest.skip("this Mac has Blender in the usual place")
    assert find_blender({"PATH": ""}) is None


# ---- one conversion --------------------------------------------------------------------------------------


def test_a_conversion_writes_the_model(work, usdz):
    target = str(work / "model.glb")
    assert convert(usdz, target, blender=FAKE, env=fake("ok")) == (True, "")
    assert describe(target) == {"bytes": len(box_glb()),
                                "bounds": {"min": [-0.21, 0.0, -0.09], "max": [0.21, 0.14, 0.09]}}
    assert os.listdir(work) == ["model.glb"]  # nothing half-written is left beside it


def test_the_models_folder_is_made_if_missing(work, usdz):
    target = str(work / "scans" / "scan-1" / "model.glb")
    assert convert(usdz, target, blender=FAKE, env=fake("ok")) == (True, "")
    assert os.path.isfile(target)


def test_no_blender_says_how_to_fix_it(work, usdz, monkeypatch):
    monkeypatch.setattr(scan_convert, "find_blender", lambda env=None: None)
    ok, reason = convert(usdz, str(work / "model.glb"))
    assert not ok and reason == NO_BLENDER and "Install" in reason
    assert os.listdir(work) == []


def test_a_missing_scan_file(work):
    assert convert(str(work / "gone.usdz"), str(work / "model.glb"), blender=FAKE) == \
        (False, "the scan file is missing")


def test_blenders_own_reason_is_passed_on(work, usdz):
    ok, reason = convert(usdz, str(work / "model.glb"), blender=FAKE, env=fake("fail"))
    assert (ok, reason) == (False, "the scan holds no 3D shape")
    assert os.listdir(work) == []


def test_a_crash_with_no_reason_names_the_exit_code(work, usdz):
    ok, reason = convert(usdz, str(work / "model.glb"), blender=FAKE, env=fake("crash"))
    assert not ok and "code 7" in reason


def test_a_conversion_that_never_ends_is_stopped(work, usdz):
    ok, reason = convert(usdz, str(work / "model.glb"), blender=FAKE, env=fake("hang"), timeout_s=1.0)
    assert not ok and "longer than 1 seconds" in reason
    assert os.listdir(work) == []


def test_a_result_that_is_not_a_glb_is_refused(work, usdz):
    ok, reason = convert(usdz, str(work / "model.glb"), blender=FAKE, env=fake("junk"))
    assert not ok and "not a GLB" in reason
    assert os.listdir(work) == []


def test_no_result_at_all_is_a_failure(work, usdz):
    ok, reason = convert(usdz, str(work / "model.glb"), blender=FAKE, env=fake("nothing"))
    assert not ok and "wrote no model" in reason


def test_a_program_that_cannot_be_started(work, usdz):
    ok, reason = convert(usdz, str(work / "model.glb"), blender=str(work / "no-such-program"))
    assert not ok and reason.startswith("Blender could not be started")


def test_an_old_model_is_kept_when_a_new_conversion_fails(work, usdz):
    target = work / "model.glb"
    target.write_bytes(box_glb())
    ok, _ = convert(usdz, str(target), blender=FAKE, env=fake("fail"))
    assert not ok and target.read_bytes() == box_glb()


# ---- the background worker -------------------------------------------------------------------------------


class Seen:
    def __init__(self, ends=1):
        self.steps = []
        self.done = threading.Event()
        self.ends = ends

    def __call__(self, scan_id, status, details):
        self.steps.append((scan_id, status, details))
        if sum(1 for s in self.steps if s[1] in ("ready", "failed")) >= self.ends:
            self.done.set()


def test_the_worker_reports_converting_then_ready(work, usdz):
    seen = Seen()
    converter = Converter(seen, convert=lambda s, t, timeout_s: convert(s, t, timeout_s, blender=FAKE, env=fake("ok")))
    assert converter.submit("scan-1", usdz, str(work / "model.glb")) is True
    assert seen.done.wait(30)
    converter.stop()
    assert [s[:2] for s in seen.steps] == [("scan-1", "converting"), ("scan-1", "ready")]
    assert seen.steps[1][2]["bytes"] == len(box_glb())
    assert seen.steps[1][2]["bounds"]["max"] == [0.21, 0.14, 0.09]


def test_the_worker_reports_failed_with_the_reason(work, usdz):
    seen = Seen()
    converter = Converter(seen, convert=lambda s, t, timeout_s: (False, NO_BLENDER))
    converter.submit("scan-1", usdz, str(work / "model.glb"))
    assert seen.done.wait(10)
    converter.stop()
    assert seen.steps[-1] == ("scan-1", "failed", {"reason": NO_BLENDER})


def test_one_at_a_time_in_order_and_never_twice(work, usdz):
    running, most, order = [0], [0], []
    gate = threading.Event()

    def slow(source, target, timeout_s):
        running[0] += 1
        most[0] = max(most[0], running[0])
        order.append(os.path.basename(os.path.dirname(target)))
        gate.wait(10)
        running[0] -= 1
        return False, "stopped by the test"

    seen = Seen(ends=3)
    converter = Converter(seen, convert=slow)
    for name in ("a", "b", "c"):
        assert converter.submit(name, usdz, str(work / name / "model.glb")) is True
    assert converter.submit("b", usdz, str(work / "b" / "model.glb")) is False  # already waiting
    assert converter.busy()
    gate.set()
    assert seen.done.wait(10)
    converter.stop()
    assert most[0] == 1 and order == ["a", "b", "c"]
    assert not converter.busy()
    assert converter.submit("d", usdz, str(work / "d" / "model.glb")) is False  # after stop()


def test_an_error_in_one_conversion_does_not_stop_the_next(work, usdz):
    def broken(source, target, timeout_s):
        if "bad" in target:
            raise RuntimeError("boom")
        return convert(source, target, timeout_s, blender=FAKE, env=fake("ok"))

    seen = Seen(ends=2)
    converter = Converter(seen, convert=broken)
    converter.submit("bad", usdz, str(work / "bad" / "model.glb"))
    converter.submit("good", usdz, str(work / "good" / "model.glb"))
    assert seen.done.wait(30)
    converter.stop()
    ends = {s[0]: s[1] for s in seen.steps if s[1] != "converting"}
    assert ends == {"bad": "failed", "good": "ready"}


def test_a_store_that_raises_does_not_stop_the_worker(work, usdz):
    calls = []
    done = threading.Event()

    def on_change(scan_id, status, details):
        calls.append((scan_id, status))
        if (scan_id, status) == ("two", "failed"):
            done.set()
        raise RuntimeError("the store is broken")

    converter = Converter(on_change, convert=lambda s, t, timeout_s: (False, "no"))
    converter.submit("one", usdz, str(work / "1.glb"))
    converter.submit("two", usdz, str(work / "2.glb"))
    assert done.wait(10)
    converter.stop()
    assert calls == [("one", "converting"), ("one", "failed"), ("two", "converting"), ("two", "failed")]


# ---- a real Blender, when this laptop has one ------------------------------------------------------------


def test_a_real_blender_converts_a_real_usdz(work):
    """Skipped without Blender, or without a scan to try: set EFFENTE_TEST_USDZ to a USDZ file's path.
    No scan file is committed (AGENTS.md: no large binary files)."""
    blender = find_blender()
    sample = os.environ.get("EFFENTE_TEST_USDZ", "")
    if not blender:
        pytest.skip("Blender is not installed on this laptop")
    if not os.path.isfile(sample):
        pytest.skip("set EFFENTE_TEST_USDZ to a USDZ file to try a real conversion")
    target = str(work / "model.glb")
    ok, reason = convert(sample, target)
    assert ok, reason
    box = describe(target)["bounds"]
    assert all(box["max"][k] > box["min"][k] for k in range(3))
