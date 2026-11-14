"""The Machine page's logic (app/pages/machine/logic.py): plain Python, no window, no graphics card."""

from datetime import datetime, timezone

import pytest
from app.pages.machine import logic

PALETTE = {"red": "#ff4d2e", "text": "#ededed", "muted": "#878787", "bg": "#0a0a0a"}
NOW = datetime(2026, 11, 14, 21, 2, 11, tzinfo=timezone.utc)
SCAN = {"scan_id": "scan-20261114181502-3fa9", "machine_id": "conveyor-07",
        "model": {"status": "ready", "reason": "", "bytes": 9120344,
                  "bounds": {"min": [-0.21, 0.0, -0.09], "max": [0.21, 0.14, 0.09]}}}
OLD_SCAN = "scan-20261101090000-aaaa"


def part(part_id, name, state, since=None, at=None, scan_id=SCAN["scan_id"], removed=False):
    return {"part_id": part_id, "machine_id": "conveyor-07", "name": name, "kind": "roller", "removed": removed,
            "position": {"scan_id": scan_id, "at": at} if at else None,
            "state": {"name": state, "since": since, "reason": "", "alarm_id": None, "entry_id": None}}


# The plan's sample data (7.3): four parts, one in each state, the last with no pin.
PARTS = [
    part("part-0001", "Drive roller", "needs_repair", "2026-11-14T19:02:11Z", [0.18, 0.06, 0.0]),
    part("part-0002", "Motor mount", "possible", "2026-11-14T20:22:11Z", [-0.15, 0.02, 0.05]),
    part("part-0003", "Belt", "nothing_found", "2026-11-14T18:30:00Z", [0.0, 0.14, 0.0]),
    part("part-0004", "Idle roller", "not_checked"),
]


# ---- how a state looks and reads -------------------------------------------------------------------------


def test_every_state_has_its_own_look():
    looks = {state: tuple(sorted(logic.pin_look(state, PALETTE).items())) for state in logic.STATES}
    assert len(set(looks.values())) == 4


def test_red_only_marks_the_two_states_that_point_at_a_problem():
    for state in logic.STATES:
        look = logic.pin_look(state, PALETTE)
        uses_red = PALETTE["red"] in (look["colour"], look["ring"])
        assert uses_red == (state in ("needs_repair", "possible")), state


def test_a_suspect_is_drawn_hollow_and_a_confirmed_fault_solid():
    assert logic.pin_look("needs_repair", PALETTE)["colour"] == PALETTE["red"]
    assert logic.pin_look("possible", PALETTE)["colour"] == PALETTE["bg"]
    assert logic.pin_look("possible", PALETTE)["ring"] == PALETTE["red"]


def test_no_state_is_drawn_in_a_colour_that_reads_as_good():
    for state in logic.STATES:
        for colour in logic.pin_look(state, PALETTE).values():
            assert colour in PALETTE.values()  # nothing but the theme's own four: no green


def test_an_unknown_state_is_shown_as_not_checked():
    odd = part("part-0009", "Thing", "brand_new_state")
    assert logic.state_name(odd) == "not_checked"
    assert logic.pin_look("brand_new_state", PALETTE) == logic.pin_look("not_checked", PALETTE)
    assert logic.state_name({"part_id": "x"}) == "not_checked"


@pytest.mark.parametrize("since, text", [
    ("2026-11-14T21:02:00Z", "under 1 min"),
    ("2026-11-14T20:22:11Z", "40 min"),
    ("2026-11-14T19:02:11Z", "2 h"),
    ("2026-11-11T19:02:11Z", "3 d"),
    ("2026-11-14T22:00:00Z", "under 1 min"),  # a clock a little ahead is not shown as a negative time
    (None, ""), ("", ""), ("not a time", ""),
])
def test_ago(since, text):
    assert logic.ago(since, NOW) == text


def test_state_lines_use_the_plans_words():
    assert [logic.state_line(p, NOW) for p in PARTS] == \
        ["needs repair, 2 h", "possible, 40 min", "nothing found", "not checked"]
    assert "healthy" not in " ".join(logic.STATE_WORDS.values())


# ---- pins ------------------------------------------------------------------------------------------------


def test_one_pin_per_part_with_a_position_on_this_scan():
    pins = logic.pins(PARTS, SCAN, None, PALETTE)
    assert [p["id"] for p in pins] == ["part-0001", "part-0002", "part-0003"]
    assert pins[0] == {"id": "part-0001", "x": 0.18, "y": 0.06, "z": 0.0, "colour": PALETTE["red"],
                       "ring": PALETTE["text"], "selected": False, "label": "Drive roller"}


def test_the_selected_pin_is_drawn_last_so_it_is_on_top():
    pins = logic.pins(PARTS, SCAN, "part-0001", PALETTE)
    assert [p["id"] for p in pins] == ["part-0002", "part-0003", "part-0001"]
    assert [p["selected"] for p in pins] == [False, False, True]


def test_no_pins_without_a_scan_for_removed_parts_or_for_another_scans_positions():
    assert logic.pins(PARTS, None, None, PALETTE) == []
    others = [part("part-0005", "Gone", "possible", at=[0, 0, 0], removed=True),
              part("part-0006", "Old", "possible", at=[0, 0, 0], scan_id=OLD_SCAN),
              part("part-0007", "Broken", "possible", at=[0, "x", 0]),
              part("part-0008", "Short", "possible", at=[0, 0])]
    assert logic.pins(others, SCAN, None, PALETTE) == []


# ---- the parts list --------------------------------------------------------------------------------------


def test_rows_in_the_order_the_parts_were_made():
    rows = logic.rows(PARTS, SCAN, "part-0002", PALETTE, NOW)
    assert [(r["name"], r["line"], r["note"], r["action"], r["selected"]) for r in rows] == [
        ("Drive roller", "needs repair, 2 h", "", "", False),
        ("Motor mount", "possible, 40 min", "", "", True),
        ("Belt", "nothing found", "", "", False),
        ("Idle roller", "not checked", "no pin", "place", False),
    ]


def test_a_part_pinned_on_an_older_scan_can_be_placed_on_this_one():
    old = part("part-0006", "Old roller", "nothing_found", at=[0.1, 0.1, 0.1], scan_id=OLD_SCAN)
    row = logic.rows([old], SCAN, None, PALETTE, NOW)[0]
    assert (row["note"], row["action"]) == ("no pin on this scan", "place")


def test_the_list_works_with_no_scan_and_hides_removed_parts():
    rows = logic.rows(PARTS + [part("part-0005", "Gone", "possible", removed=True)], None, None, PALETTE, NOW)
    assert [r["name"] for r in rows] == ["Drive roller", "Motor mount", "Belt", "Idle roller"]
    assert all(r["note"] == "" and r["action"] == "" for r in rows)  # nothing to place a pin on


# ---- what a click means ----------------------------------------------------------------------------------


def test_a_plain_click_on_the_model_moves_nothing():
    assert logic.on_model_click(logic.IDLE, SCAN, (0.1, 0.05, 0.0)) == (("nothing",), logic.IDLE)


def test_add_part_then_a_click_gives_the_new_parts_position():
    action, mode = logic.on_model_click(logic.adding(), SCAN, (0.123456789, 0.05, -0.0))
    assert action == ("new_part", {"scan_id": SCAN["scan_id"], "at": [0.12346, 0.05, 0.0]})
    assert mode == logic.IDLE


def test_place_then_a_click_moves_that_parts_pin():
    action, mode = logic.on_model_click(logic.placing("part-0004"), SCAN, (0.0, 0.14, 0.02))
    assert action == ("move_pin", "part-0004", {"scan_id": SCAN["scan_id"], "at": [0.0, 0.14, 0.02]})
    assert mode == logic.IDLE


def test_a_click_with_no_scan_or_a_broken_point_does_nothing_and_keeps_the_mode():
    assert logic.on_model_click(logic.adding(), None, (0, 0, 0)) == (("nothing",), logic.adding())
    assert logic.on_model_click(logic.adding(), SCAN, (float("nan"), 0, 0)) == (("nothing",), logic.adding())


def test_a_miss_while_adding_says_so_and_stays_in_the_mode():
    action, mode = logic.on_miss(logic.adding())
    assert action[0] == "say" and "missed" in action[1] and mode == logic.adding()
    assert logic.on_miss(logic.placing("part-0004"))[1] == logic.placing("part-0004")


def test_a_miss_otherwise_clears_the_selection():
    assert logic.on_miss(logic.IDLE) == (("select", None), logic.IDLE)


def test_a_click_on_a_pin_selects_it_and_ends_adding():
    assert logic.on_pin_click(logic.adding(), "part-0002") == (("select", "part-0002"), logic.IDLE)
    assert logic.on_pin_click(logic.IDLE, "part-0002") == (("select", "part-0002"), logic.IDLE)


def test_hints():
    assert logic.hint(logic.IDLE) == ""
    assert "new part" in logic.hint(logic.adding())
    assert "Idle roller" in logic.hint(logic.placing("part-0004"), "Idle roller")


# ---- the page's states -----------------------------------------------------------------------------------


def scan_with(status, reason=""):
    return dict(SCAN, model={"status": status, "reason": reason})


@pytest.mark.parametrize("scan, can_draw, problem, on_disk, name, show", [
    (None, True, "", True, "no_scan", False),
    (scan_with("waiting"), True, "", True, "arriving", False),
    (scan_with("converting"), True, "", True, "converting", False),
    (scan_with("failed", "Blender is not installed on this laptop"), True, "", True, "failed", False),
    (scan_with("something_new"), True, "", True, "failed", False),
    (SCAN, False, "", True, "cannot_draw", False),
    (SCAN, True, "the 3D view could not start on this laptop (no driver)", True, "cannot_draw", False),
    (SCAN, True, "", False, "fetching", False),
    (SCAN, True, "", True, "ready", True),
])
def test_every_state_shows_something(scan, can_draw, problem, on_disk, name, show):
    state = logic.view_state(scan, can_draw, problem, on_disk)
    assert (state["name"], state["show_model"]) == (name, show)
    assert show or (state["title"] and state["text"])  # never a blank page


def test_a_failed_conversion_shows_its_reason_and_a_retry():
    state = logic.view_state(scan_with("failed", "Blender is not installed on this laptop"), True)
    assert state["text"] == "Blender is not installed on this laptop" and state["retry"] is True
    assert logic.view_state(scan_with("failed"), True)["text"] == "No reason was given."
    assert logic.view_state(SCAN, True)["retry"] is False


def test_the_view_that_cannot_draw_shows_why():
    state = logic.view_state(SCAN, True, "the 3D view could not start on this laptop (no driver)")
    assert "no driver" in state["text"]


def test_the_empty_parts_panel_says_what_to_do():
    assert logic.parts_note(PARTS, SCAN) == ""
    assert "click the model" in logic.parts_note([], SCAN)
    assert "without a pin" in logic.parts_note([], None)
    assert "without a pin" in logic.parts_note([part("p", "Gone", "possible", removed=True)], scan_with("failed"))
