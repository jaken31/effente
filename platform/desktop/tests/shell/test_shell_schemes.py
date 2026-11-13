"""Colour schemes: applying one swaps tokens, and the sidebar picker saves the choice."""

import pytest
from app.theme import schemes
from app.theme import tokens as t
from app.theme.qss import stylesheet


@pytest.fixture(autouse=True)
def _back_to_default():
    yield
    schemes.apply(schemes.DEFAULT)


def test_default_scheme_changes_nothing():
    before = {k: v for k, v in vars(t).items() if k.isupper()}
    schemes.apply("blue")
    schemes.apply(schemes.DEFAULT)
    assert {k: v for k, v in vars(t).items() if k.isupper()} == before


@pytest.mark.parametrize("key", [k for k, _ in schemes.names()])
def test_scheme_applies_and_keeps_the_red_rule(key):
    assert schemes.apply(key) == key
    assert t.ACCENT == t.PRIMARY == t.WARN == t.RED
    assert all(t.is_red(c) for c in (t.RED, t.PRIMARY_HOVER, t.ALERT, t.ACCENT_BG))
    assert not t.is_red(t.TEXT)
    assert not t.is_red(t.text_color(t.RED))
    assert t.BG in stylesheet()


def test_unknown_scheme_falls_back():
    assert schemes.apply("nope") == schemes.DEFAULT


def test_picker_saves_the_choice(window, monkeypatch):
    saved = []
    monkeypatch.setattr(schemes, "save", saved.append)
    window.sidebar.scheme_changed.disconnect()
    window.sidebar.scheme_changed.connect(lambda key: schemes.save(key))
    box = window.sidebar.scheme
    assert box.currentData() == schemes.DEFAULT
    assert not window.sidebar.scheme_note.isVisible()
    box.setCurrentIndex(box.findData("light"))
    assert saved == ["light"]
    assert window.sidebar.scheme_note.isVisible()
