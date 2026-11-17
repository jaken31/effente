"""The pairing window shown before the main window: the devices found on the laptop's network, and
"No device". The person picks one and presses Pair; Refresh looks again.

The search also asks the addresses near the one connected to last time (`last`), as the Device page's
Find boards does: on a large network the device is often not near the laptop's own address.

`device_url()` is the picked device's own port (http://ADDRESS:8765) for `ReceiverHost(device_url=...)`,
or None for "No device". Closing the window without choosing rejects it: main.py then quits.
"""

from __future__ import annotations

from app.data import devices as finding
from app.theme import fonts
from app.theme import tokens as t
from app.theme.qss import css
from app.widgets import Button
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

TITLE = "Pair a device"
INTRO = ("Pick the device this laptop reads. Devices on the same WiFi as this laptop show here, a board "
         "on USB, and the device connected to last time.")
NO_DEVICE = "No device"
NO_DEVICE_SIM = "No device: use the simulated device"
LOOKING = "Looking for devices on this network…"
NO_NETWORK = "This laptop is on no local network. Join the WiFi the device is on, then press Refresh."
NONE_FOUND = "No device answered. Check that it is on and on this WiFi, then press Refresh."
PAIR = "Pair"
CONTINUE = "Continue"
REFRESH = "Refresh"
URL_ROLE = Qt.ItemDataRole.UserRole


def device_text(device: dict) -> str:
    """One device as a line: what it calls itself, what it is doing, and where it is."""
    name = device.get("machine_id") or device.get("device_id") or "Device"
    where = "this laptop (USB)" if device.get("host") == finding.THIS_LAPTOP else device.get("host", "")
    mode = device.get("mode")
    return f"{name}  ·  {mode}  ·  {where}" if mode else f"{name}  ·  {where}"


def found_text(count: int) -> str:
    return "1 device found." if count == 1 else f"{count} devices found."


def _label(text: str, role: str, font=None) -> QLabel:
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setProperty("role", role)
    w.setFont(font or fonts.body())
    w.setWordWrap(True)
    return w


class DevicePicker(QDialog):
    def __init__(self, parent: QWidget | None = None, *, with_sim: bool = True, finder=None, networks=None,
                 last: str = ""):
        """`last` is the address connected to last time: the addresses near it are asked too."""
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setMinimumSize(460, 380)
        self.finder = finder or finding.Finder(self, find=lambda: finding.find(last=last))
        self.finder.found.connect(self.show_devices)
        self._networks = networks or finding.networks
        self._no_device = NO_DEVICE_SIM if with_sim else NO_DEVICE
        self._chosen = False  # the person picked a row themselves: keep it over a refresh

        col = QVBoxLayout(self)
        col.setContentsMargins(20, 18, 20, 18)
        col.setSpacing(10)
        col.addWidget(_label(TITLE, "strong", fonts.panel_title()))
        col.addWidget(_label(INTRO, "muted", fonts.small()))

        self.list = QListWidget()
        self.list.setObjectName("devicePickerList")
        self.list.setFont(fonts.body())
        self.list.setAccessibleName("Devices")
        self.list.setStyleSheet(
            f"#devicePickerList {{ background: {t.PANEL}; color: {t.TEXT}; border: 1px solid {css(t.BORDER)}; "
            f"border-radius: {t.R_SM}px; padding: 4px; outline: none; }}"
            f"#devicePickerList::item {{ padding: 8px 10px; border-radius: {t.R_SM}px; }}"
            f"#devicePickerList::item:selected {{ background: {css(t.BORDER)}; color: {t.TEXT}; }}")
        self.list.currentItemChanged.connect(self._picked)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        col.addWidget(self.list, 1)

        self.status = _label("", "muted", fonts.small())
        col.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self.refresh_button = Button(REFRESH)
        self.refresh_button.clicked.connect(self.refresh)
        self.pair_button = Button(CONTINUE, "primary")
        self.pair_button.setDefault(True)
        self.pair_button.clicked.connect(self.accept)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        buttons.addWidget(self.pair_button)
        col.addLayout(buttons)

        self._fill([])
        self.refresh()

    # ---- reads ------------------------------------------------------------

    def device_url(self):
        """The picked device's own port, or None for "No device"."""
        item = self.list.currentItem()
        return item.data(URL_ROLE) if item is not None else None

    def urls(self) -> list:
        """What each row stands for, top to bottom: a device's URL, or None for "No device"."""
        return [self.list.item(i).data(URL_ROLE) for i in range(self.list.count())]

    # ---- actions ----------------------------------------------------------

    def refresh(self) -> None:
        """Look for devices again. The rows stay until the answer comes."""
        self.refresh_button.setEnabled(False)
        self.status.setText(LOOKING)
        self.finder.start()

    def show_devices(self, devices: list) -> None:
        self._fill(devices)
        self.refresh_button.setEnabled(True)
        self.status.setText(found_text(len(devices)) if devices else NONE_FOUND if self._networks() else NO_NETWORK)

    def done(self, result: int) -> None:
        self.finder.cancel()  # a search still running must not answer a closed window
        super().done(result)

    # ---- drawing ----------------------------------------------------------

    def _fill(self, devices: list) -> None:
        """The devices, then "No device". A row the person picked stays picked while it is still there.
        Otherwise the first row is: the first device found, or "No device" when there is none."""
        before = self.device_url()
        self.list.blockSignals(True)
        self.list.clear()
        for device in devices:
            item = QListWidgetItem(device_text(device))
            item.setData(URL_ROLE, device["url"])
            self.list.addItem(item)
        none = QListWidgetItem(self._no_device)
        none.setData(URL_ROLE, None)
        self.list.addItem(none)
        urls = self.urls()
        self._chosen = self._chosen and before in urls
        self.list.setCurrentRow(urls.index(before) if self._chosen else 0)
        self.list.blockSignals(False)
        self._draw_button()

    def _picked(self, *_args) -> None:
        self._chosen = True
        self._draw_button()

    def _draw_button(self) -> None:
        self.pair_button.setText(PAIR if self.device_url() else CONTINUE)
