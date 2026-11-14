"""The USB watch against a stand-in for adb. No adb and no board are used here."""

import usb_link as ul

HEADER = "List of devices attached\n"


class FakeAdb:
    """Answers `adb devices` with `self.listed` and records every command."""

    def __init__(self, listed=""):
        self.listed = listed
        self.commands = []
        self.forward_fails = False

    def __call__(self, command):
        self.commands.append(command[1:])
        if command[1:] == ["devices"]:
            return None if self.listed is None else HEADER + self.listed
        if "--remove" not in command and self.forward_fails:
            return None
        return ""


MADE = []  # every FakeLink since the last watch()


class FakeLink:
    def __init__(self, device_url, platform_url, log=None):
        self.device, self.platform, self.running = device_url, platform_url, False
        MADE.append(self)

    def start(self):
        self.running = True
        return self

    def stop(self):
        self.running = False


def watch(adb, ports=(5001, 5002, 5003)):
    MADE.clear()
    said = []
    it = iter(ports)
    usb = ul.UsbLink("http://127.0.0.1:8000", adb="adb", log=said.append, run=adb, make_link=FakeLink,
                     port=lambda: next(it))
    return usb, said


def test_attached_takes_only_ready_devices():
    text = HEADER + "2E2A1B0C\tdevice\nR58M\tunauthorized\nXYZ\toffline\n\n"
    assert ul.attached(text) == ["2E2A1B0C"]
    assert ul.attached("") == [] and ul.attached(HEADER) == []


def test_a_board_plugged_in_is_forwarded_and_linked_once():
    adb = FakeAdb()
    usb, said = watch(adb)
    usb.check()
    assert usb.links == {} and adb.commands == [["devices"]]

    adb.listed = "UNOQ1\tdevice\n"
    usb.check()
    assert ["-s", "UNOQ1", "forward", "tcp:5001", "tcp:8765"] in adb.commands
    (link,) = MADE
    assert link.running and link.device == "http://127.0.0.1:5001" and link.platform == "http://127.0.0.1:8000"
    assert "UNOQ1" in said[-1]

    usb.check()  # still there: the same link and port, the forward asked for again
    assert len(MADE) == 1 and usb.ports == {"UNOQ1": 5001}
    assert adb.commands.count(["-s", "UNOQ1", "forward", "tcp:5001", "tcp:8765"]) == 2


def test_unplugged_stops_the_link_and_plugged_in_again_starts_a_new_one():
    adb = FakeAdb("UNOQ1\tdevice\n")
    usb, said = watch(adb)
    usb.check()
    adb.listed = ""
    usb.check()
    assert not MADE[0].running and usb.links == {} and usb.ports == {}
    assert "unplugged" in said[-1]

    adb.listed = "UNOQ1\tdevice\n"
    usb.check()
    assert len(MADE) == 2 and MADE[1].running and usb.ports == {"UNOQ1": 5002}


def test_adb_not_answering_keeps_the_link():
    adb = FakeAdb("UNOQ1\tdevice\n")
    usb, _ = watch(adb)
    usb.check()
    adb.listed = None
    usb.check()
    assert MADE[0].running and "UNOQ1" in usb.links


def test_a_forward_that_fails_is_tried_again_next_time():
    adb = FakeAdb("UNOQ1\tdevice\n")
    adb.forward_fails = True
    usb, _ = watch(adb)
    usb.check()
    assert MADE == [] and usb.ports == {}
    adb.forward_fails = False
    usb.check()
    assert len(MADE) == 1


def test_stop_stops_every_link_and_removes_the_forwards():
    adb = FakeAdb("A\tdevice\nB\tdevice\n")
    usb, _ = watch(adb)
    usb.check()
    assert len(MADE) == 2
    usb.stop()
    assert not any(link.running for link in MADE) and usb.links == {}
    assert ["-s", "A", "forward", "--remove", "tcp:5001"] in adb.commands


def test_without_adb_it_says_so_once_and_starts_nothing(monkeypatch):
    monkeypatch.setattr(ul, "find_adb", lambda: None)
    said = []
    usb = ul.UsbLink("http://127.0.0.1:8000", log=said.append).start()
    assert said == [ul.NO_ADB] and usb._thread is None
    usb.stop()
