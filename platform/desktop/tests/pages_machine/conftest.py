"""Tests for the Machine page. Skipped cleanly without PySide6 or pytest-qt."""

import importlib.util
import os
import sys
from pathlib import Path

if importlib.util.find_spec("PySide6") is None or importlib.util.find_spec("pytestqt") is None:
    collect_ignore_glob = ["test_*.py"]
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    TESTS = Path(__file__).resolve().parents[1]
    for p in (TESTS.parent, TESTS):  # platform/desktop for `app`, tests/ for `fakes`
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    import pytest

    @pytest.fixture(autouse=True)
    def _theme(qapp):
        from app.theme import apply_theme

        apply_theme(qapp)
