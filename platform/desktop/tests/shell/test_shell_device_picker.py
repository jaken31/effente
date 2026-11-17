"""The pairing window shown at start (app/shell/device_picker.py), with a stand-in for the search."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.shell import device_picker as dp
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog

CONVEYOR = {"url": "http://192.168.43.20:8765", "host": "192.168.43.20", "mode": "watching", "machine_id": "conveyor-07"}
USB = {"url": "http://127.0.0.1:8765", "host": "127.0.0.1", "mode": "", "device_id": "uno-q-1"}


class FakeFinder(QObject):
    found = Signal(list)

    def __init__(self):
        super().__init__()
        self.starts = self.cancels = 0

    def start(self):
        self.starts += 1

    def cancel(self):
        self.cancels += 1


@pytest.fixture
def finder():
    return FakeFinder()


@pytest.fixture
def picker(qtbot, finder):
    w = dp.DevicePicker(finder=finder, networks=lambda: ["192.168.43.0/24"])
    qtbot.addWidget(w)
    w.show()
    return w


def click(qtbot, button):
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def test_it_opens_looking_with_no_device_as_the_only_row(picker, finder):
    assert finder.starts == 1 and picker.status.text() == dp.LOOKING and not picker.refresh_button.isEnabled()
    assert picker.urls() == [None] and picker.list.item(0).text() == dp.NO_DEVICE_SIM
    assert picker.device_url() is None and picker.pair_button.text() == dp.CONTINUE


def test_found_devices_show_and_the_first_is_picked(picker, finder):
    finder.found.emit([CONVEYOR, USB])
    assert [picker.list.item(i).text() for i in range(3)] == [
        "conveyor-07  ·  watching  ·  192.168.43.20", "uno-q-1  ·  this laptop (USB)", dp.NO_DEVICE_SIM]
    assert picker.status.text() == "2 devices found." and picker.refresh_button.isEnabled()
    assert picker.device_url() == CONVEYOR["url"] and picker.pair_button.text() == dp.PAIR


def test_pair_gives_the_picked_device(qtbot, picker, finder):
    finder.found.emit([CONVEYOR, USB])
    picker.list.setCurrentRow(1)
    click(qtbot, picker.pair_button)
    assert picker.result() == QDialog.DialogCode.Accepted and picker.device_url() == USB["url"]
    assert finder.cancels == 1


def test_no_device_can_be_picked_and_stays_picked_over_a_refresh(qtbot, picker, finder):
    finder.found.emit([CONVEYOR])
    picker.list.setCurrentRow(1)
    assert picker.device_url() is None and picker.pair_button.text() == dp.CONTINUE
    click(qtbot, picker.refresh_button)
    assert finder.starts == 2 and picker.status.text() == dp.LOOKING and picker.urls() == [CONVEYOR["url"], None]
    finder.found.emit([USB, CONVEYOR])
    assert picker.device_url() is None  # the person's own pick, not the first device found
    picker.list.setCurrentRow(1)
    finder.found.emit([USB])  # the picked device is gone: the first row again
    assert picker.device_url() == USB["url"]
    click(qtbot, picker.pair_button)
    assert picker.device_url() == USB["url"]


def test_nothing_found_says_why(qtbot, finder):
    finder.found.emit([])
    w = dp.DevicePicker(finder=finder, networks=list, with_sim=False)
    qtbot.addWidget(w)
    finder.found.emit([])
    assert w.status.text() == dp.NO_NETWORK and w.list.item(0).text() == dp.NO_DEVICE
    w._networks = lambda: ["10.0.0.0/24"]
    finder.found.emit([])
    assert w.status.text() == dp.NONE_FOUND
    finder.found.emit([CONVEYOR])
    assert w.status.text() == "1 device found."
    w.reject()
    assert w.result() == QDialog.DialogCode.Rejected


def test_its_own_search_asks_near_the_address_connected_to_last(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(dp.finding, "find", lambda last="": asked.append(last) or [CONVEYOR])
    w = dp.DevicePicker(last="10.110.213.214", networks=lambda: ["10.110.173.0/24"])
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.device_url() == CONVEYOR["url"], timeout=5000)
    assert asked == ["10.110.213.214"]
