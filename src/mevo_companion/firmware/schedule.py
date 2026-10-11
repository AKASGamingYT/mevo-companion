"""Sunday firmware checker: weekly scheduled task plus sign-in startup."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from mevo_companion.firmware.store import repo_root

TASK_NAME = "MevoCompanionFirmwareCheck"
STARTUP_NAME = "MevoCompanionFirmwareCheck.vbs"
_CACHE_TTL = 60.0
_cache_lock = threading.Lock()
_cache: tuple[float, dict[str, object]] | None = None


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
    try:
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["schtasks", *args], 1, "", "timeout")


def _task_exists(name: str) -> bool:
    return _run_schtasks(["/Query", "/TN", name]).returncode == 0


def task_installed() -> bool:
    return _task_exists(TASK_NAME) and startup_script().exists()


def schedule_info(*, force: bool = False) -> dict[str, object]:
    global _cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _cache is not None and now - _cache[0] < _CACHE_TTL:
            return dict(_cache[1])
        info = _schedule_info_uncached()
        _cache = (time.monotonic(), info)
        return dict(info)


def _schedule_info_uncached() -> dict[str, object]:
    weekly = _task_exists(TASK_NAME)
    startup = startup_script().exists()
    info: dict[str, object] = {
        "installed": weekly and startup,
        "name": TASK_NAME,
        "weekly": weekly,
        "startup": startup,
        "startup_path": str(startup_script()),
        "triggers": ["At sign-in", "Weekly Sunday 08:00"],
        "command": str(check_bat()),
    }
    result = _run_schtasks(["/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    if result.returncode == 0:
        info["query"] = (result.stdout or "").strip()[:2000]
    return info


def _create_weekly() -> None:
    bat = check_bat()
    if not bat.exists():
        raise ScheduleError(f"Missing {bat.name}; cannot register the weekly checker")
    if _task_exists(TASK_NAME):
        return
    result = _run_schtasks(
        [
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            str(bat),
            "/SC",
            "WEEKLY",
            "/D",
            "SUN",
            "/ST",
            "08:00",
            "/RL",
            "LIMITED",
            "/F",
        ]
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "schtasks failed").strip()
        raise ScheduleError(detail)


def _install_startup() -> None:
    bat = check_bat()
    if not bat.exists():
        raise ScheduleError(f"Missing {bat.name}; cannot register the sign-in checker")
    folder = startup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    # Hidden window at sign-in; the checker itself no-ops unless it's Sunday (or catch-up).
    startup_script().write_text(
        "Set sh = CreateObject(\"Wscript.Shell\")\r\n"
        f"sh.Run \"\"\"{bat}\"\"\", 0, False\r\n",
        encoding="utf-8",
    )


def install_task() -> dict[str, object]:
    _create_weekly()
    _install_startup()
    info = schedule_info(force=True)
    if not info["installed"]:
        raise ScheduleError("Could not install the Sunday checker")
    return info


def uninstall_task() -> dict[str, object]:
    errors: list[str] = []
    if _task_exists(TASK_NAME):
        result = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
        if result.returncode != 0:
            errors.append((result.stderr or result.stdout or TASK_NAME).strip())
    script = startup_script()
    try:
        script.unlink(missing_ok=True)
    except OSError as exc:
        errors.append(str(exc))
    if errors:
        raise ScheduleError("; ".join(errors))
    return schedule_info(force=True)
