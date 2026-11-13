"""Add effente to the desktop's app launcher (Linux and macOS).

    python3 platform/desktop/install_launcher.py            # add or refresh
    python3 platform/desktop/install_launcher.py --remove

Linux: writes a desktop entry and an icon under ~/.local/share (or $XDG_DATA_HOME).
macOS: writes two small apps into ~/Applications, "effente" and "effente (real
device)" (--no-sim), which Spotlight, Launchpad and the Dock can open.

Either way the launcher starts this checkout with the Python that ran this script,
so run it again if the repo moves or the Python changes.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
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


# macOS: an app is a folder. Ours holds a shell script that starts main.py, an
# Info.plist and the icon. There are no entry actions there, so the real-device
# start is a second app.
MAC_APPS = {  # app name -> (bundle id, extra arguments)
    "effente": ("com.effente.desktop", []),
    "effente (real device)": ("com.effente.desktop.real-device", ["--no-sim"]),
}
MAC_LOG = "$HOME/Library/Logs/effente.log"


def mac_apps_dir() -> Path:
    return Path.home() / "Applications"


def mac_script(python: str, extra: list[str], path: str) -> str:
    """The app's executable. An app opened from Finder gets a bare PATH and no
    terminal, so it takes the PATH of the shell that installed it (the Agents page
    looks for llama-server there) and keeps its output in a log file."""
    run = " ".join(shlex.quote(a) for a in [python, str(MAIN), *extra])
    return f"""#!/bin/sh
# Written by platform/desktop/install_launcher.py. Run that again instead of editing this.
export PATH={shlex.quote(path)}
cd {shlex.quote(str(HERE.parents[1]))} || exit 1
exec {run} >>"{MAC_LOG}" 2>&1
"""


def mac_plist(name: str, bundle_id: str) -> dict:
    return {
        "CFBundleName": name,
        "CFBundleDisplayName": name,
        "CFBundleIdentifier": bundle_id,
        "CFBundleExecutable": NAME,
        "CFBundleIconFile": NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": "0",
        "NSHighResolutionCapable": True,
    }


def mac_icon(dest: Path) -> bool:
    """Draw the svg into an .icns at `dest`. Needs PySide6 (the app does too) and
    macOS's iconutil. False when either is missing: the app then has the plain icon."""
    tool = shutil.which("iconutil")
    try:
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return False
    if not tool:
        return False
    renderer = QSvgRenderer(str(ICON))
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / f"{NAME}.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                size = points * scale
                image = QImage(size, size, QImage.Format.Format_ARGB32)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                margin = size * 0.1  # macOS icons sit inside their square
                renderer.render(painter, QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
                painter.end()
                image.save(str(iconset / f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"))
        done = subprocess.run([tool, "-c", "icns", "-o", str(dest), str(iconset)],
                              check=False, capture_output=True)
    return done.returncode == 0 and dest.exists()


def mac_install(python: str = sys.executable, path: str | None = None) -> list[Path]:
    """Write both apps, replacing ones already there. Returns the apps written."""
    path = os.environ.get("PATH", "/usr/bin:/bin") if path is None else path
    icon = None
    written = []
    for name, (bundle_id, extra) in MAC_APPS.items():
        app = mac_apps_dir() / f"{name}.app"
        if app.exists():
            shutil.rmtree(app)
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        with open(app / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump(mac_plist(name, bundle_id), f)
        script = app / "Contents" / "MacOS" / NAME
        script.write_text(mac_script(python, extra, path), encoding="utf-8")
        script.chmod(0o755)
        dest = app / "Contents" / "Resources" / f"{NAME}.icns"
        if icon is None:
            icon = dest if mac_icon(dest) else False
        elif icon:
            shutil.copyfile(icon, dest)
        written.append(app)
    return written


def mac_remove() -> list[Path]:
    """Delete both apps. Returns the ones that were there."""
    gone = [app for name in MAC_APPS if (app := mac_apps_dir() / f"{name}.app").exists()]
    for app in gone:
        shutil.rmtree(app)
    return gone


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remove", action="store_true", help="take effente out of the launcher")
    args = parser.parse_args(argv)
    mac = sys.platform == "darwin"
    if not mac and not sys.platform.startswith("linux"):
        print("This adds a Linux desktop entry or macOS apps. On other systems start "
              "platform/desktop/main.py by hand.", file=sys.stderr)
        return 1
    if args.remove:
        gone = mac_remove() if mac else remove()
        print("\n".join(f"removed {p}" for p in gone) or "effente was not in the launcher")
        return 0
    for path in (mac_install() if mac else install()):
        print(f"wrote {path}")
    if mac and not (mac_apps_dir() / "effente.app" / "Contents" / "Resources" / f"{NAME}.icns").exists():
        print("Warning: no icon was made (needs PySide6 and iconutil), so the apps have the plain one.",
              file=sys.stderr)
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print(f"Warning: {sys.executable} has no PySide6, so the entry will not start. "
              "Run this script with the Python you start effente with.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
