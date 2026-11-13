"""The RED rule: no widget ever paints text in RED (or its family).

Walks the widget tree and reads every text colour a widget can get: its palette's
text roles, the app stylesheet, its own stylesheet and any rich text."""

import re

import pytest
from app.theme import tokens as t
from app.widgets import (
    Button,
    DataTable,
    FlagValue,
    KpiStrip,
    Panel,
    Pill,
    Segmented,
    SimChip,
    Sparkline,
    StatusDot,
    Toggle,
)
from PySide6.QtGui import QColor, QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

TEXT_ROLES = [
    QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
    QPalette.ColorRole.BrightText, QPalette.ColorRole.HighlightedText, QPalette.ColorRole.ToolTipText,
    QPalette.ColorRole.PlaceholderText, QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited,
]
GROUPS = [QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled]
# color / selection-color declarations, not background-color or border-color.
CSS_COLOR = re.compile(r"(?<![\w-])(?:selection-)?color\s*:\s*([^;}]+)", re.IGNORECASE)
HTML_COLOR = re.compile(r"""(?:color\s*[:=]\s*["']?)(#[0-9a-f]{3,8}|rgba?\([^)]*\))""", re.IGNORECASE)


def parse(value: str) -> QColor | None:
    v = value.strip().rstrip(";").strip()
    m = re.match(r"rgba?\(([^)]*)\)", v)
    if m:
        r, g, b = (int(float(x)) for x in m.group(1).split(",")[:3])
        return QColor(r, g, b)
    c = QColor(v)
    return c if c.isValid() else None


def red_text(widget: QWidget) -> list[str]:
    found = []
    pal = widget.palette()
    for group in GROUPS:
        for role in TEXT_ROLES:
            if t.is_red(pal.color(group, role)):
                found.append(f"palette {role.name}")
    for source, sheet in (("own stylesheet", widget.styleSheet()), ("app stylesheet", QApplication.instance().styleSheet())):
        for value in CSS_COLOR.findall(sheet):
            c = parse(value)
            if c is not None and t.is_red(c):
                found.append(f"{source}: color {value.strip()}")
    if isinstance(widget, QLabel):
        for value in HTML_COLOR.findall(widget.text()):
            c = parse(value)
            if c is not None and t.is_red(c):
                found.append(f"rich text colour {value}")
    return found


def assert_no_red_text(root: QWidget) -> None:
    bad = {}
    for w in [root, *root.findChildren(QWidget)]:
        problems = red_text(w)
        if problems:
            bad[f"{type(w).__name__} {w.objectName()!r}"] = sorted(set(problems))
    assert not bad, f"red text: {bad}"


def gallery() -> QWidget:
    """Every shared widget, in its red-carrying states."""
    root = QWidget()
    lay = QVBoxLayout(root)
    strip = KpiStrip(["Abnormal for", "Since"])
    strip.cell(0).set_value("34 periods", abnormal=True)
    strip.cell(1).set_meter(0.5)
    panel = Panel("Signals", "hint", link=True)
    panel.header_end.addWidget(Segmented(["Low", "Balanced", "High"], current="High"))
    model = QStandardItemModel(2, 3)
    model.setItem(0, 0, QStandardItem("Alarm score"))
    flagged = QStandardItem("2.85x")
    flagged.setData(True, DataTable.FlagRole)
    model.setItem(0, 1, flagged)
    pill = QStandardItem()
    pill.setData(("Possible unbalance", t.RED), DataTable.PillRole)
    model.setItem(0, 2, pill)
    table = DataTable(clickable=True)
    table.setModel(model)
    panel.add(table)
    for w in (strip, panel, Button("Run", "primary"), Button("Mark"), Button("Later", "ghost"),
              Toggle(True), Toggle(False), Pill("Possible unbalance", t.RED), FlagValue("4.3 mm/s", True),
              SimChip(), StatusDot(t.RED), Sparkline([1, 2, 3], 0, 4, 1)):
        lay.addWidget(w)
    return root


def test_window_has_no_red_text(window):
    for key in window.pages:
        window.navigate(key)
        assert_no_red_text(window)


def test_window_stale_and_badged_has_no_red_text(window, state):
    state.fresh = {"stale": True, "age_s": 9, "last_at": None}
    state.unanswered = [{"alarm_id": "a", "answer": None}]
    state.running = True
    window.refresh()
    assert_no_red_text(window)


def test_widgets_have_no_red_text(qtbot):
    root = gallery()
    qtbot.addWidget(root)
    root.show()
    assert_no_red_text(root)


def test_checker_catches_red_text(qtbot):
    label = QLabel("alarm")
    qtbot.addWidget(label)
    label.setStyleSheet(f"background-color: {t.RED}; color: {t.RED};")
    assert red_text(label) == [f"own stylesheet: color {t.RED}"]
    rich = QLabel(f'<span style="color:{t.ALERT}">x</span>')
    qtbot.addWidget(rich)
    assert red_text(rich)
    pal_label = QLabel("x")
    qtbot.addWidget(pal_label)
    pal = pal_label.palette()
    pal.setColor(QPalette.ColorRole.WindowText, QColor(t.RED))
    pal_label.setPalette(pal)
    assert red_text(pal_label)


@pytest.mark.parametrize("color", [t.RED, t.ALERT, t.PRIMARY_HOVER, "rgba(255, 77, 46, .5)"])
def test_text_color_guard(color):
    assert not t.is_red(t.text_color(color))
