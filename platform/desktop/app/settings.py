"""Where the app keeps what it remembers between runs: the colour scheme, the
window's size, the page and the machine. One QSettings store for all of it."""

from __future__ import annotations


def store():
    from PySide6.QtCore import QSettings

    return QSettings("effente", "desktop")


class Choices:
    """What one page keeps between runs (a range, a filter, a view), under
    `pages/<page>/` in the store. With no store nothing is kept, which is how
    the tests run. The window hands one to every page that has `remember()`."""

    def __init__(self, store=None, page: str = ""):
        self._store = store
        self._prefix = f"pages/{page}/"

    def get(self, name: str, allowed) -> str | None:
        """The kept value if it is still one of `allowed`, else None: a choice
        that was renamed or removed since is dropped, not passed on."""
        if self._store is None:
            return None
        value = self._store.value(self._prefix + name)
        return value if isinstance(value, str) and value in allowed else None

    def set(self, name: str, value: str) -> None:
        if self._store is not None:
            self._store.setValue(self._prefix + name, value)
