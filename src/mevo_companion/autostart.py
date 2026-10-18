"""Start Mevo Companion in the background at sign-in."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mevo_companion.firmware.store import repo_root, store_dir
from mevo_companion.runtime import is_frozen

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
    if sys.platform != "win32":
        return _posix_startup_path()
    return startup_dir() / STARTUP_NAME


def _posix_startup_path() -> Path:
    override = os.environ.get("MEVO_COMPANION_STARTUP_DIR")
    if sys.platform == "darwin":
        folder = Path(override) if override else Path.home() / "Library" / "LaunchAgents"
        return folder / "com.mevo-companion.plist"
    folder = Path(override) if override else _xdg_config_dir() / "autostart"
    return folder / "mevo-companion.desktop"


def _xdg_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else Path.home() / ".config"


def disabled_marker() -> Path:
    return store_dir() / "autostart.disabled"


def is_installed() -> bool:
    return startup_script().exists() and not disabled_marker().exists()


def launcher_exe() -> Path:
    if is_frozen():
        return Path(sys.executable)
    root = repo_root()
    venv_w = root / ".venv" / "Scripts" / "pythonw.exe"
    if venv_w.exists():
        return venv_w
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        return pythonw
    return exe


def command_args() -> list[str]:
    if is_frozen():
        return [str(Path(sys.executable))]
    return [str(launcher_exe()), "-m", "mevo_companion"]


def _vbs_escape(value: str) -> str:
    return value.replace('"', '""')


def _startup_vbs() -> str:
    if is_frozen():
        exe = _vbs_escape(str(Path(sys.executable)))
        return (
            "Set sh = CreateObject(\"Wscript.Shell\")\n"
            f"sh.Run \"\"\"{exe}\"\"\", 0, False\n"
        )
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
        "command": " ".join(command_args()),
    }


def install_autostart() -> dict[str, object]:
    if sys.platform != "win32":
        return _install_posix()
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


def _xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _shell_quote(value: str) -> str:
    if any(char in value for char in " \"\\"):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _macos_plist() -> str:
    args = "\n".join(f"    <string>{_xml(arg)}</string>" for arg in command_args())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key>\n"
        "  <string>com.mevo-companion</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        f"{args}\n"
        "  </array>\n"
        "  <key>RunAtLoad</key>\n"
        "  <true/>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _linux_desktop() -> str:
    command = " ".join(_shell_quote(arg) for arg in command_args())
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Mevo Companion\n"
        "Comment=Control Mevo cameras on your network\n"
        f"Exec={command}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _install_posix() -> dict[str, object]:
    target = launcher_exe()
    if not target.exists():
        raise AutostartError(f"Missing launcher: {target}")
    path = startup_script()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        path.write_text(_macos_plist(), encoding="utf-8")
    else:
        path.write_text(_linux_desktop(), encoding="utf-8")
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
