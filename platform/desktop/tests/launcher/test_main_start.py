"""main.py before any window: which Python it runs on."""

import builtins
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[2]
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

import main  # noqa: E402


@pytest.fixture
def no_pyside(monkeypatch):
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "PySide6":
            raise ImportError("No module named 'PySide6'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)


def test_without_pyside_and_without_a_venv_it_says_what_to_install(no_pyside, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main, "REPO", tmp_path)
    monkeypatch.delenv("EFFENTE_VENV_TRIED", raising=False)
    assert main.main(["--fake"]) == 1
    said = capsys.readouterr().err
    assert "PySide6" in said and "requirements.txt" in said


def test_without_pyside_it_starts_again_with_the_repos_venv_once(no_pyside, monkeypatch, tmp_path):
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setattr(main, "REPO", tmp_path)
    monkeypatch.setattr(main.os, "name", "posix")
    monkeypatch.delenv("EFFENTE_VENV_TRIED", raising=False)
    ran = []
    monkeypatch.setattr(main.os, "execv", lambda path, argv: ran.append((path, argv)))
    main.have_pyside(["--no-sim"])
    assert ran == [(str(python), [str(python), str(main.HERE / "main.py"), "--no-sim"])]
    assert main.have_pyside(["--no-sim"]) is False and len(ran) == 1  # the venv had none either: no loop
    monkeypatch.delenv("EFFENTE_VENV_TRIED", raising=False)


def test_showing_names_the_start():
    assert main.showing(main.parse_args([])) == "the simulated device"
    assert "real device" in main.showing(main.parse_args(["--no-sim"]))
    assert "http://x:8765" in main.showing(main.parse_args(["--device", "http://x:8765"]))
