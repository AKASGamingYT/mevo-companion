"""Pull official Mevo APKs and refresh the local firmware library."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterator

from mevo_companion.firmware.apkmirror import APKMirrorError, download_mevo, latest_mevo_app
from mevo_companion.firmware.archive import (
    attach_changelog,
    camera_firmware_view,
    changelog_since,
    ingest_apk,
    latest_images,
    version_newer,
)
from mevo_companion.firmware.schedule import schedule_info
from mevo_companion.firmware.store import (
    load_catalog,
    load_state,
    repo_root,
    save_state,
    store_dir,
    utc_now,
)

log = logging.getLogger("mevo_companion.firmware")

_thread_lock = threading.Lock()
_thread: threading.Thread | None = None
_state_lock = threading.Lock()


class CheckInProgress(RuntimeError):
    pass


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def should_skip_check(last_success: str | None, now: datetime | None = None) -> bool:
    """Skip unless this is Sunday (once a day) or a missed-Sunday catch-up.

    Logon can fire any day. The scheduled task also fires Sunday morning.
    After a successful check we wait for the next Sunday, unless the last
    success is 8+ days old (PC was off last Sunday). Weekday is local time.
    """
    if not last_success:
        return False
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        last = parse_iso(last_success).astimezone(now.tzinfo)
    except ValueError:
        return False
    if now - last < timedelta(hours=12):
        return True
    if now.weekday() == 6:
        return last.date() == now.date()
    return now - last < timedelta(days=8)


def seed_paths() -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    roots = [repo_root(), repo_root() / "incoming", store_dir() / "incoming"]
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for pattern in ("*.apk", "*.apks"):
            for path in sorted(root.glob(pattern)):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                found.append(path)
    return found


# A check that stops writing progress is treated as dead so the UI can retry.
STALE_LOCK_SECONDS = 180


def _append_log(message: str) -> None:
    path = store_dir() / "check.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = utc_now()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        log.warning("could not append firmware check log")


def _lock_path() -> Path:
    return store_dir() / "check.lock"


def _lock_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip() or "0")
    except (OSError, ValueError):
        return None


def _lock_age(path: Path) -> float:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return STALE_LOCK_SECONDS + 1


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _foreign_check_running() -> bool:
    path = _lock_path()
    if not path.exists():
        return False
    pid = _lock_pid(path)
    if not pid or pid == os.getpid() or not _pid_alive(pid):
        return False
    return _lock_age(path) < STALE_LOCK_SECONDS


def _touch_lock() -> None:
    path = _lock_path()
    try:
        os.utime(path, None)
    except OSError:
        pass


def recover_stale_check() -> bool:
    """Clear a ``checking`` status left behind by a check that already exited."""
    state = load_state()
    if (state.get("status") or "") != "checking":
        return False
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return False
    if _foreign_check_running():
        return False
    _lock_path().unlink(missing_ok=True)
    _set_state(
        status="error",
        last_error="Firmware check stopped before it finished",
        message="Firmware check stopped before it finished. Try again.",
    )
    return True


def _set_state(**fields: Any) -> dict[str, Any]:
    with _state_lock:
        state = load_state()
        state.update(fields)
        save_state(state)
        return state


@contextmanager
def _check_lock() -> Iterator[None]:
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _foreign_check_running():
        raise CheckInProgress("Firmware check already running")
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise CheckInProgress("Firmware check already running") from exc
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise CheckInProgress("Firmware check already running") from exc
    try:
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)
        path.unlink(missing_ok=True)


def _job_for_camera(camera: Any) -> dict[str, Any] | None:
    from mevo_companion.firmware.push import job_for

    ids = [getattr(camera, "id", None), *list(getattr(camera, "aliases", []) or [])]
    for cam_id in ids:
        if not cam_id:
            continue
        job = job_for(str(cam_id))
        if job:
            return job
    return None


def public_status(registry: Any | None = None, *, light: bool = False) -> dict[str, Any]:
    recover_stale_check()
    catalog = load_catalog()
    state = load_state()
    raw_images = latest_images(catalog)
    images = raw_images if light else [attach_changelog(image) for image in raw_images]
    by_family = {str(image.get("family") or ""): image for image in images}
    cameras = []
    seen: set[str] = set()
    if registry is not None:
        from mevo_companion.firmware.push import all_jobs, camera_indicator

        jobs = all_jobs()
        for camera in registry.list():
            view = camera_firmware_view(camera, catalog)
            family = str(view.get("family") or "")
            image = by_family.get(family) or {}
            if light:
                view["changelog"] = []
            else:
                view["changelog"] = changelog_since(
                    image.get("changelog") or [],
                    view.get("current"),
                    family,
                )
            job = _job_for_camera(camera)
            view["aliases"] = list(getattr(camera, "aliases", []) or [])
            powered_off = bool(getattr(camera, "powered_off", False))
            online = not bool(getattr(camera, "offline", False))
            if job:
                view["job"] = _public_job(
                    job,
                    online=online,
                    current=view.get("current"),
                    powered_off=powered_off,
                )
                phase = view["job"].get("phase")
                status = view["job"].get("status")
                if status in {"queued", "updating"} or phase in {"rebooting", "complete"}:
                    view["update"] = False
            view["offline"] = not online
            view["powered_off"] = powered_off
            if view["offline"]:
                view["update"] = False
            view["indicator"] = camera_indicator(view)
            cameras.append(view)
            if camera.id:
                seen.add(camera.id)
            seen.update(str(alias) for alias in (getattr(camera, "aliases", []) or []) if alias)
        known_names = {
            str(getattr(camera, "name", "") or "")
            for camera in registry.list()
            if getattr(camera, "name", None)
        }
        friendly_names = {
            str((getattr(camera, "txt", None) or {}).get("camera_name") or "")
            for camera in registry.list()
        }
        friendly_names.discard("")
        for cam_id, job in jobs.items():
            job_name = str(job.get("name") or "")
            job_label = str(job.get("label") or "")
            if cam_id in seen or job_name in known_names or job_label in friendly_names:
                continue
            family = str(job.get("family") or "start")
            image = by_family.get(family) or {}
            view = {
                "id": cam_id,
                "name": job.get("label") or job.get("name") or cam_id,
                "family": family,
                "label": job.get("product") or image.get("label") or family,
                "current": job.get("to_version") or job.get("from_version"),
                "available": job.get("to_version") or image.get("version"),
                "update": False,
                "offline": True,
                "powered_off": True,
                "changelog": []
                if light
                else changelog_since(
                    image.get("changelog") or [],
                    job.get("from_version"),
                    family,
                ),
                "job": _public_job(job, online=False, current=None, powered_off=True),
            }
            view["indicator"] = camera_indicator(view)
            cameras.append(view)
    return {
        "status": state.get("status") or "idle",
        "message": state.get("message"),
        "last_started": state.get("last_started"),
        "last_success": state.get("last_success"),
        "last_error": state.get("last_error"),
        "skipped": bool(state.get("skipped")),
        "store": str(store_dir()),
        "app": catalog.get("app"),
        "images": images,
        "cameras": cameras,
        "schedule": None if light else schedule_info(),
    }


def _public_job(
    job: dict[str, Any],
    *,
    online: bool,
    current: str | None,
    powered_off: bool = False,
) -> dict[str, Any]:
    from mevo_companion.firmware.push import job_percent

    view = dict(job)
    status = view.get("status")
    target = view.get("to_version")
    if status == "done":
        if online and current and current == target:
            view["message"] = f"Updated to {target}"
            view["phase"] = "complete"
        elif powered_off or (not online and not _job_recent(job)):
            view["message"] = f"Updated to {target}" if target else view.get("message")
            view["phase"] = "complete"
        elif not online:
            view["message"] = f"Updated to {target}. Waiting for camera to return."
            view["phase"] = "rebooting"
        elif online and current and current != target and _job_recent(job):
            view["message"] = f"Updated to {target}. Waiting for camera to advertise {target}."
            view["phase"] = "rebooting"
        elif online and current and current != target:
            view["phase"] = "stale"
    view["progress"] = job_percent(view)
    return view


def _job_recent(job: dict[str, Any], minutes: int = 20) -> bool:
    stamp = job.get("finished_at") or job.get("started_at")
    if not stamp:
        return True
    try:
        parsed = parse_iso(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return True
    now = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return now - parsed <= timedelta(minutes=minutes)


def run_check(*, force: bool = False, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    def note(message: str) -> None:
        _touch_lock()
        _append_log(message)
        log.info(message)
        try:
            _set_state(message=message)
        except OSError:
            log.warning("could not update firmware check status")
        if progress:
            try:
                progress(message)
            except Exception:
                log.exception("firmware check progress callback failed")
        print(message, flush=True)

    entered = False
    try:
        with _check_lock():
            entered = True
            return _run_check_locked(force=force, note=note)
    except CheckInProgress as exc:
        _set_state(message=str(exc))
        return {"ok": True, "skipped": True, "reason": str(exc)}
    finally:
        if entered:
            state = load_state()
            if state.get("status") == "checking":
                _set_state(
                    status="error",
                    last_error=state.get("last_error") or "Firmware check stopped before it finished",
                    message="Firmware check stopped before it finished. Try again.",
                )


def _run_check_locked(*, force: bool, note: Callable[[str], None]) -> dict[str, Any]:
    state = load_state()
    if not force and should_skip_check(state.get("last_success")):
        message = "Already checked this Sunday cycle"
        note(message)
        _set_state(status="idle", skipped=True, message=message)
        return {"ok": True, "skipped": True, "reason": message}

    _set_state(
        status="checking",
        last_started=utc_now(),
        skipped=False,
        last_error=None,
        message="Starting firmware check",
    )
    ingested: list[dict[str, Any]] = []
    downloaded: dict[str, str] | None = None
    try:
        for path in seed_paths():
            note(f"Ingesting {path.name}…")
            ingested.append(ingest_apk(path, source="seed", app_version=_guess_app_version(path)))

        catalog = load_catalog()
        current_app = (catalog.get("app") or {}).get("version")
        try:
            note("Checking APKMirror…")
            latest = latest_mevo_app()
            note(f"APKMirror latest stable app is {latest.version}")
            dest_dir = store_dir() / "downloads"
            existing = None if force else _existing_download(dest_dir, latest.version)
            need_app = force or not current_app or version_newer(latest.version, current_app)
            if not need_app:
                note(f"Library already has app {current_app}; APKMirror {latest.version} is not newer")
            elif existing is not None:
                note(f"Ingesting cached {existing.name}…")
                ingested.append(
                    ingest_apk(
                        existing,
                        app_version=latest.version,
                        source="apkmirror",
                        source_url=latest.url,
                    )
                )
            else:
                downloaded = download_mevo(dest_dir, progress=note, selected=latest)
                ingested.append(
                    ingest_apk(
                        Path(downloaded["path"]),
                        app_version=downloaded.get("version") or latest.version,
                        source="apkmirror",
                        source_url=latest.url,
                        version_code=downloaded.get("version_code"),
                    )
                )
        except APKMirrorError as exc:
            if ingested:
                note(f"APKMirror unavailable ({exc}); kept local library")
            else:
                raise

        images = latest_images()
        summary = ", ".join(
            f"{item.get('label') or item.get('family')} {item.get('version')}"
            for item in images
        ) or "no firmware images"
        message = f"Firmware library ready: {summary}"
        note(message)
        _set_state(status="idle", last_success=utc_now(), last_error=None, message=message)
        return {
            "ok": True,
            "skipped": False,
            "ingested": ingested,
            "downloaded": downloaded,
            "images": images,
        }
    except Exception as exc:
        message = str(exc)
        note(f"Firmware check failed: {message}")
        _set_state(status="error", last_error=message, message=message)
        raise


def _guess_app_version(path: Path) -> str | None:
    name = path.stem.lower().replace("_", "-")
    if name.startswith("mevo-"):
        tail = name.split("mevo-", 1)[1]
        if tail and tail[0].isdigit():
            return tail
    return None


def _existing_download(dest_dir: Path, version: str) -> Path | None:
    if not dest_dir.exists():
        return None
    matches = sorted(dest_dir.glob(f"mevo-{version}.*"))
    matches = [path for path in matches if path.suffix.lower() in {".apk", ".apks"}]
    return matches[0] if matches else None


def start_background_check(*, force: bool = True) -> bool:
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return False

        def target() -> None:
            try:
                run_check(force=force)
            except Exception:
                log.exception("firmware check failed")

        _thread = threading.Thread(target=target, daemon=True, name="firmware-check")
        _thread.start()
        return True
