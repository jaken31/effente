"""The launcher entry: written under XDG_DATA_HOME, valid, and removable."""

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[2]
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

import install_launcher as launcher


@pytest.fixture(autouse=True)
def _data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(launcher, "_refresh", lambda: None)
    return tmp_path


def test_install_writes_the_entry_and_the_icon(tmp_path):
    written = launcher.install("/usr/bin/python3")
    assert written == [tmp_path / "applications" / "effente.desktop",
                       tmp_path / "icons" / "hicolor" / "scalable" / "apps" / "effente.svg"]
    text = written[0].read_text(encoding="utf-8")
    assert f'Exec="/usr/bin/python3" "{launcher.MAIN}"\n' in text
    assert f'Exec="/usr/bin/python3" "{launcher.MAIN}" --no-sim\n' in text
    assert "Icon=effente\n" in text
    assert written[1].read_bytes() == launcher.ICON.read_bytes()


def test_the_entry_points_at_files_that_exist():
    assert launcher.MAIN.is_file()
    assert launcher.ICON.is_file()
    # main.py names its windows after the entry, so the desktop can match them.
    assert 'setDesktopFileName("effente")' in launcher.MAIN.read_text(encoding="utf-8")


def test_paths_with_spaces_and_specials_are_quoted():
    assert launcher.quoted("/home/a b/py") == '"/home/a b/py"'
    assert launcher.quoted('/x/$HOME/"q"/100%') == '"/x/\\$HOME/\\"q\\"/100%%"'


@pytest.mark.skipif(shutil.which("desktop-file-validate") is None, reason="needs desktop-file-validate")
def test_the_entry_is_a_valid_desktop_file():
    entry, _ = launcher.install("/usr/bin/python3")
    result = subprocess.run(["desktop-file-validate", str(entry)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_remove_deletes_both_and_is_safe_to_repeat():
    written = launcher.install("/usr/bin/python3")
    assert launcher.remove() == written
    assert not any(p.exists() for p in written)
    assert launcher.remove() == []


# macOS: two apps in ~/Applications.

@pytest.fixture
def apps(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "mac_apps_dir", lambda: tmp_path / "Applications")
    monkeypatch.setattr(launcher, "mac_icon", lambda dest: bool(dest.write_bytes(b"icns")))
    return tmp_path / "Applications"


def test_mac_install_writes_two_apps(apps):
    written = launcher.mac_install("/usr/bin/python3", path="/opt/homebrew/bin:/usr/bin")
    assert written == [apps / "effente.app", apps / "effente (real device).app"]
    for app, extra in zip(written, ("", " --no-sim")):
        script = app / "Contents" / "MacOS" / "effente"
        assert os.access(script, os.X_OK)
        text = script.read_text(encoding="utf-8")
        assert text.startswith("#!/bin/sh\n")
        assert "export PATH=/opt/homebrew/bin:/usr/bin\n" in text
        assert f"exec /usr/bin/python3 {launcher.MAIN}{extra} >>" in text
        with open(app / "Contents" / "Info.plist", "rb") as f:
            plist = plistlib.load(f)
        assert plist["CFBundleExecutable"] == "effente"
        assert plist["CFBundleName"] == app.stem
        assert (app / "Contents" / "Resources" / f"{plist['CFBundleIconFile']}.icns").is_file()
    ids = {plistlib.loads((a / "Contents" / "Info.plist").read_bytes())["CFBundleIdentifier"] for a in written}
    assert len(ids) == 2


def test_mac_script_quotes_paths_with_spaces():
    text = launcher.mac_script("/Users/a b/py", ["--no-sim"], "/x y/bin:/usr/bin")
    assert "exec '/Users/a b/py' " in text
    assert "export PATH='/x y/bin:/usr/bin'\n" in text
    assert subprocess.run(["sh", "-n"], input=text, text=True, check=False).returncode == 0


def test_mac_install_without_an_icon_still_writes_the_apps(apps, monkeypatch):
    monkeypatch.setattr(launcher, "mac_icon", lambda dest: False)
    written = launcher.mac_install("/usr/bin/python3", path="/usr/bin")
    assert all((a / "Contents" / "MacOS" / "effente").is_file() for a in written)
    assert not any((a / "Contents" / "Resources" / "effente.icns").exists() for a in written)


def test_mac_install_replaces_and_remove_is_safe_to_repeat(apps):
    launcher.mac_install("/old/python", path="/usr/bin")
    written = launcher.mac_install("/new/python", path="/usr/bin")
    assert "/old/python" not in (written[0] / "Contents" / "MacOS" / "effente").read_text(encoding="utf-8")
    assert launcher.mac_remove() == written
    assert not any(a.exists() for a in written)
    assert launcher.mac_remove() == []


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("iconutil") is None, reason="needs macOS's iconutil")
def test_mac_icon_is_a_real_icns(tmp_path):
    pytest.importorskip("PySide6.QtSvg")
    dest = tmp_path / "effente.icns"
    assert launcher.mac_icon(dest)
    assert dest.read_bytes()[:4] == b"icns"
