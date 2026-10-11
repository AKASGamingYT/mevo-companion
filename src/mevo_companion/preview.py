"""Low-bandwidth NDI preview frames for the companion GUI.

Mevo Start/Core advertise NDI | HX when ``ndi_mode`` is on. The official
app's FFOV preview is a separate MVTP stream; NDI is already enabled on
the church cameras and is safe to receive without touching RTMP/SRT output.

We subscribe at NDI ``lowest`` bandwidth (typically 640x360) so a GUI
preview does not pull the program encoder.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

IDLE_SEC = 5.0
CAPTURE_SEC = 0.25
SWITCH_HOLD_SEC = 0.35
JPEG_QUALITY = 72


def camera_ndi_hints(camera: Any) -> list[str]:
    if camera is None:
        return []
    if hasattr(camera, "txt"):
        txt = getattr(camera, "txt", None) or {}
        name = getattr(camera, "name", "") or ""
    else:
        txt = camera.get("txt") or {}
        name = camera.get("name") or ""
    hints: list[str] = []
    serial = str(txt.get("serial_number") or "")
    if len(serial) >= 5:
        hints.append(serial[-5:])
    if serial:
        hints.append(serial)
    label = str(txt.get("camera_name") or "")
    if label:
        hints.append(label)
    if name:
        hints.append(name)
    seen: set[str] = set()
    out: list[str] = []
    for item in hints:
        key = item.upper()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def match_ndi_source(hints: list[str], source_names: list[str]) -> str | None:
    names = [str(item) for item in source_names if item]
    for hint in hints:
        needle = hint.upper()
        if not needle:
            continue
        for name in names:
            if needle in name.upper():
                return name
    return None


def source_switch_ready(
    current: str,
    wanted: str,
    pending_since: float,
    now: float,
    hold: float = SWITCH_HOLD_SEC,
) -> bool:
    """Connect immediately the first time; debounce later source hops."""
    if not wanted or wanted == current:
        return False
    if not current:
        return True
    return (now - pending_since) >= hold


def ndi_enabled(camera: Any) -> bool:
    if camera is None:
        return False
    txt = getattr(camera, "txt", None) if hasattr(camera, "txt") else camera.get("txt")
    return str((txt or {}).get("ndi_mode") or "").lower() in {"true", "1", "yes"}


@dataclass
class PreviewFrame:
    jpeg: bytes
    width: int
    height: int
    source: str
    captured_at: float


class PreviewHub:
    """One NDI receiver, pointed at whichever camera the GUI last asked for."""

    def __init__(self, get_camera: Callable[[str], Any | None]) -> None:
        self._get_camera = get_camera
        self._lock = threading.Lock()
        self._wanted: str | None = None
        self._touched = 0.0
        self._frames: dict[str, PreviewFrame] = {}
        self._messages: dict[str, str] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def follow(self, cam_id: str) -> None:
        with self._lock:
            self._wanted = cam_id
            self._touched = time.monotonic()
        self._ensure_thread()

    def heartbeat(self, cam_id: str) -> None:
        with self._lock:
            if self._wanted == cam_id:
                self._touched = time.monotonic()

    def touch(self, cam_id: str) -> None:
        self.follow(cam_id)

    def frame(self, cam_id: str) -> PreviewFrame | None:
        return self._frame_for(cam_id)

    def _frame_for(self, cam_id: str) -> PreviewFrame | None:
        camera = self._get_camera(cam_id)
        with self._lock:
            frame = self._frames.get(cam_id)
        if frame is None or camera is None:
            return None
        matched = match_ndi_source(camera_ndi_hints(camera), [frame.source])
        if matched != frame.source:
            return None
        return frame

    def status(self, cam_id: str) -> dict[str, Any]:
        camera = self._get_camera(cam_id)
        frame = self._frame_for(cam_id)
        with self._lock:
            message = self._messages.get(cam_id)
            following = self._wanted == cam_id
        if frame:
            return {
                "ok": True,
                "ready": True,
                "width": frame.width,
                "height": frame.height,
                "source": frame.source,
                "age_ms": int((time.monotonic() - frame.captured_at) * 1000),
                "ndi": ndi_enabled(camera),
                "following": following,
                "message": "Live preview" if following else "Last preview frame",
            }
        if not ndi_enabled(camera):
            message = message or "NDI is off on this camera — turn it on in camera settings."
        elif following:
            message = message or "Connecting NDI preview…"
        return {
            "ok": False,
            "ready": False,
            "ndi": ndi_enabled(camera),
            "following": following,
            "message": message or "Waiting for NDI preview…",
        }

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="mevo-ndi-preview",
                daemon=True,
            )
            self._thread.start()

    def _set_message(self, cam_id: str, message: str) -> None:
        with self._lock:
            self._messages[cam_id] = message

    def _run(self) -> None:
        try:
            from cyndilib.finder import Finder
            from cyndilib.receiver import Receiver
            from cyndilib.video_frame import VideoFrameSync
            from cyndilib.wrapper.ndi_recv import RecvBandwidth, RecvColorFormat
        except Exception as exc:
            log.warning("NDI preview unavailable: %s", exc)
            with self._lock:
                cam_id = self._wanted
            if cam_id:
                self._set_message(cam_id, "NDI runtime is not available on this PC.")
            return

        finder = Finder()
        receiver = Receiver(
            color_format=RecvColorFormat.RGBX_RGBA,
            bandwidth=RecvBandwidth.lowest,
            recv_name="Mevo Companion",
        )
        video = VideoFrameSync()
        receiver.frame_sync.set_video_frame(video)
        current_source = ""
        pending_source = ""
        pending_since = 0.0
        names: list[str] = []
        names_at = 0.0
        skip_frames = 0
        try:
            finder.open()
            while not self._stop.is_set():
                now = time.monotonic()
                with self._lock:
                    cam_id = self._wanted
                    idle = (now - self._touched) > IDLE_SEC
                if not cam_id or idle:
                    if current_source:
                        try:
                            receiver.disconnect()
                        except Exception:
                            pass
                        current_source = ""
                    pending_source = ""
                    skip_frames = 0
                    self._stop.wait(0.25)
                    continue
                camera = self._get_camera(cam_id)
                if camera is None:
                    self._set_message(cam_id, "Camera not found.")
                    self._stop.wait(0.4)
                    continue
                if getattr(camera, "offline", False):
                    self._set_message(cam_id, "Camera offline")
                    if current_source:
                        try:
                            receiver.disconnect()
                        except Exception:
                            pass
                        current_source = ""
                    pending_source = ""
                    skip_frames = 0
                    self._stop.wait(0.4)
                    continue
                if now - names_at > 1.0 or not names:
                    try:
                        names = list(finder.get_source_names() or [])
                        names_at = now
                    except Exception as exc:
                        self._set_message(cam_id, f"NDI finder error: {exc}")
                        self._stop.wait(0.5)
                        continue
                source_name = match_ndi_source(camera_ndi_hints(camera), names) or ""
                if not source_name:
                    if not ndi_enabled(camera):
                        self._set_message(
                            cam_id,
                            "NDI is off on this camera — turn it on in camera settings.",
                        )
                    else:
                        self._set_message(cam_id, "Waiting for NDI source…")
                    if current_source:
                        try:
                            receiver.disconnect()
                        except Exception:
                            pass
                        current_source = ""
                    pending_source = ""
                    skip_frames = 0
                    self._stop.wait(0.4)
                    continue
                if source_name != current_source:
                    if pending_source != source_name:
                        pending_source = source_name
                        pending_since = now
                    if not source_switch_ready(
                        current_source, source_name, pending_since, now
                    ):
                        self._stop.wait(CAPTURE_SEC)
                        continue
                    try:
                        source = finder.get_source(source_name)
                        if source is None:
                            names = []
                            names_at = 0.0
                            self._set_message(cam_id, "Waiting for NDI source…")
                            self._stop.wait(0.4)
                            continue
                        receiver.set_source(source)
                        current_source = source_name
                        pending_source = ""
                        skip_frames = 4
                    except Exception as exc:
                        self._set_message(cam_id, f"NDI connect failed: {exc}")
                        current_source = ""
                        pending_source = ""
                        skip_frames = 0
                        self._stop.wait(0.5)
                        continue
                    self._set_message(cam_id, "Connecting NDI preview…")
                try:
                    receiver.frame_sync.capture_video()
                    if skip_frames > 0:
                        skip_frames -= 1
                        self._stop.wait(CAPTURE_SEC)
                        continue
                    width, height = int(video.xres), int(video.yres)
                    if min(width, height) > 0:
                        pixels = video.get_array().reshape(height, width, 4)
                        jpeg = _jpeg_rgb(pixels[..., :3].copy())
                        frame = PreviewFrame(
                            jpeg=jpeg,
                            width=width,
                            height=height,
                            source=current_source,
                            captured_at=time.monotonic(),
                        )
                        with self._lock:
                            if self._wanted == cam_id:
                                self._frames[cam_id] = frame
                                self._messages[cam_id] = "Live preview"
                except Exception as exc:
                    self._set_message(cam_id, f"NDI frame failed: {exc}")
                self._stop.wait(CAPTURE_SEC)
        finally:
            try:
                receiver.disconnect()
            except Exception:
                pass
            try:
                finder.close()
            except Exception:
                pass


def _jpeg_rgb(rgb: Any) -> bytes:
    from PIL import Image

    image = Image.fromarray(rgb, "RGB")
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=False)
    return buf.getvalue()
