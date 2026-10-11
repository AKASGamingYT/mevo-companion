"""Extract camera firmware blobs from official Mevo APKs / bundles."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

from mevo_companion.firmware.store import load_catalog, save_catalog, store_dir

FAMILY_BY_TYPE = {
    "001": "plus",
    "002": "plus",
    "004": "start",
    "200": "core",
}

FAMILY_LABELS = {
    "start": "Mevo Start",
    "plus": "Mevo Plus",
    "core": "Mevo Core",
}

FAMILY_SHORT = {
    "start": "Start",
    "plus": "Plus",
    "core": "Core",
}

_MONTHS = (
    "January|February|March|April|May|June|July|August|"
    "September|October|November|December"
)
_HEADING_RE = re.compile(
    rf"^(?P<date>(?:{_MONTHS})\s+\d{{1,2}},\s+\d{{4}})\s*\((?P<body>[^)]+)\)\s*$",
    re.IGNORECASE,
)
_FAMILY_VER_RE = re.compile(
    r"(?:Mevo\s+)?(?P<family>Start|Plus|Core)(?:\s+FW)?\s+(?P<version>\d+(?:\.\d+)*)",
    re.IGNORECASE,
)


class ArchiveError(RuntimeError):
    pass


@dataclass
class FirmwareImage:
    family: str
    mevo_types: list[str]
    version: str
    min_required_version: str
    filename: str
    sha512: str
    path: str
    bytes: int
    notes_path: str | None
    notes_excerpt: str
    app_version: str | None
    source: str
    protocol: str
    signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "label": FAMILY_LABELS.get(self.family, self.family),
            "mevo_types": self.mevo_types,
            "version": self.version,
            "min_required_version": self.min_required_version,
            "filename": self.filename,
            "sha512": self.sha512,
            "path": self.path,
            "bytes": self.bytes,
            "notes_path": self.notes_path,
            "notes_excerpt": self.notes_excerpt,
            "app_version": self.app_version,
            "source": self.source,
            "protocol": self.protocol,
            "signature": self.signature,
        }


def version_tuple(value: str | None) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", value or "")]
    return tuple(parts) if parts else (0,)


def version_newer(candidate: str | None, current: str | None) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    left = version_tuple(candidate)
    right = version_tuple(current)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


def parse_changelog(text: str | None) -> list[dict[str, Any]]:
    """Normalize APK release notes to the Mevo app changelog layout.

    Official dialog format (help.mevo.com firmware GIF)::

        March 26, 2021 (Start FW 2.4.7, Plus FW 2.4.4)
        - Bug fixes
    """
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        heading = _parse_heading(line)
        if heading is not None:
            if current is not None:
                entries.append(current)
            current = heading
            continue
        if current is None:
            current = {
                "date": "",
                "heading": line,
                "versions": {},
                "items": [],
            }
        current["items"].append(_changelog_item(line))
    if current is not None:
        entries.append(current)
    return entries


def changelog_since(
    entries: list[dict[str, Any]],
    current: str | None,
    family: str | None = None,
) -> list[dict[str, Any]]:
    family_key = (family or "").lower()
    out: list[dict[str, Any]] = []
    for entry in entries:
        versions = entry.get("versions") or {}
        version = versions.get(family_key) if family_key else None
        if version:
            if version_newer(version, current):
                out.append(entry)
            continue
        others = [value for value in versions.values() if value]
        if not others or any(version_newer(value, current) for value in others):
            out.append(entry)
    return out


def load_changelog(image: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not image:
        return []
    rel = str(image.get("notes_path") or "")
    if not rel:
        excerpt = str(image.get("notes_excerpt") or "")
        return parse_changelog(excerpt) if excerpt else []
    path = store_dir() / rel
    if not path.is_file():
        return []
    return parse_changelog(path.read_text(encoding="utf-8"))


def attach_changelog(image: dict[str, Any]) -> dict[str, Any]:
    item = dict(image)
    changelog = load_changelog(item)
    item["changelog"] = changelog
    if changelog:
        item["notes_excerpt"] = _changelog_excerpt(changelog)
    return item


def _parse_heading(line: str) -> dict[str, Any] | None:
    match = _HEADING_RE.match(line)
    if match is None:
        return None
    date = re.sub(r"\s+", " ", match.group("date")).strip()
    body = match.group("body").strip()
    found: list[str] = []
    versions: dict[str, str] = {}
    for part in re.split(r"\s*(?:,|/)\s*", body):
        item = _FAMILY_VER_RE.search(part)
        if item is None:
            continue
        family = item.group("family").lower()
        version = item.group("version")
        versions[family] = version
        found.append(f"{FAMILY_SHORT.get(family, item.group('family'))} FW {version}")
    heading = f"{date} ({', '.join(found)})" if found else f"{date} ({body})"
    return {
        "date": date,
        "heading": heading,
        "versions": versions,
        "items": [],
    }


def _changelog_item(line: str) -> str:
    text = re.sub(r"^[-*•]+\s*", "", line).strip()
    return text


def _changelog_excerpt(entries: list[dict[str, Any]], limit: int = 280) -> str:
    parts: list[str] = []
    for entry in entries:
        if entry.get("heading"):
            parts.append(str(entry["heading"]))
        for item in entry.get("items") or []:
            parts.append(f"- {item}")
    compact = " ".join(parts).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def family_from_type(mevo_type: str) -> str:
    return FAMILY_BY_TYPE.get(str(mevo_type), f"type-{mevo_type}")


def family_from_camera(camera: Any) -> str:
    if isinstance(camera, dict):
        txt = camera.get("txt") or {}
        model = str(camera.get("model") or "")
        name = str(camera.get("name") or "")
    else:
        txt = getattr(camera, "txt", None) or {}
        model = str(getattr(camera, "model", "") or "")
        name = str(getattr(camera, "name", "") or "")
    mevo_type = str(txt.get("mevo_type") or txt.get("type") or "")
    if mevo_type in FAMILY_BY_TYPE:
        return FAMILY_BY_TYPE[mevo_type]
    blob = " ".join(
        [
            model,
            name,
            str(txt.get("model") or ""),
            str(txt.get("hw") or ""),
            str(txt.get("hwid") or ""),
            str(txt.get("camera_name") or ""),
        ]
    ).lower()
    if "plus" in blob:
        return "plus"
    if "core" in blob:
        return "core"
    return "start"


def camera_fw_version(camera: Any) -> str | None:
    if isinstance(camera, dict):
        txt = camera.get("txt") or {}
    else:
        txt = getattr(camera, "txt", None) or {}
    for key in ("fw_version", "fw_version_mevo", "firmware", "version"):
        value = txt.get(key)
        if value:
            return str(value)
    return None


def walk_firmware_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    protocol = payload.get("protocol") or {}
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for proto_id, proto in protocol.items():
        types = (proto or {}).get("mevo_type") or {}
        for type_id, entry in types.items():
            if not isinstance(entry, dict):
                continue
            family = family_from_type(str(type_id))
            sha = str(entry.get("sha512") or "")
            key = (family, sha or str(entry.get("url") or type_id))
            item = grouped.setdefault(
                key,
                {
                    "family": family,
                    "mevo_types": [],
                    "version": str(entry.get("version") or ""),
                    "min_required_version": str(entry.get("min_required_version") or ""),
                    "filename": str(entry.get("url") or ""),
                    "sha512": sha,
                    "signature": str(entry.get("signature") or ""),
                    "release_notes_url": str(entry.get("release_notes_url") or ""),
                    "protocol": str(proto_id),
                },
            )
            if str(type_id) not in item["mevo_types"]:
                item["mevo_types"].append(str(type_id))
    rows.extend(grouped.values())
    return rows


def latest_images(catalog: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    catalog = catalog if catalog is not None else load_catalog()
    best: dict[str, dict[str, Any]] = {}
    for image in catalog.get("images") or []:
        family = image.get("family") or ""
        if family not in best or version_newer(image.get("version"), best[family].get("version")):
            best[family] = image
    order = ("start", "plus", "core")
    out = [best[key] for key in order if key in best]
    out.extend(value for key, value in best.items() if key not in order)
    return out


def latest_for_family(family: str, catalog: dict[str, Any] | None = None) -> dict[str, Any] | None:
    for image in latest_images(catalog):
        if image.get("family") == family:
            return image
    return None


def camera_firmware_view(camera: Any, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    family = family_from_camera(camera)
    current = camera_fw_version(camera)
    image = latest_for_family(family, catalog)
    available = image.get("version") if image else None
    if isinstance(camera, dict):
        cam_id = camera.get("id")
        name = (camera.get("txt") or {}).get("camera_name") or camera.get("name")
    else:
        cam_id = getattr(camera, "id", None)
        name = (getattr(camera, "txt", None) or {}).get("camera_name") or getattr(camera, "name", None)
    return {
        "id": cam_id,
        "name": name,
        "family": family,
        "label": FAMILY_LABELS.get(family, family),
        "current": current,
        "available": available,
        "update": bool(available and version_newer(available, current)),
        "sha512": image.get("sha512") if image else None,
        "signature": image.get("signature") if image else None,
        "blob": image.get("path") if image else None,
    }


def _find_member(names: Iterable[str], filename: str) -> str | None:
    target = filename.replace("\\", "/").lstrip("/")
    if not target:
        return None
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized == target or normalized.endswith("/" + target):
            return name
        if Path(normalized).name == Path(target).name and "firmware" in normalized:
            return name
    return None


def _copy_hashed(source, dest: Path) -> tuple[str, int]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha512()
    size = 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    with tmp.open("wb") as handle:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            handle.write(chunk)
            size += len(chunk)
    tmp.replace(dest)
    return digest.hexdigest(), size


def _unwrap_bundle(path: Path, tmp: Path) -> Path:
    suffix = path.suffix.lower()
    if suffix not in {".apks", ".xapk", ".apkm"}:
        return path
    with zipfile.ZipFile(path) as bundle:
        names = bundle.namelist()
        candidates = [name for name in names if name.replace("\\", "/").endswith("base.apk")]
        if not candidates:
            apks = [name for name in names if name.lower().endswith(".apk")]
            if not apks:
                raise ArchiveError(f"{path.name} contains no APK")
            candidates = [max(apks, key=lambda name: bundle.getinfo(name).file_size)]
        target = tmp / "base.apk"
        with bundle.open(candidates[0]) as src, target.open("wb") as dest:
            shutil.copyfileobj(src, dest, length=1024 * 1024)
        return target


def _excerpt(text: str, limit: int = 280) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _merge_images(catalog: dict[str, Any], images: list[dict[str, Any]]) -> dict[str, Any]:
    existing = catalog.get("images") or []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in existing:
        by_key[(item.get("family") or "", item.get("sha512") or item.get("filename") or "")] = item
    for item in images:
        by_key[(item.get("family") or "", item.get("sha512") or item.get("filename") or "")] = item
    catalog["images"] = list(by_key.values())
    return catalog


def ingest_apk(
    path: Path | str,
    *,
    app_version: str | None = None,
    source: str = "local",
    source_url: str | None = None,
    version_code: str | None = None,
) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise ArchiveError(f"APK not found: {path}")
    with tempfile.TemporaryDirectory(prefix="mevo-fw-") as tmp:
        apk_path = _unwrap_bundle(path, Path(tmp))
        return _ingest_apk_zip(
            apk_path,
            app_version=app_version,
            source=source,
            source_url=source_url,
            version_code=version_code,
            source_path=str(path),
        )


def _ingest_apk_zip(
    apk_path: Path,
    *,
    app_version: str | None,
    source: str,
    source_url: str | None,
    version_code: str | None,
    source_path: str,
) -> dict[str, Any]:
    root = store_dir()
    with zipfile.ZipFile(apk_path) as archive:
        names = archive.namelist()
        json_name = _find_member(names, "assets/firmware/firmware.json")
        if json_name is None:
            raise ArchiveError(f"{Path(source_path).name} has no assets/firmware/firmware.json")
        payload = json.loads(archive.read(json_name).decode("utf-8"))
        images: list[dict[str, Any]] = []
        copied = 0
        for row in walk_firmware_json(payload):
            filename = row["filename"]
            member = _find_member(names, filename)
            if member is None:
                raise ArchiveError(f"Missing firmware blob {filename}")
            expected = row["sha512"]
            dest_name = f"{(expected or 'blob')[:16]}-{Path(filename).name}"
            dest = root / "blobs" / dest_name
            rel = f"blobs/{dest_name}"
            if dest.exists() and expected:
                sha = expected
                size = dest.stat().st_size
            else:
                with archive.open(member) as handle:
                    sha, size = _copy_hashed(handle, dest)
                copied += 1
                if expected and sha != expected:
                    dest.unlink(missing_ok=True)
                    raise ArchiveError(f"sha512 mismatch for {filename}")
            notes_rel = None
            excerpt = ""
            notes_name = row.get("release_notes_url") or ""
            notes_member = _find_member(names, notes_name) if notes_name else None
            if notes_member:
                notes = archive.read(notes_member).decode("utf-8", "replace")
                notes_path = root / "notes" / f"{row['family']}-{row['version']}.txt"
                notes_path.parent.mkdir(parents=True, exist_ok=True)
                notes_path.write_text(notes, encoding="utf-8")
                notes_rel = f"notes/{notes_path.name}"
                excerpt = _excerpt(notes)
            images.append(
                FirmwareImage(
                    family=row["family"],
                    mevo_types=row["mevo_types"],
                    version=row["version"],
                    min_required_version=row["min_required_version"],
                    filename=filename,
                    sha512=sha,
                    path=rel,
                    bytes=size,
                    notes_path=notes_rel,
                    notes_excerpt=excerpt,
                    app_version=app_version,
                    source=source,
                    protocol=row["protocol"],
                    signature=str(row.get("signature") or ""),
                ).to_dict()
            )
    catalog = load_catalog()
    _merge_images(catalog, images)
    if app_version:
        catalog["app"] = {
            "version": app_version,
            "version_code": version_code,
            "source": source,
            "source_url": source_url,
            "apk": Path(source_path).name,
        }
    save_catalog(catalog)
    return {
        "ok": True,
        "apk": Path(source_path).name,
        "copied": copied,
        "images": images,
        "app_version": app_version,
    }
