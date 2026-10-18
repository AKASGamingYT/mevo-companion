from __future__ import annotations

from typing import Any, NoReturn

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mevo_companion import DEFAULT_HOST, DEFAULT_PORT, __version__
from mevo_companion.discovery import CameraRegistry
from mevo_companion.protocol import (
    DEFAULT_SETTING_KEYS,
    KNOWN_COMMANDS,
    POWER_COMMANDS,
    CameraCommandError,
    CameraSession,
)
from mevo_companion.firmware.check import public_status, start_background_check
from mevo_companion.firmware.push import FirmwarePushError, start_updates
from mevo_companion.firmware.schedule import ScheduleError, install_task, uninstall_task
from mevo_companion.preview import PreviewHub
from mevo_companion.web import WEB_DIR

_sessions: dict[str, CameraSession] = {}
_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class ManualCameraIn(BaseModel):
    host: str
    port: int = Field(default=38000, ge=1, le=65535)
    name: str | None = None


class SettingsIn(BaseModel):
    settings: dict[str, Any]


class ConnectIn(BaseModel):
    password: str = ""


class FirmwareCheckIn(BaseModel):
    force: bool = True


class FirmwareTaskIn(BaseModel):
    action: str = "install"


class FirmwareUpdateIn(BaseModel):
    camera_ids: list[str] | None = None
    force: bool = False


def _close_session(cam_id: str) -> None:
    session = _sessions.pop(cam_id, None)
    if session is None:
        return
    try:
        session.close()
    except Exception:
        pass


def _require_online(camera: Any) -> None:
    if getattr(camera, "offline", False):
        raise HTTPException(status_code=409, detail="Camera is off")


def _session_for(registry: CameraRegistry, cam_id: str) -> tuple[Any, CameraSession]:
    camera = registry.get(cam_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found. Refresh discovery.")
    cam_id = camera.id
    _require_online(camera)
    session = _sessions.get(cam_id)
    if session is not None and (session.host != camera.host or session.port != camera.port):
        _close_session(cam_id)
        session = None
    if session is None:
        session = CameraSession(camera.host, camera.port)
        _sessions[cam_id] = session
    return camera, session


def _session_dict(session: CameraSession | None) -> dict[str, Any]:
    if session is None:
        return {"authorized": False, "protocol_version": None, "error": None, "probe": None}
    return {
        "authorized": session.authorized,
        "protocol_version": session.protocol_version,
        "error": session.last_error,
        "last_result": session.last_result,
        "probe": session.last_probe.to_dict() if session.last_probe else None,
    }


def _raise_command_error(exc: Exception, command: str, cam_id: str, camera: Any) -> NoReturn:
    if isinstance(exc, PermissionError):
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "command": command,
                "camera_id": cam_id,
                "host": camera.host,
                "port": camera.port,
            },
        ) from exc
    if isinstance(exc, CameraCommandError):
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "command": command,
                "camera_id": cam_id,
                "result": exc.payload,
            },
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(
        status_code=502,
        detail={"message": str(exc), "command": command, "camera_id": cam_id},
    ) from exc


def create_app(registry: CameraRegistry) -> FastAPI:
    app = FastAPI(title="Mevo Companion", version=__version__)
    app.state.registry = registry
    preview = PreviewHub(get_camera=registry.get)
    app.state.preview = preview

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "listen": f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
        }

    @app.post("/api/shutdown")
    def shutdown(request: Request) -> dict[str, bool]:
        client = request.client.host if request.client else ""
        if client not in _LOCAL_CLIENTS:
            raise HTTPException(status_code=403, detail="Shutdown is only available from this computer")
        stop = getattr(request.app.state, "stop", None)
        if stop is not None:
            stop.set()
        tray = getattr(request.app.state, "tray", None)
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        return {"ok": True}

    @app.get("/api/cameras")
    def list_cameras() -> dict[str, Any]:
        cameras = []
        for camera in registry.list():
            item = camera.to_dict()
            item["session"] = _session_dict(_sessions.get(camera.id))
            cameras.append(item)
        return {"cameras": cameras, "count": len(cameras)}

    @app.post("/api/cameras/manual")
    def add_manual(body: ManualCameraIn) -> dict[str, Any]:
        try:
            camera = registry.add_manual(body.host, body.port, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"camera": camera.to_dict()}

    @app.get("/api/cameras/{cam_id}")
    def get_camera(cam_id: str) -> dict[str, Any]:
        camera = registry.get(cam_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="Camera not found")
        return {
            "camera": camera.to_dict(),
            "session": _session_dict(_sessions.get(cam_id)),
        }

    @app.post("/api/cameras/{cam_id}/connect")
    def connect_camera(cam_id: str, body: ConnectIn | None = None) -> dict[str, Any]:
        camera, session = _session_for(registry, cam_id)
        if body and body.password:
            session.password = body.password
        try:
            result = session.connect()
        except Exception as exc:
            _raise_command_error(exc, "Authorize", cam_id, camera)
        return {
            "ok": True,
            "camera": camera.to_dict(),
            "session": _session_dict(session),
            "result": result,
        }

    @app.post("/api/cameras/{cam_id}/disconnect")
    def disconnect_camera(cam_id: str) -> dict[str, Any]:
        camera, session = _session_for(registry, cam_id)
        session.close()
        return {"ok": True, "camera": camera.to_dict(), "session": _session_dict(session)}

    @app.post("/api/cameras/{cam_id}/probe")
    def probe_camera(cam_id: str) -> dict[str, Any]:
        camera, session = _session_for(registry, cam_id)
        result = session.probe()
        return {"camera": camera.to_dict(), "probe": result.to_dict()}

    @app.get("/api/cameras/{cam_id}/settings")
    def get_settings(
        cam_id: str,
        keys: str | None = Query(default=None, description="Comma-separated setting keys"),
    ) -> dict[str, Any]:
        camera, session = _session_for(registry, cam_id)
        key_list = [item.strip() for item in (keys or "").split(",") if item.strip()]
        if not key_list:
            key_list = list(DEFAULT_SETTING_KEYS)
        try:
            result = session.get_settings(key_list)
        except Exception as exc:
            _raise_command_error(exc, "GetSettings", cam_id, camera)
        return {"camera": camera.to_dict(), "settings": result}

    @app.get("/api/cameras/{cam_id}/preview")
    async def preview_status(
        cam_id: str,
        follow: bool = Query(default=False),
    ) -> dict[str, Any]:
        camera = registry.get(cam_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="Camera not found")
        if getattr(camera, "offline", False):
            return {
                "ok": False,
                "ready": False,
                "ndi": False,
                "following": False,
                "message": "Camera offline",
                "camera": camera.to_dict(),
            }
        if follow:
            preview.follow(cam_id)
        else:
            preview.heartbeat(cam_id)
        status = preview.status(cam_id)
        status["camera"] = camera.to_dict()
        return status

    @app.get("/api/cameras/{cam_id}/preview.jpg")
    async def preview_jpeg(
        cam_id: str,
        follow: bool = Query(default=False),
    ) -> Response:
        camera = registry.get(cam_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="Camera not found")
        if getattr(camera, "offline", False):
            raise HTTPException(status_code=503, detail="Camera offline")
        if follow:
            preview.follow(cam_id)
        else:
            preview.heartbeat(cam_id)
        frame = preview.frame(cam_id)
        if frame is None:
            raise HTTPException(
                status_code=503,
                detail=preview.status(cam_id).get("message") or "Preview not ready",
            )
        return Response(
            content=frame.jpeg,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.put("/api/cameras/{cam_id}/settings")
    def put_settings(cam_id: str, body: SettingsIn) -> dict[str, Any]:
        camera, session = _session_for(registry, cam_id)
        try:
            result = session.set_settings(body.settings)
        except Exception as exc:
            _raise_command_error(exc, "SetSettings", cam_id, camera)
        return {"ok": True, "camera": camera.to_dict(), "result": result}

    @app.get("/api/commands")
    def commands() -> dict[str, Any]:
        return {
            "commands": list(KNOWN_COMMANDS),
            "stream_deck": list(POWER_COMMANDS),
            "setting_keys": list(DEFAULT_SETTING_KEYS),
        }

    def _run_action(cam_id: str, command: str) -> dict[str, Any]:
        camera, session = _session_for(registry, cam_id)
        try:
            result = session.send_command(command)
        except Exception as exc:
            _raise_command_error(exc, command, cam_id, camera)
        return {
            "ok": True,
            "command": command,
            "result": result,
            "session": _session_dict(session),
            "camera": camera.to_dict(),
        }

    @app.post("/api/cameras/{cam_id}/actions/ping")
    def action_ping(cam_id: str) -> dict[str, Any]:
        return _run_action(cam_id, "Ping")

    @app.post("/api/cameras/{cam_id}/actions/shutdown")
    def action_shutdown(cam_id: str) -> dict[str, Any]:
        camera = registry.get(cam_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="Camera not found. Refresh discovery.")
        cam_id = camera.id
        if camera.offline:
            return {
                "ok": True,
                "command": "Shutdown",
                "result": {"already_offline": True},
                "session": _session_dict(None),
                "camera": camera.to_dict(),
            }
        result: dict[str, Any] = {"ok": True, "command": "Shutdown"}
        try:
            result = _run_action(cam_id, "Shutdown")
        except HTTPException as exc:
            if exc.status_code == 409:
                raise
        camera = registry.mark_offline(cam_id, powered_off=True)
        _close_session(cam_id)
        if camera is not None:
            result["camera"] = camera.to_dict()
        return result

    @app.post("/api/cameras/{cam_id}/actions/reboot")
    def action_reboot(cam_id: str) -> dict[str, Any]:
        return _run_action(cam_id, "Reboot")

    @app.post("/api/cameras/{cam_id}/actions/update-firmware")
    def action_update_firmware(cam_id: str, body: FirmwareUpdateIn | None = None) -> dict[str, Any]:
        force = body.force if body else False
        try:
            started = start_updates(
                [cam_id],
                get_camera=registry.get,
                get_session=lambda cid: _session_for(registry, cid)[1],
                force=force,
            )
        except FirmwarePushError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        status = public_status(registry)
        status["ok"] = True
        status["started"] = started
        return status

    @app.post("/api/firmware/update")
    def firmware_update(body: FirmwareUpdateIn | None = None) -> dict[str, Any]:
        payload = body or FirmwareUpdateIn()
        ids = list(payload.camera_ids or [])
        if not ids:
            ids = [
                item["id"]
                for item in public_status(registry).get("cameras") or []
                if item.get("id")
                and not item.get("offline")
                and (payload.force or item.get("update"))
            ]
        if not ids:
            raise HTTPException(status_code=400, detail="No cameras need a firmware update")
        try:
            started = start_updates(
                ids,
                get_camera=registry.get,
                get_session=lambda cid: _session_for(registry, cid)[1],
                force=payload.force,
            )
        except FirmwarePushError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        status = public_status(registry)
        status["ok"] = True
        status["started"] = started
        return status

    @app.get("/api/firmware")
    def firmware_status(light: bool = Query(default=False)) -> dict[str, Any]:
        return public_status(registry, light=light)

    @app.post("/api/firmware/check")
    def firmware_check(body: FirmwareCheckIn | None = None) -> dict[str, Any]:
        force = True if body is None else body.force
        started = start_background_check(force=force)
        status = public_status(registry)
        status["ok"] = True
        status["started"] = started
        return status

    @app.post("/api/firmware/task")
    def firmware_task(body: FirmwareTaskIn | None = None) -> dict[str, Any]:
        action = (body.action if body else "install").strip().lower()
        try:
            if action == "uninstall":
                uninstall_task()
            elif action == "install":
                install_task()
            else:
                raise HTTPException(status_code=400, detail="action must be install or uninstall")
        except ScheduleError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        status = public_status(registry)
        status["ok"] = True
        return status

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(
            WEB_DIR / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
