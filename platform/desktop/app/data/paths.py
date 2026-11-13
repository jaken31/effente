"""Where the rest of the repo is, and importing platform/server and platform/sim.

platform/ is not a package (it would shadow the stdlib `platform`), and the
server and sim import their own modules by file name, so their folders go on
sys.path. Their module names (receiver_*, sim_*) do not clash.
"""

from __future__ import annotations

import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[2]  # platform/desktop
PLATFORM = DESKTOP.parent
REPO = PLATFORM.parent
SERVER_DIR = PLATFORM / "server"
SIM_DIR = PLATFORM / "sim"
WEB_DIR = PLATFORM / "web"
REPORT_SCRIPT = PLATFORM / "report.py"
DEFAULT_DATA_DIR = SERVER_DIR / "data"  # where platform/report.py looks for the device log
SAMPLE_REPLAY = SIM_DIR / "sample_replay.jsonl"


def _on_path(path: Path) -> None:
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


def server_modules():
    """platform/server's receiver_server module."""
    _on_path(SERVER_DIR)
    import receiver_server

    return receiver_server


def sim_modules():
    """platform/sim's (sim_device, sim_model, sim_heat) modules."""
    _on_path(SIM_DIR)
    import sim_device
    import sim_heat
    import sim_model

    return sim_device, sim_model, sim_heat
