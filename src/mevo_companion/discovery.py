from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import socket
import threading
import time
from typing import Any

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

from mevo_companion import SERVICE_TYPES


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def camera_id(name: str, host: str, port: int) -> str:
    raw = f"{name}|{host}|{port}".encode()
    return hashlib.sha1(raw).hexdigest()[:10]


def instance_name(name: str) -> str:
    return name.split(".", 1)[0].strip()


def identity_key(name: str, txt: dict[str, str] | None) -> str:
    """Stable identity for one physical camera.

    DHCP and the two Bonjour service types used to mint a new id on every
    address or service change. Serial number, then the mDNS instance name
    (``mevo-27FRY``), stays the same across those changes.
    """
    serial = str((txt or {}).get("serial_number") or "").strip().upper()
    if serial:
        return f"serial:{serial}"
    short = instance_name(name).lower()
    if short:
        return f"name:{short}"
    return ""


def _decode_txt(properties: dict[bytes, bytes | None]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in properties.items():
        k = key.decode("utf-8", "replace")
        if value is None:
            out[k] = ""
        else:
            out[k] = value.decode("utf-8", "replace")
    return out


def _parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_battery(txt: dict[str, str] | None) -> dict[str, Any]:
    """Normalize Bonjour ``camera_charge`` / ``power_status`` for the UI and Stream Deck.

    ``power_status`` 0 is on battery. Non-zero (commonly 6) is AC / charging.
    """
    txt = txt or {}
    percent = _parse_int(txt.get("camera_charge"))
    if percent is not None:
        percent = max(0, min(100, percent))
    status = _parse_int(txt.get("power_status"))
    return {
        "percent": percent,
        "charging": status is not None and status != 0,
        "power_status": status,
    }


def _pick_host(info: ServiceInfo) -> str | None:
    parsed = info.parsed_addresses()
    if not parsed:
        return None
    # Prefer IPv4 on the LAN.
    for addr in parsed:
        if ":" not in addr:
            return addr
    return parsed[0]


@dataclass
class DiscoveredCamera:
    id: str
    name: str
    host: str
    port: int
    service_type: str
    txt: dict[str, str] = field(default_factory=dict)
    last_seen: str = field(default_factory=_utc_now)
    source: str = "bonjour"
    offline: bool = False
    powered_off: bool = False
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        model = (
            self.txt.get("model")
            or self.txt.get("hw")
            or self.txt.get("type")
            or ""
        )
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "service_type": self.service_type,
            "model": model,
            "txt": self.txt,
            "battery": parse_battery(self.txt),
            "last_seen": self.last_seen,
            "source": self.source,
            "offline": self.offline,
            "powered_off": self.powered_off,
            "aliases": list(self.aliases),
        }


def camera_from_dict(data: dict[str, Any]) -> DiscoveredCamera:
    txt = data.get("txt") if isinstance(data.get("txt"), dict) else {}
    try:
        port = int(data.get("port") or 38000)
    except (TypeError, ValueError):
        port = 38000
    return DiscoveredCamera(
        id=str(data.get("id") or ""),
        name=str(data.get("name") or data.get("id") or "Mevo"),
        host=str(data.get("host") or ""),
        port=port,
        service_type=str(data.get("service_type") or "bonjour"),
        txt={str(key): str(value) for key, value in txt.items()},
        last_seen=str(data.get("last_seen") or _utc_now()),
        source=str(data.get("source") or "bonjour"),
        offline=True,
        powered_off=bool(data.get("powered_off", True)),
        aliases=[str(item) for item in (data.get("aliases") or []) if item],
    )


class _Listener(ServiceListener):
    def __init__(self, registry: CameraRegistry) -> None:
        self.registry = registry

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.registry._upsert_from_mdns(zc, type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.registry._upsert_from_mdns(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.registry._remove_mdns(type_, name)


class CameraRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cameras: dict[str, DiscoveredCamera] = {}
        self._mdns_index: dict[str, str] = {}
        self._zeroconf: Zeroconf | None = None
        self._browsers: list[ServiceBrowser] = []
        self._persist_enabled = False
        self._last_persist = 0.0

    def start(self) -> None:
        if self._zeroconf is not None:
            return
        self._persist_enabled = True
        self._restore()
        self._zeroconf = Zeroconf()
        listener = _Listener(self)
        for service_type in SERVICE_TYPES:
            self._browsers.append(
                ServiceBrowser(self._zeroconf, service_type, listener)
            )

    def stop(self) -> None:
        for browser in self._browsers:
            browser.cancel()
        self._browsers.clear()
        if self._zeroconf is not None:
            self._zeroconf.close()
            self._zeroconf = None
        with self._lock:
            self._persist_locked(force=True)

    def list(self) -> list[DiscoveredCamera]:
        with self._lock:
            return sorted(
                self._cameras.values(),
                key=lambda c: (c.offline, c.name.lower()),
            )

    def get(self, cam_id: str) -> DiscoveredCamera | None:
        with self._lock:
            return self._resolve_locked(cam_id)

    def mark_offline(self, cam_id: str, *, powered_off: bool = False) -> DiscoveredCamera | None:
        with self._lock:
            cam = self._resolve_locked(cam_id)
            if cam is None:
                return None
            cam.offline = True
            if powered_off:
                cam.powered_off = True
            self._persist_locked(force=True)
            return cam

    def observe(
        self,
        *,
        service_type: str,
        service_name: str,
        host: str,
        port: int,
        txt: dict[str, str],
    ) -> DiscoveredCamera:
        """Record a Bonjour advertisement, reusing the camera's existing id."""
        short = instance_name(service_name) or service_name
        with self._lock:
            cam = self._match_locked(short, txt)
            if cam is None:
                cam = DiscoveredCamera(
                    id=camera_id(service_name, host, port),
                    name=short,
                    host=host,
                    port=port,
                    service_type=service_type,
                    txt=dict(txt),
                    source="bonjour",
                )
                self._cameras[cam.id] = cam
            else:
                cam.name = short or cam.name
                cam.host = host
                cam.port = port
                cam.service_type = service_type
                cam.txt = dict(txt)
                cam.last_seen = _utc_now()
                cam.offline = False
                cam.powered_off = False
                cam.source = "bonjour"
            self._collapse_locked()
            resolved = self._resolve_locked(cam.id) or cam
            self._mdns_index[f"{service_type}|{service_name}"] = resolved.id
            self._persist_locked(force=False)
            return resolved

    def add_manual(self, host: str, port: int, name: str | None = None) -> DiscoveredCamera:
        host = host.strip()
        if not host:
            raise ValueError("host is required")
        try:
            socket.inet_aton(host)
        except OSError:
            # Allow hostnames.
            pass
        label = name.strip() if name else f"Mevo @ {host}"
        cam = DiscoveredCamera(
            id=camera_id(label, host, port),
            name=label,
            host=host,
            port=port,
            service_type="manual",
            source="manual",
        )
        with self._lock:
            self._cameras[cam.id] = cam
        return cam

    def _upsert_from_mdns(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=3000)
        if info is None:
            return
        host = _pick_host(info)
        if host is None:
            return
        txt = _decode_txt(info.properties)
        self.observe(
            service_type=type_,
            service_name=name,
            host=host,
            port=info.port,
            txt=txt,
        )

    def _remove_mdns(self, type_: str, name: str) -> None:
        key = f"{type_}|{name}"
        with self._lock:
            cam_id = self._mdns_index.pop(key, None)
            if not cam_id:
                return
            if any(other == cam_id for other in self._mdns_index.values()):
                return
            existing = self._resolve_locked(cam_id)
            if existing and existing.source == "bonjour":
                existing.offline = True
                self._persist_locked(force=True)

    def _resolve_locked(self, cam_id: str) -> DiscoveredCamera | None:
        cam = self._cameras.get(cam_id)
        if cam is not None:
            return cam
        for item in self._cameras.values():
            if cam_id in item.aliases:
                return item
        return None

    def _match_locked(self, short_name: str, txt: dict[str, str]) -> DiscoveredCamera | None:
        key = identity_key(short_name, txt)
        matches = [
            cam
            for cam in self._cameras.values()
            if key and identity_key(cam.name, cam.txt) == key
        ]
        if not matches:
            short = instance_name(short_name).lower()
            matches = [
                cam
                for cam in self._cameras.values()
                if short and instance_name(cam.name).lower() == short
            ]
        if not matches:
            return None
        return min(matches, key=lambda cam: cam.last_seen or "")

    def _collapse_locked(self) -> None:
        groups: dict[str, list[DiscoveredCamera]] = {}
        for cam in list(self._cameras.values()):
            key = identity_key(cam.name, cam.txt)
            if not key:
                continue
            groups.setdefault(key, []).append(cam)
        for group in groups.values():
            if len(group) < 2:
                continue
            primary = min(group, key=lambda cam: cam.last_seen or "")
            best = max(group, key=lambda cam: (not cam.offline, cam.last_seen or ""))
            if best is not primary:
                self._copy_presence_locked(primary, best)
            for other in group:
                if other.id != primary.id:
                    self._absorb_locked(primary, other)

    def _copy_presence_locked(self, target: DiscoveredCamera, source: DiscoveredCamera) -> None:
        if source.host:
            target.host = source.host
        if source.port:
            target.port = source.port
        if source.txt:
            target.txt = dict(source.txt)
        if source.name:
            target.name = source.name
        if source.service_type:
            target.service_type = source.service_type
        if source.last_seen:
            target.last_seen = source.last_seen
        target.offline = source.offline
        target.powered_off = source.powered_off
        if source.source:
            target.source = source.source

    def _absorb_locked(self, primary: DiscoveredCamera, other: DiscoveredCamera) -> None:
        if other.id != primary.id and other.id not in primary.aliases:
            primary.aliases.append(other.id)
        for alias in other.aliases:
            if alias != primary.id and alias not in primary.aliases:
                primary.aliases.append(alias)
        self._cameras.pop(other.id, None)
        for key, cam_id in list(self._mdns_index.items()):
            if cam_id == other.id:
                self._mdns_index[key] = primary.id

    def _restore(self) -> None:
        from mevo_companion.firmware.store import load_json, store_dir

        payload = load_json(store_dir() / "cameras.json", {})
        items = payload.get("cameras") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return
        with self._lock:
            for item in items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                cam_id = str(item["id"])
                if cam_id in self._cameras:
                    continue
                cam = camera_from_dict(item)
                if not cam.id:
                    continue
                self._cameras[cam.id] = cam
            self._collapse_locked()
            self._persist_locked(force=True)

    def _persist_locked(self, *, force: bool) -> None:
        if not self._persist_enabled:
            return
        now = time.monotonic()
        if not force and now - self._last_persist < 2.0:
            return
        from mevo_companion.firmware.store import save_json, store_dir

        self._last_persist = now
        save_json(
            store_dir() / "cameras.json",
            {"cameras": [cam.to_dict() for cam in self._cameras.values()]},
        )
