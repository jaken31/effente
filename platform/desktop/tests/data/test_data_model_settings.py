"""The model folder and size, kept in the settings store like the colour scheme."""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from app.data.model_settings import FOLDER_KEY, SIZE_KEY, ModelSettings
from PySide6.QtCore import QSettings


@pytest.fixture
def ini(tmp_path):
    return str(tmp_path / "effente.ini")


def store(ini):
    return QSettings(ini, QSettings.Format.IniFormat)


def test_defaults_are_the_small_model_in_effente_models(ini):
    s = ModelSettings(store(ini))
    assert s.folder() == "~/effente-models"
    assert s.size() == "small"
    assert s.model_file() == Path.home() / "effente-models" / "small.gguf"


def test_folder_and_size_come_back_on_the_next_run(ini, tmp_path):
    first = ModelSettings(store(ini))
    first.set_folder(tmp_path / "models")
    first.set_size("large")
    del first

    second = ModelSettings(store(ini))
    assert second.folder() == str(tmp_path / "models")
    assert second.size() == "large"
    assert second.model_file() == tmp_path / "models" / "large.gguf"
    assert set(store(ini).allKeys()) == {FOLDER_KEY, SIZE_KEY}


def test_bad_values_are_not_kept(ini):
    s = ModelSettings(store(ini))
    s.set_size("huge")
    s.set_folder("   ")
    assert (s.folder(), s.size()) == ("~/effente-models", "small")
    raw = store(ini)
    raw.setValue(SIZE_KEY, "huge")  # written by hand, or by an older version
    raw.setValue(FOLDER_KEY, "")
    raw.sync()
    assert (ModelSettings(store(ini)).folder(), ModelSettings(store(ini)).size()) == ("~/effente-models", "small")


def test_without_a_store_it_lasts_only_this_run():
    s = ModelSettings()
    s.set_size("large")
    assert s.size() == "large"
    assert ModelSettings().size() == "small"
