"""Which model file the local model server loads: a folder and a size.

Kept in the app's settings store (app/settings.py), as the colour scheme is.
The size means a file in the folder: small.gguf or large.gguf.
"""

from __future__ import annotations

from pathlib import Path

from .model_server import DEFAULT_FOLDER, DEFAULT_SIZE, SIZES, model_file

FOLDER_KEY = "model_folder"
SIZE_KEY = "model_size"
SIZE_NAMES = [("small", "Small"), ("large", "Large")]


class ModelSettings:
    """`store` is a QSettings, or None to remember nothing past this run."""

    def __init__(self, store=None):
        self._store = store
        self._unsaved: dict[str, str] = {}

    def _read(self, key: str, default: str) -> str:
        value = self._store.value(key, default) if self._store is not None else self._unsaved.get(key, default)
        return value if isinstance(value, str) and value.strip() else default

    def _write(self, key: str, value: str) -> None:
        if self._store is not None:
            self._store.setValue(key, value)
        else:
            self._unsaved[key] = value

    def folder(self) -> str:
        """The model folder as it was chosen; `~` stands for the home folder."""
        return self._read(FOLDER_KEY, DEFAULT_FOLDER)

    def set_folder(self, folder) -> None:
        text = str(folder).strip()
        if text:
            self._write(FOLDER_KEY, text)

    def size(self) -> str:
        size = self._read(SIZE_KEY, DEFAULT_SIZE)
        return size if size in SIZES else DEFAULT_SIZE

    def set_size(self, size: str) -> None:
        if size in SIZES:
            self._write(SIZE_KEY, size)

    def model_file(self) -> Path:
        return model_file(self.folder(), self.size())
