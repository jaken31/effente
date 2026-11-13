"""Add effente to the desktop's app launcher (Linux).

    python3 platform/desktop/install_launcher.py            # add or refresh
    python3 platform/desktop/install_launcher.py --remove

Writes a desktop entry and an icon under ~/.local/share (or $XDG_DATA_HOME). The
entry starts this checkout with the Python that ran this script, so run it again
if the repo moves or the Python changes.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"
ICON = HERE / "app" / "theme" / "effente.svg"
NAME = "effente"  # main.py gives the same name to setDesktopFileName, so windows match the entry


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def entry_path() -> Path:
    return data_home() / "applications" / f"{NAME}.desktop"


def icon_path() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{NAME}.svg"


def quoted(arg: str) -> str:
    """One Exec argument, quoted as the desktop entry spec asks."""
    for ch in ("\\", '"', "`", "$"):
        arg = arg.replace(ch, "\\" + ch)
    return f'"{arg}"'.replace("%", "%%")


def entry(python: str) -> str:
    run = f"{quoted(python)} {quoted(str(MAIN))}"
    return f"""[Desktop Entry]
Type=Application
Name=effente
Comment=Machine health console: receiver, simulated device and dashboard
Exec={run}
Path={HERE.parents[1]}
Icon={NAME}
Terminal=false
Categories=Utility;
StartupWMClass={NAME}
Actions=real-device;

[Desktop Action real-device]
Name=Real device (no simulator)
Exec={run} --no-sim
"""


def install(python: str = sys.executable) -> list[Path]:
    """Write the entry and the icon. Returns the files written."""
    for path in (entry_path(), icon_path()):
        path.parent.mkdir(parents=True, exist_ok=True)
    entry_path().write_text(entry(python), encoding="utf-8")
    shutil.copyfile(ICON, icon_path())
    _refresh()
    return [entry_path(), icon_path()]


def remove() -> list[Path]:
    """Delete the entry and the icon. Returns the files that were there."""
    gone = [p for p in (entry_path(), icon_path()) if p.exists()]
    for path in gone:
        path.unlink()
    _refresh()
    return gone


def _refresh() -> None:
    """Tell the desktop the entries changed, where the tool for it exists."""
    tool = shutil.which("update-desktop-database")
    if tool:
        subprocess.run([tool, str(entry_path().parent)], check=False, capture_output=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remove", action="store_true", help="take effente out of the launcher")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        print("This adds a Linux desktop entry. On other systems start platform/desktop/main.py by hand.",
              file=sys.stderr)
        return 1
    if args.remove:
        gone = remove()
        print("\n".join(f"removed {p}" for p in gone) or "effente was not in the launcher")
        return 0
    for path in install():
        print(f"wrote {path}")
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print(f"Warning: {sys.executable} has no PySide6, so the entry will not start. "
              "Run this script with the Python you start effente with.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
