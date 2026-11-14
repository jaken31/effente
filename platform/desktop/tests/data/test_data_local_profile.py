"""The local profile (contract section 10.5): a name and a role kept on this laptop,
in the store from app/settings.py, that fills `by`. No password."""

import getpass
import inspect

import pytest

pytest.importorskip("PySide6")

from app.data.setup import NAME_KEY, NO_NAME, ROLE_KEY, Setup
from PySide6.QtCore import QSettings

UNUSED_URL = "http://127.0.0.1:9"  # never started: these tests make no request


@pytest.fixture
def ini(tmp_path):
    return str(tmp_path / "effente.ini")


def store(ini):
    return QSettings(ini, QSettings.Format.IniFormat)


@pytest.fixture
def make(qapp):
    from app.data.state import DashboardState

    return lambda settings=None: DashboardState(UNUSED_URL, settings=settings)


def test_none_until_one_is_saved(make, ini):
    assert make(store(ini)).setup.local_profile() is None


def test_kept_in_the_store_and_back_on_the_next_run(make, ini, qtbot):
    state = make(store(ini))
    with qtbot.waitSignal(state.setup.local_profile_changed):
        state.setup.set_local_profile("  Sam ", " technician ")
    assert state.setup.local_profile() == {"name": "Sam", "role": "technician"}
    again = store(ini)
    assert (again.value(NAME_KEY), again.value(ROLE_KEY)) == ("Sam", "technician")
    assert make(store(ini)).setup.local_profile() == {"name": "Sam", "role": "technician"}


def test_the_role_may_be_left_out(make, ini):
    state = make(store(ini))
    state.setup.set_local_profile("Sam", "")
    assert state.setup.local_profile() == {"name": "Sam", "role": ""}


def test_a_name_is_needed(make, ini):
    state = make(store(ini))
    failed = []
    state.action_failed.connect(failed.append)
    state.setup.set_local_profile("   ", "technician")
    assert failed == [NO_NAME]
    assert state.setup.local_profile() is None


def test_without_a_store_it_lasts_only_this_run(make):
    state = make()
    state.setup.set_local_profile("Sam", "technician")
    assert state.setup.local_profile() == {"name": "Sam", "role": "technician"}
    assert make().setup.local_profile() is None


def test_by_is_the_name_and_the_role_is_not_sent(make, ini, monkeypatch):
    monkeypatch.setenv("EFFENTE_ANSWERER", "Alex")
    state = make(store(ini))
    assert state.answerer() == "Alex"  # none set: as before
    state.setup.set_local_profile("Sam", "technician")
    assert state.answerer() == "Sam"


def test_with_none_set_by_falls_back_to_the_login_name(make, monkeypatch):
    monkeypatch.delenv("EFFENTE_ANSWERER", raising=False)
    assert make().answerer() == getpass.getuser()


def test_no_password():
    assert list(inspect.signature(Setup.set_local_profile).parameters) == ["self", "name", "role"]


def test_profile_known_only_once_a_snapshot_carries_profiles(make):
    setup = make().setup
    setup.apply_snapshot({"machines": []})
    assert not setup.profile_known()
    setup.apply_snapshot({"profiles": {}})
    assert setup.profile_known()
