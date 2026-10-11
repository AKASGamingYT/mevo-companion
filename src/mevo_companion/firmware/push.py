"""Push library firmware to cameras the way the official app does.

APK flow (CommandReceiveFirmware / CommandUpdateFirmware / UploadFwToMevoUseCase):

1. ``receive_firmware`` with ``force``, ``sha512``, ``signature``
2. Camera replies with an HTTP ``url`` (typically ``/upload/mevo_fw.tar.xz``)
3. App PUTs the ``.tar.xz`` as ``application/octet-stream``
4. ``update_firmware`` with the same ``sha512`` / ``signature``
5. Camera flashes, emits ``firmware_update_finished_notification``, then reboots
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from mevo_companion.firmware.archive import latest_for_family, version_newer
from mevo_companion.firmware.store import store_dir, utc_now
from mevo_companion.protocol import DEFAULT_UPLOAD_PATH, CameraSession

PHASE_PROGRESS = {
    "queued": 2,
    "preparing": 8,
    "uploading": 15,
    "updating": 70,
    "flashing": 85,
    "rebooting": 92,
    "complete": 100,
}
UPLOAD_PROGRESS_START = 15
UPLOAD_PROGRESS_END = 80

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_threads: dict[str, threading.Thread] = {}


class FirmwarePushError(RuntimeError):
    pass


def friendly_progress(raw: str) -> tuple[str, str]:
    """Map internal push logs to the official app's firmware status copy."""
    text = (raw or "").strip()
    lower = text.lower()
    if lower.startswith("asking") or "receive firmware" in lower:
        return "Preparing firmware update.", "preparing"
    if (
        lower.startswith("put ")
        or lower.startswith("post ")
        or "upload url" in lower
        or "upload" in lower
    ):
        return "Uploading Mevo firmware. Please wait.", "uploading"
    if "flash" in lower or "applied" in lower:
        return "Updating Mevo firmware. Please wait.", "flashing"
    return text or "Updating Mevo firmware. Please wait.", "updating"


def job_percent(job: dict[str, Any] | None) -> int:
    """0–100 progress for Stream Deck keys and the web overlay."""
    if not job:
        return 0
    status = str(job.get("status") or "")
    phase = str(job.get("phase") or "")
    stored = job.get("progress")
    if status == "error":
        return max(0, min(100, int(stored or 0)))
    sent = job.get("bytes_sent")
    total = job.get("bytes_total")
    if phase == "uploading" and isinstance(sent, int) and isinstance(total, int) and total > 0:
        ratio = min(1.0, max(0.0, sent / total))
        return int(UPLOAD_PROGRESS_START + (UPLOAD_PROGRESS_END - UPLOAD_PROGRESS_START) * ratio)
    if phase in PHASE_PROGRESS:
        return PHASE_PROGRESS[phase]
    if status == "done":
        return 100
    if isinstance(stored, (int, float)):
        return max(0, min(100, int(stored)))
    return 0


def camera_indicator(view: dict[str, Any]) -> str:
    """latest (green), pending (orange), updating, error, offline, or unknown."""
    job = view.get("job") if isinstance(view.get("job"), dict) else {}
    status = str(job.get("status") or "")
    phase = str(job.get("phase") or "")
    if status == "error":
        return "error"
    if status in {"queued", "updating"}:
        return "updating"
    if view.get("powered_off"):
        return "offline"
    if phase == "rebooting":
        return "updating"
    if view.get("offline"):
        return "offline"
    if view.get("update"):
        return "pending"
    if view.get("available") or phase == "complete":
        return "latest"
    return "unknown"


class _ProgressBody:
    """httpx-iterable firmware blob that reports upload bytes and keeps Content-Length."""

    def __init__(self, data: bytes, on_progress: Callable[[int, int], None] | None = None) -> None:
        self._buf = io.BytesIO(data)
        self._total = len(data)
        self._on_progress = on_progress
        self._last_emit = 0.0
        self._last_pct = -1

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        chunk = self.read(65536)
        if not chunk:
            raise StopIteration
        return chunk

    def __len__(self) -> int:
        return self._total

    def read(self, n: int = -1) -> bytes:
        chunk = self._buf.read(n)
        if chunk and self._on_progress:
            pos = self._buf.tell()
            now = time.monotonic()
            pct = int(pos * 100 / self._total) if self._total else 100
            if pos >= self._total or pct >= self._last_pct + 2 or now - self._last_emit >= 0.3:
                self._last_emit = now
                self._last_pct = pct
                self._on_progress(pos, self._total)
        return chunk

    def tell(self) -> int:
        return self._buf.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._buf.seek(offset, whence)


def _jobs_path() -> Path:
    return store_dir() / "jobs.json"


def _load_persisted_jobs() -> dict[str, dict[str, Any]]:
    path = _jobs_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def job_for(cam_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(cam_id)
        return dict(job) if job else None


def all_jobs() -> dict[str, dict[str, Any]]:
    with _lock:
        return {key: dict(value) for key, value in _jobs.items()}


def _set_job(cam_id: str, *, persist: bool = True, **fields: Any) -> dict[str, Any]:
    with _lock:
        job = _jobs.setdefault(
            cam_id,
            {
                "camera_id": cam_id,
                "status": "idle",
                "message": None,
                "error": None,
                "started_at": None,
                "finished_at": None,
                "from_version": None,
                "to_version": None,
                "progress": 0,
                "phase": None,
                "bytes_sent": None,
                "bytes_total": None,
            },
        )
        job.update(fields)
        snapshot = dict(job)
        dump = {key: dict(value) for key, value in _jobs.items()}
    if persist:
        try:
            path = _jobs_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dump, indent=2), encoding="utf-8")
        except OSError:
            pass
    return snapshot


def resolve_upload_url(url: str | None, host: str) -> str:
    raw = (url or "").strip()
    if not raw or raw.startswith("file:"):
        return f"http://{host}{DEFAULT_UPLOAD_PATH}"
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.path:
        if parsed.hostname in {"127.0.0.1", "localhost"}:
            return f"{parsed.scheme}://{host}{parsed.path}"
        return raw
    if raw.startswith("/"):
        return f"http://{host}{raw}"
    return f"http://{host}{DEFAULT_UPLOAD_PATH}"


def upload_firmware_blob(
    url: str,
    blob: Path,
    progress: Callable[[str], None] | None = None,
    bytes_progress: Callable[[int, int], None] | None = None,
) -> str:
    if not blob.exists():
        raise FirmwarePushError(f"firmware blob missing: {blob}")
    log = progress or (lambda _msg: None)
    headers = {"Content-Type": "application/octet-stream"}
    last_error = "upload failed"
    with blob.open("rb") as handle:
        body = handle.read()
    for method in ("PUT", "POST"):
        log(f"{method} {url} ({blob.stat().st_size} bytes)")
        try:
            response = httpx.request(
                method,
                url,
                content=_ProgressBody(body, bytes_progress),
                headers=headers,
                timeout=httpx.Timeout(600.0, connect=15.0),
                follow_redirects=True,
                verify=False,
            )
        except httpx.HTTPError as exc:
            last_error = str(exc)
            continue
        if response.status_code in {404, 405, 501}:
            last_error = f"{method} {url} -> {response.status_code}"
            continue
        if response.status_code >= 400:
            raise FirmwarePushError(
                f"camera rejected firmware upload ({response.status_code}): {response.text[:240]}"
            )
        return method
    raise FirmwarePushError(last_error)


def _image_for_camera(camera: Any) -> dict[str, Any]:
    from mevo_companion.firmware.store import load_catalog

    catalog = load_catalog()
    family = camera_family(camera)
    image = latest_for_family(family, catalog)
    if image is None:
        raise FirmwarePushError(f"no firmware in the library for {family}")
    return image


def camera_family(camera: Any) -> str:
    from mevo_companion.firmware.archive import family_from_camera

    return family_from_camera(camera)


def current_version(camera: Any) -> str | None:
    from mevo_companion.firmware.archive import camera_fw_version

    return camera_fw_version(camera)


def push_camera(
    camera: Any,
    session: CameraSession,
    *,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
    bytes_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    log = progress or (lambda _msg: None)
    image = _image_for_camera(camera)
    current = current_version(camera)
    target = str(image.get("version") or "")
    if not force and current and not version_newer(target, current):
        raise FirmwarePushError(f"{camera.name} is already on {current}")
    blob = store_dir() / str(image["path"])
    sha = str(image.get("sha512") or "")
    signature = str(image.get("signature") or "")
    if not sha:
        raise FirmwarePushError("firmware image is missing sha512")

    log(f"Asking {camera.name} to receive firmware {target}…")
    receive = session.receive_firmware(sha, signature, force=True, timeout=30.0)
    upload_url = resolve_upload_url(str(receive.get("url") or ""), camera.host)
    log(f"Camera upload URL: {upload_url}")

    stop_ping = threading.Event()

    def _ping() -> None:
        while not stop_ping.wait(8.0):
            try:
                session.send_command("Ping", timeout=5.0)
            except Exception:
                return

    pinger = threading.Thread(target=_ping, daemon=True, name=f"fw-ping-{camera.id}")
    pinger.start()
    try:
        method = upload_firmware_blob(
            upload_url, blob, progress=log, bytes_progress=bytes_progress
        )
    finally:
        stop_ping.set()

    log("Telling camera to flash…")
    try:
        session.connect(timeout=10.0)
    except Exception:
        pass
    apply = session.apply_firmware(sha, signature, force=True, timeout=600.0)
    log(f"{camera.name} firmware {target} applied")
    return {
        "ok": True,
        "receive": receive,
        "upload_url": upload_url,
        "upload_method": method,
        "apply": apply,
        "from_version": current,
        "to_version": target,
    }


def start_updates(
    camera_ids: list[str],
    *,
    get_camera: Callable[[str], Any],
    get_session: Callable[[str], CameraSession],
    force: bool = False,
) -> list[dict[str, Any]]:
    started: list[dict[str, Any]] = []
    for cam_id in camera_ids:
        camera = get_camera(cam_id)
        if camera is None:
            started.append({"camera_id": cam_id, "started": False, "error": "Camera not found"})
            continue
        with _lock:
            existing = _threads.get(cam_id)
            if existing is not None and existing.is_alive():
                started.append({"camera_id": cam_id, "started": False, "error": "Update already running"})
                continue

        try:
            image = _image_for_camera(camera)
        except FirmwarePushError as exc:
            started.append({"camera_id": cam_id, "started": False, "error": str(exc)})
            continue
        current = current_version(camera)
        target = str(image.get("version") or "")
        if not force and current and not version_newer(target, current):
            started.append(
                {
                    "camera_id": cam_id,
                    "started": False,
                    "error": f"already on {current}",
                }
            )
            continue
        label = ""
        txt = getattr(camera, "txt", None) or {}
        if isinstance(txt, dict):
            label = str(txt.get("camera_name") or "")
        job = _set_job(
            cam_id,
            status="queued",
            message="Queued",
            phase="queued",
            progress=job_percent({"phase": "queued", "status": "queued"}),
            error=None,
            started_at=utc_now(),
            finished_at=None,
            from_version=current_version(camera),
            to_version=image.get("version"),
            name=getattr(camera, "name", cam_id),
            label=label or getattr(camera, "name", cam_id),
            family=camera_family(camera),
            product=str(image.get("label") or ""),
            bytes_sent=None,
            bytes_total=None,
        )

        def _run(target_id: str = cam_id) -> None:
            cam = get_camera(target_id)
            session = get_session(target_id)
            try:
                _set_job(
                    target_id,
                    status="updating",
                    phase="preparing",
                    message="Preparing firmware update.",
                    progress=job_percent({"phase": "preparing", "status": "updating"}),
                )

                def note(message: str) -> None:
                    user, phase = friendly_progress(message)
                    _set_job(
                        target_id,
                        status="updating",
                        message=user,
                        phase=phase,
                        detail=message,
                        progress=job_percent({"phase": phase, "status": "updating"}),
                    )

                def on_bytes(sent: int, total: int) -> None:
                    snapshot = {
                        "phase": "uploading",
                        "status": "updating",
                        "bytes_sent": sent,
                        "bytes_total": total,
                    }
                    percent = job_percent(snapshot)
                    _set_job(
                        target_id,
                        persist=sent >= total or percent % 5 == 0,
                        status="updating",
                        phase="uploading",
                        message="Uploading Mevo firmware. Please wait.",
                        progress=percent,
                        bytes_sent=sent,
                        bytes_total=total,
                    )

                result = push_camera(
                    cam, session, force=force, progress=note, bytes_progress=on_bytes
                )
                _set_job(
                    target_id,
                    status="done",
                    phase="rebooting",
                    message=f"Updated to {result['to_version']}. Waiting for camera to return.",
                    progress=job_percent({"phase": "rebooting", "status": "done"}),
                    error=None,
                    finished_at=utc_now(),
                    result=result,
                )
            except Exception as exc:
                _set_job(
                    target_id,
                    status="error",
                    message=str(exc),
                    error=str(exc),
                    finished_at=utc_now(),
                )

        thread = threading.Thread(target=_run, daemon=True, name=f"fw-update-{cam_id}")
        with _lock:
            _threads[cam_id] = thread
        thread.start()
        started.append({"camera_id": cam_id, "started": True, "job": job})
    return started


_jobs.update(_load_persisted_jobs())
