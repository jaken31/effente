"""The launcher entry: written under XDG_DATA_HOME, valid, and removable."""

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
