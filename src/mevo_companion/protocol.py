"""Mevo cameraman protocol (Start / Core).

Wire format, confirmed against live Start units:

* TLS 1.2 on the Bonjour TCP port (usually 38000)
* Frame: ``CMAN`` + 4-byte big-endian length + compact JSON (no spaces)
* Command names are snake_case (``ping``, ``authorize``, ``reboot``, ``shut_down``)
* Camera replies with ``{command}_result``
* ``Shutdown`` is ``CommandShutDown`` on the wire: ``shut_down``, not ``shutdown``
* Idle connections drop after ~20s; we reconnect and re-authorize
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import socket
import ssl
import struct
import threading
import time
from typing import Any
import uuid

from mevo_companion import __version__

MAGIC = b"CMAN"
DEFAULT_TIMEOUT = 5.0

# Public catalog still uses firmware / app PascalCase names.
POWER_COMMANDS = ("Shutdown", "Reboot", "Ping")
POWER_OFF_COMMANDS = frozenset({"shut_down", "reboot"})
FIRMWARE_FINISH = "firmware_update_finished_notification"
DEFAULT_UPLOAD_PATH = "/upload/mevo_fw.tar.xz"

# "Shutdown" is one English word, but the cameraman command is CommandShutDown.
WIRE_ALIASES = {
    "shutdown": "shut_down",
}

KNOWN_COMMANDS = (
    "Authorize",
    "Start",
    "Stop",
    "Ping",
    "GetSettings",
    "SetSettings",
    "Shutdown",
    "Reboot",
    "CancelShutdownByExternalPower",
    "StreamConfig",
    "StreamStart",
    "StreamStop",
    "StartRecording",
    "StopRecording",
    "Crop",
    "DismissCrop",
    "CancelCropMove",
    "UpdateFirmware",
    "ReceiveFirmware",
    "ResetToDefaults",
    "FormatSD",
    "GetNetworkConfiguration",
    "ConfigureSta",
    "ConfigureAP",
    "ConfigureEthernet",
    "SetStudioMode",
    "GfxGet",
    "GfxSet",
    "DisconnectOtherClients",
)

DEFAULT_SETTING_KEYS = (
    "led_brightness",
    "camera_name",
    "ndi_mode",
    "srt_mode",
    "uvc_mode",
    "wireless_webcam_mode",
    "multicam_mode",
)


class CameraCommandError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def wire_command(name: str) -> str:
    """Map CJsonInCmd PascalCase (or mixed) names to on-wire snake_case."""
    raw = name.strip()
    if not raw:
        raise ValueError("command is required")
    if "_" in raw or raw.islower():
        wire = raw.lower()
    else:
        parts = re.findall(r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z][a-z]+|[0-9]+", raw)
        wire = "_".join(part.lower() for part in parts) if parts else raw.lower()
    return WIRE_ALIASES.get(wire, wire)


def encode_frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return MAGIC + struct.pack(">I", len(body)) + body


def decode_frame(data: bytes) -> dict[str, Any]:
    if len(data) < 8 or data[:4] != MAGIC:
        raise ValueError(f"invalid CMAN frame: {data[:16]!r}")
    (length,) = struct.unpack(">I", data[4:8])
    body = data[8 : 8 + length]
    if len(body) != length:
        raise ValueError("truncated CMAN frame")
    return json.loads(body)


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


@dataclass
class ProbeResult:
    ok: bool
    host: str
    port: int
    connected: bool
    banner_hex: str
    banner_text: str
    error: str | None = None
    tls: bool = False
    reply: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "host": self.host,
            "port": self.port,
            "connected": self.connected,
            "tls": self.tls,
            "banner_hex": self.banner_hex,
            "banner_text": self.banner_text,
            "error": self.error,
            "reply": self.reply,
        }


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> ProbeResult:
    """TLS connect and Ping — the useful liveness check."""
    try:
        session = CameraSession(host, port)
        reply = session.send_command("Ping", timeout=timeout, authorize=False)
        session.close()
        return ProbeResult(
            ok=True,
            host=host,
            port=port,
            connected=True,
            tls=True,
            banner_hex="",
            banner_text="",
            reply=reply,
        )
    except Exception as exc:
        return ProbeResult(
            ok=False,
            host=host,
            port=port,
            connected=False,
            tls=True,
            banner_hex="",
            banner_text="",
            error=str(exc),
        )


class CameraSession:
    """Persistent TLS + CMAN session to one camera."""

    def __init__(self, host: str, port: int, password: str = "") -> None:
        self.host = host
        self.port = port
        self.password = password
        self.authorized = False
        self.protocol_version: int | None = None
        self.client_id = "{" + str(uuid.uuid4()) + "}"
        self.last_probe: ProbeResult | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self._sock: ssl.SSLSocket | None = None
        self._lock = threading.RLock()

    def probe(self) -> ProbeResult:
        self.last_probe = probe_tcp(self.host, self.port)
        return self.last_probe

    def connect(self, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        with self._lock:
            self._close_socket()
            self._open_socket(timeout)
            result = self._transact(
                {
                    "command": "authorize",
                    "app_name": "MevoCompanion",
                    "app_version": __version__,
                    "client_id": self.client_id,
                    "client_type": 1,
                    "password": self.password or "",
                },
                timeout=timeout,
            )
            if not result.get("result"):
                self._close_socket()
                desc = result.get("error_description") or "authorize failed"
                self.last_error = str(desc)
                raise PermissionError(str(desc))
            self.authorized = True
            self.protocol_version = result.get("protocol_version")
            self.last_error = None
            self.last_result = result
            return result

    def close(self) -> None:
        with self._lock:
            self._close_socket()
            self.authorized = False

    def send_command(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        authorize: bool = True,
    ) -> dict[str, Any]:
        wire = wire_command(command)
        known = {wire_command(item) for item in KNOWN_COMMANDS}
        known.add("authorize")
        if wire not in known:
            raise ValueError(f"Unknown command {command}")
        body = {**(payload or {}), "command": wire}
        with self._lock:
            self._ensure_socket(timeout=timeout, authorize=authorize)
            try:
                result = self._transact(body, timeout=timeout)
            except (OSError, TimeoutError, ValueError, ConnectionError):
                if not authorize or wire in POWER_OFF_COMMANDS or wire == "update_firmware":
                    raise
                self.connect(timeout=timeout)
                result = self._transact(body, timeout=timeout)
            result = self._finish_result(wire, result)
            if wire in POWER_OFF_COMMANDS or wire == "update_firmware":
                self._close_socket()
            return result

    def get_settings(
        self, keys: list[str] | tuple[str, ...] = DEFAULT_SETTING_KEYS
    ) -> dict[str, Any]:
        return self.send_command("GetSettings", {"settings": list(keys)})

    def set_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        return self.send_command("SetSettings", values)

    def _ensure_socket(self, timeout: float, authorize: bool) -> None:
        if authorize:
            if not self.authorized or self._sock is None:
                self.connect(timeout=timeout)
            return
        if self._sock is None:
            self._open_socket(timeout)

    def _open_socket(self, timeout: float) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=timeout)
        try:
            sock = _ssl_context().wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise
        sock.settimeout(timeout)
        self._sock = sock
        self.authorized = False

    def _finish_result(self, wire: str, result: dict[str, Any]) -> dict[str, Any]:
        self.last_result = result
        if result.get("command") == "unsupported_command_result":
            raise CameraCommandError(
                f"unsupported command: {result.get('unsupported_command', wire)}",
                result,
            )
        if result.get("result") is False:
            desc = result.get("error_description") or f"{wire} failed"
            self.last_error = str(desc)
            raise CameraCommandError(str(desc), result)
        self.last_error = None
        return result

    def _transact(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        sock = self._sock
        if sock is None:
            raise ConnectionError("not connected")
        command = payload["command"]
        sock.settimeout(timeout)
        sock.sendall(encode_frame(payload))
        deadline = time.monotonic() + timeout
        extras: list[dict[str, Any]] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no {command}_result from {self.host}")
            try:
                frame = self._read_frame(sock, remaining)
            except ConnectionError:
                if command in POWER_OFF_COMMANDS or command == "update_firmware":
                    return _power_off_result(command, extras, closed=True)
                raise
            name = frame.get("command")
            if name == f"{command}_result":
                if extras:
                    frame = {**frame, "_notifications": extras}
                if command == "update_firmware" and frame.get("result") is not False:
                    finished = self._wait_firmware_finished(sock, deadline, extras)
                    if finished is not None:
                        frame = {**frame, "_finished": finished}
                return frame
            if name == FIRMWARE_FINISH and command == "update_firmware":
                extras.append(frame)
                return {
                    "command": "update_firmware_result",
                    "result": True,
                    "error_code": 0,
                    "error_description": "",
                    "_finished": frame,
                    "_notifications": extras,
                }
            if name == "unsupported_command_result" and frame.get("unsupported_command") in {
                command,
                None,
            }:
                if extras:
                    frame = {**frame, "_notifications": extras}
                return frame
            extras.append(frame)
            if (
                command in POWER_OFF_COMMANDS
                and name == "camera_will_shutdown_notification"
            ):
                return _power_off_result(command, extras, closed=False)

    def _wait_firmware_finished(
        self, sock: ssl.SSLSocket, deadline: float, extras: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                frame = self._read_frame(sock, remaining)
            except (ConnectionError, TimeoutError, OSError):
                return {"command": FIRMWARE_FINISH, "result": True, "_closed": True}
            name = frame.get("command")
            if name == FIRMWARE_FINISH:
                return frame
            extras.append(frame)

    def receive_firmware(
        self,
        sha512: str,
        signature: str = "",
        *,
        force: bool = True,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        return self.send_command(
            "ReceiveFirmware",
            {"force": force, "sha512": sha512, "signature": signature or ""},
            timeout=timeout,
        )

    def apply_firmware(
        self,
        sha512: str,
        signature: str = "",
        *,
        force: bool = True,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        return self.send_command(
            "UpdateFirmware",
            {"force": force, "sha512": sha512, "signature": signature or ""},
            timeout=timeout,
        )

    def _read_frame(self, sock: ssl.SSLSocket, timeout: float) -> dict[str, Any]:
        sock.settimeout(timeout)
        header = _recv_exact(sock, 8)
        if header[:4] != MAGIC:
            raise ValueError(f"invalid CMAN magic: {header!r}")
        (length,) = struct.unpack(">I", header[4:8])
        if length > 4_000_000:
            raise ValueError(f"CMAN length too large: {length}")
        body = _recv_exact(sock, length)
        return json.loads(body)

    def _close_socket(self) -> None:
        sock = self._sock
        self._sock = None
        self.authorized = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _power_off_result(
    command: str, extras: list[dict[str, Any]], closed: bool
) -> dict[str, Any]:
    for extra in extras:
        if extra.get("command") == f"{command}_result":
            rest = [item for item in extras if item is not extra]
            if rest:
                extra = {**extra, "_notifications": rest}
            if closed:
                extra = {**extra, "_closed": True}
            return extra
    result: dict[str, Any] = {
        "command": f"{command}_result",
        "result": True,
        "error_code": 0,
        "error_description": "",
    }
    if extras:
        result["_notifications"] = extras
    if closed:
        result["_closed"] = True
    return result


def _recv_exact(sock: ssl.SSLSocket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("camera closed the connection")
        buf.extend(chunk)
    return bytes(buf)
