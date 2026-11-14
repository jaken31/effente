from app.shell import KEYS
from PySide6.QtCore import Qt


def test_starts_on_live(window):
    assert window.current == "live"
    assert window.sidebar.nav.items["live"].on
    assert window.topbar.title.text() == "Live"


def test_sidebar_click_routes(window, qtbot):
    for key in KEYS:
        qtbot.mouseClick(window.sidebar.nav.items[key], Qt.MouseButton.LeftButton)
        assert window.current == key
        assert window.stack.currentIndex() == KEYS.index(key)
        assert [k for k, i in window.sidebar.nav.items.items() if i.on] == [key]
        assert window.topbar.title.text() == window.pages[key].title


def test_shortcuts_follow_interface_order(window, qtbot):
    assert KEYS == ["live", "spectrum", "thermal", "history", "evidence", "agent", "device", "machine", "log"]
    window.activateWindow()
    qtbot.waitActive(window)
    for i, key in enumerate(KEYS):
        qtbot.keyClick(window, getattr(Qt.Key, f"Key_{i + 1}"))
        assert window.current == key


def test_spectrum_and_thermal_get_a_live_crumb(window, qtbot):
    for key in ("spectrum", "thermal"):
        window.navigate(key)
        assert window.topbar.crumb.isVisible()
        assert window.sidebar.nav.items["live"].parent_on
        assert window.sidebar.nav.items[key].child
    qtbot.mouseClick(window.topbar.crumb, Qt.MouseButton.LeftButton)
    assert window.current == "live"
    assert not window.topbar.crumb.isVisible()
    assert not window.sidebar.nav.items["live"].parent_on


def test_page_navigate_signal_routes(window):
    window.pages["live"].navigate.emit("history")
    assert window.current == "history"


def test_unknown_key_is_ignored(window):
    window.navigate("history")
    window.navigate("nowhere")
    assert window.current == "history"


def test_placeholder_pages_show_their_titles(window):
    for key, page in window.pages.items():
        assert page.key == key
        assert page.title in [w.text() for w in page.findChildren(type(window.topbar.title))]


def test_machine_picker_sets_state(window, state):
    picker = window.sidebar.machine
    assert picker.currentText() == state.current_machine()
    picker.setCurrentIndex(1)
    assert state.machine_calls == ["conveyor-08"]
    assert window.topbar.machine.text() == "conveyor-08"


def test_minimum_size_fits_half_a_screen(window):
    assert window.minimumSize().width() == 960
    assert window.minimumSize().height() == 660
    # The sidebar and top bar lay out cleanly at that size.
    hint = window.centralWidget().minimumSizeHint()
    assert hint.width() <= 960
    assert hint.height() <= 660


def test_opens_at_the_mockup_size(qtbot, state):
    from app.shell import MainWindow

    w = MainWindow(state, placeholders=True)
    qtbot.addWidget(w)
    assert (w.width(), w.height()) == (1440, 900)


def test_narrow_top_bar_drops_the_subtitle_not_the_names(window, qtbot):
    bar = window.topbar
    window.navigate("thermal")
    assert bar.subtitle.isVisible()
    window.resize(960, 660)
    qtbot.waitUntil(lambda: window.width() == 960)
    assert not bar.subtitle.isVisible()
    for label in (bar.machine, bar.crumb, bar.title, bar.fresh):
        assert label.width() >= label.sizeHint().width()
    window.resize(1600, 1000)
    qtbot.waitUntil(bar.subtitle.isVisible)


def test_page_scrollbar_takes_no_content_width(window, qtbot):
    from PySide6.QtWidgets import QWidget

    area = window.stack.currentWidget()
    tall = QWidget()
    tall.setMinimumHeight(3000)
    window.pages["live"].layout().addWidget(tall)
    qtbot.waitUntil(area.overlay.isVisible)
    page = window.pages["live"]
    right = page.mapTo(window, page.rect().topRight()).x()
    assert right == window.width() - 24 - 1
    area.overlay.setValue(200)
    assert area.verticalScrollBar().value() == 200
