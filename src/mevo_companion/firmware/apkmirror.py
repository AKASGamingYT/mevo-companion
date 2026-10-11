"""APKMirror client modeled on tanishqmanuja/apkmirror-downloader.

Listing pages are ordinary HTML. Download pages sit behind Cloudflare, so
requests use Chrome TLS impersonation (curl_cffi) instead of plain httpx.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from curl_cffi import requests

BASE = "https://www.apkmirror.com"
MEVO_ORG = "logitech-europe-s-a"
MEVO_REPO = "mevo-the-live-event-camera"


class APKMirrorError(RuntimeError):
    pass


@dataclass
class Version:
    name: str
    version: str
    url: str


@dataclass
class Variant:
    version: str
    type: str
    arch: str
    min_android: str
    dpi: str
    url: str
    version_code: str = ""


def with_base(url: str) -> str:
    if url.startswith("http"):
        return url
    return urljoin(BASE + "/", url.lstrip("/"))


def repo_url(org: str = MEVO_ORG, repo: str = MEVO_REPO) -> str:
    return f"{BASE}/apk/{org}/{repo}/"


def _session() -> requests.Session:
    session = requests.Session(impersonate="chrome")
    session.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def _get(session: requests.Session, url: str, referer: str | None = None) -> str:
    headers = {"Referer": referer} if referer else {}
    response = session.get(url, headers=headers, timeout=45)
    text = response.text
    if response.status_code >= 400 or "Just a moment" in text or "you have been blocked" in text.lower():
        raise APKMirrorError(f"APKMirror blocked or failed ({response.status_code}) for {url}")
    if "Enable JavaScript and cookies to continue" in text:
        raise APKMirrorError("APKMirror requires JavaScript/cookies")
    return text


def app_version_from_name(name: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)+)", name)
    return match.group(1) if match else name.strip()


def extract_versions(html: str) -> list[Version]:
    versions: list[Version] = []
    pattern = re.compile(
        r'<h5[^>]*class="[^"]*appRowTitle[^"]*"[^>]*>\s*'
        r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<name>.*?)</a>',
        re.I | re.S,
    )
    for match in pattern.finditer(html):
        name = re.sub(r"<[^>]+>", "", match.group("name")).strip()
        if not name:
            continue
        versions.append(
            Version(name=name, version=app_version_from_name(name), url=with_base(match.group("href")))
        )
    if not versions:
        raise APKMirrorError("Could not find versions table on APKMirror")
    return versions


def is_stable(version: Version) -> bool:
    lower = version.name.lower()
    return "alpha" not in lower and "beta" not in lower


def extract_variants(html: str) -> list[Variant]:
    start = html.find("variants-table")
    if start < 0:
        raise APKMirrorError("Could not find variants table")
    marker = html.find('name="all_releases"', start)
    block = html[start:marker] if marker > start else html[start : start + 30000]
    chunks = re.split(r'<div class="table-row', block)
    variants: list[Variant] = []
    for chunk in chunks[2:]:
        href_match = re.search(r'<a class="accent_color" href="([^"]+)"', chunk)
        if not href_match:
            continue
        version_match = re.search(
            r'<a class="accent_color" href="[^"]+">\s*([^<]+?)\s*</a>',
            chunk,
        )
        badge = "bundle" if re.search(r">\s*BUNDLE\s*<", chunk) else "apk" if re.search(r">\s*APK\s*<", chunk) else "unknown"
        code_match = re.search(r'<span class="colorLightBlack">\s*(\d+)\s*</span>', chunk)
        simple_cells = [
            item.strip()
            for item in re.findall(
                r'<div class="table-cell[^"]*"[^>]*>\s*([^<]+?)\s*</div>',
                chunk,
            )
        ]
        arch = simple_cells[-3] if len(simple_cells) >= 3 else (simple_cells[0] if simple_cells else "")
        min_android = simple_cells[-2] if len(simple_cells) >= 2 else ""
        dpi = simple_cells[-1] if simple_cells else ""

        variants.append(
            Variant(
                version=(version_match.group(1).strip() if version_match else ""),
                type=badge,
                arch=arch,
                min_android=min_android,
                dpi=dpi,
                url=with_base(href_match.group(1)),
                version_code=code_match.group(1) if code_match else "",
            )
        )
    if not variants:
        raise APKMirrorError("No APK variants listed")
    return variants


def pick_variant(variants: list[Variant]) -> Variant:
    def score(item: Variant) -> tuple[int, int, int]:
        apk_score = 2 if item.type == "apk" else 1 if item.type == "bundle" else 0
        arch = item.arch.lower()
        arch_score = 2 if "universal" in arch or arch == "noarch" else 1 if "arm64" in arch else 0
        return (apk_score, arch_score, 1 if item.dpi else 0)

    ranked = sorted(variants, key=score, reverse=True)
    if not ranked or score(ranked[0])[0] == 0:
        raise APKMirrorError("No APK or bundle variant found")
    return ranked[0]


def extract_redirect_download_url(html: str) -> str:
    match = re.search(r'class="[^"]*downloadButton[^"]*"[^>]*href="([^"]+)"', html)
    if not match:
        match = re.search(r'href="([^"]+)"[^>]*class="[^"]*downloadButton', html)
    if not match:
        raise APKMirrorError("Could not find download button")
    return with_base(match.group(1))


def extract_final_download_url(html: str) -> str:
    match = re.search(r'id="download-link"[^>]*href="([^"]+)"', html)
    if not match:
        match = re.search(
            r'class="card-with-tabs[^"]*"[\s\S]{0,2000}?<a[^>]+href="([^"]+)"',
            html,
        )
    if not match:
        raise APKMirrorError("Could not find final download link")
    return with_base(match.group(1))


def latest_mevo_app(session: requests.Session | None = None) -> Version:
    own = session or _session()
    versions = extract_versions(_get(own, repo_url()))
    selected = next((item for item in versions if is_stable(item)), None)
    if selected is None:
        raise APKMirrorError("No stable Mevo app version on APKMirror")
    return selected


def download_mevo(
    dest_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
    selected: Version | None = None,
) -> dict[str, str]:
    """Download the latest stable Mevo APK/bundle. Returns metadata including `path`."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    session = _session()
    log = progress or (lambda _msg: None)
    listing = repo_url()
    log("Checking APKMirror for Logitech Mevo…")
    _get(session, listing)
    selected = selected or latest_mevo_app(session)
    log(f"Latest app {selected.version} ({selected.name})")
    variants = extract_variants(_get(session, selected.url, referer=listing))
    variant = pick_variant(variants)
    log(f"Variant {variant.type} {variant.arch} {variant.dpi} code={variant.version_code}")
    download_html = _get(session, variant.url, referer=selected.url)
    redirect = extract_redirect_download_url(download_html)
    final_html = _get(session, redirect, referer=variant.url)
    final_url = extract_final_download_url(final_html)
    suffix = ".apks" if variant.type == "bundle" else ".apk"
    filename = f"mevo-{selected.version}{suffix}"
    dest = dest_dir / filename
    if dest.exists() and dest.stat().st_size > 0:
        log(f"Already have {dest.name}")
        return {
            "path": str(dest),
            "version": selected.version,
            "version_code": variant.version_code,
            "type": variant.type,
            "source_url": selected.url,
            "bytes": str(dest.stat().st_size),
        }
    log(f"Downloading {filename}…")
    response = session.get(final_url, headers={"Referer": redirect}, timeout=180, stream=True)
    if response.status_code >= 400:
        raise APKMirrorError(f"download failed ({response.status_code})")
    tmp = dest.with_suffix(dest.suffix + ".part")
    size = 0
    next_note = 8 * 1024 * 1024
    with tmp.open("wb") as handle:
        for chunk in response.iter_content(1024 * 1024):
            if not chunk:
                continue
            handle.write(chunk)
            size += len(chunk)
            if size >= next_note:
                log(f"Downloading {filename}… {size // (1024 * 1024)} MB")
                next_note += 8 * 1024 * 1024
    tmp.replace(dest)
    log(f"Saved {dest.name} ({size} bytes)")
    return {
        "path": str(dest),
        "version": selected.version,
        "version_code": variant.version_code,
        "type": variant.type,
        "source_url": selected.url,
        "bytes": str(size),
    }
