"""Remove the old weekly firmware task. The check now runs inside the companion."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from mevo_companion.firmware.store import repo_root

TASK_NAME = "MevoCompanionFirmwareCheck"
STARTUP_NAME = "MevoCompanionFirmwareCheck.vbs"
_CREATE_NO_WINDOW = 0x08000000


class ScheduleError(RuntimeError):
    pass


def check_bat() -> Path:
    return repo_root() / "check-firmware.bat"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ScheduleError("APPDATA is not set")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_script() -> Path:
    return startup_dir() / STARTUP_NAME


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    if os.name != "nt":
        return subprocess.CompletedProcess(["schtasks", *args], 1, "", "not windows")
    try:
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["schtasks", *args], 1, "", "timeout")


def schedule_info(*, force: bool = False) -> dict[str, object]:
    del force
    return {
        "installed": True,
        "name": "sign-in",
        "weekly": False,
        "startup": False,
        "triggers": ["Once after sign-in, when the network is connected"],
        "command": str(check_bat()),
    }


def retire_legacy_checker() -> None:
    """Delete the old weekly task and its sign-in script, without a console window."""
    _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    try:
        startup_script().unlink(missing_ok=True)
    except OSError as exc:
        raise ScheduleError(str(exc)) from exc


def install_task() -> dict[str, object]:
    retire_legacy_checker()
    return schedule_info()


def uninstall_task() -> dict[str, object]:
    retire_legacy_checker()
    return schedule_info()
