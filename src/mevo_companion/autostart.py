"""Start Mevo Companion in the background at Windows sign-in."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mevo_companion.firmware.store import repo_root, store_dir

STARTUP_NAME = "MevoCompanion.vbs"


class AutostartError(RuntimeError):
    pass


def startup_dir() -> Path:
    override = os.environ.get("MEVO_COMPANION_STARTUP_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise AutostartError("APPDATA is not set")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_script() -> Path:
    return startup_dir() / STARTUP_NAME


def disabled_marker() -> Path:
    return store_dir() / "autostart.disabled"


def is_installed() -> bool:
    return startup_script().exists() and not disabled_marker().exists()


def launcher_exe() -> Path:
    root = repo_root()
    venv_w = root / ".venv" / "Scripts" / "pythonw.exe"
    if venv_w.exists():
        return venv_w
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        return pythonw
    return exe


def _vbs_escape(value: str) -> str:
    return value.replace('"', '""')


def _startup_vbs() -> str:
    py = _vbs_escape(str(launcher_exe()))
    cwd = _vbs_escape(str(repo_root()))
    return (
        "Set sh = CreateObject(\"Wscript.Shell\")\n"
        f"sh.CurrentDirectory = \"{cwd}\"\n"
        f"sh.Run \"\"\"{py}\"\" -m mevo_companion\", 0, False\n"
    )


def autostart_info() -> dict[str, object]:
    script = startup_script()
    return {
        "installed": is_installed(),
        "path": str(script),
        "disabled": disabled_marker().exists(),
        "command": f"{launcher_exe()} -m mevo_companion",
    }


def install_autostart() -> dict[str, object]:
    pythonw = launcher_exe()
    if not pythonw.exists():
        raise AutostartError(f"Missing launcher: {pythonw}")
    folder = startup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    startup_script().write_text(_startup_vbs(), encoding="utf-8")
    disabled_marker().unlink(missing_ok=True)
    info = autostart_info()
    if not info["installed"]:
        raise AutostartError("Could not install sign-in startup")
    return info


def uninstall_autostart() -> dict[str, object]:
    marker = disabled_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1\n", encoding="utf-8")
    try:
        startup_script().unlink(missing_ok=True)
    except OSError as exc:
        raise AutostartError(str(exc)) from exc
    return autostart_info()


def ensure_autostart() -> dict[str, object] | None:
    if disabled_marker().exists():
        return None
    return install_autostart()
