"""Build a Mevo Companion binary, then the installer for this platform.

Windows produces a folder build and a portable zip. Inno Setup, run by the
release workflow, turns that folder into MevoCompanion-Setup-x64.exe.
macOS produces a disk image. Linux produces a .deb and a tarball.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
APP_NAME = "MevoCompanion"


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True, cwd=ROOT)


def _pyinstaller_args() -> list[str]:
    web = ROOT / "src" / "mevo_companion" / "web"
    assets = ROOT / "src" / "mevo_companion" / "assets"
    sep = os.pathsep
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--distpath",
        str(DIST),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(ROOT / "build"),
        "--paths",
        str(ROOT / "src"),
        f"--add-data={web}{sep}mevo_companion/web",
        f"--add-data={assets}{sep}mevo_companion/assets",
        f"--runtime-hook={ROOT / 'packaging' / 'rthook_ndi.py'}",
        "--collect-submodules",
        "uvicorn",
        "--hidden-import",
        "httptools",
        "--hidden-import",
        "websockets",
        "--hidden-import",
        "watchfiles",
    ]
    for package in ("curl_cffi", "cyndilib", "zeroconf"):
        try:
            __import__(package)
        except ImportError:
            print(f"Skipping {package}: it is not installed on this platform", flush=True)
            continue
        args.extend(["--collect-all", package])
    if sys.platform == "linux":
        # GTK and WebKit are system libraries. The Linux build opens the
        # browser when a tray icon is not available.
        args.extend(
            [
                "--console",
                "--exclude-module",
                "webview",
                "--exclude-module",
                "pystray",
            ]
        )
    else:
        args.append("--windowed")
        args.extend(["--hidden-import", "webview", "--hidden-import", "pystray"])
    if sys.platform == "win32":
        icon = assets / "mevo.ico"
        if icon.exists():
            args.extend(["--icon", str(icon)])
        args.append("--hidden-import=pystray._win32")
    elif sys.platform == "darwin":
        icns = _make_icns(assets / "mevo.png")
        if icns is not None:
            args.extend(["--icon", str(icns)])
        args.append("--hidden-import=pystray._darwin")
    args.append(str(ROOT / "src" / "mevo_companion" / "__main__.py"))
    return args


def _make_icns(png: Path) -> Path | None:
    if not png.exists():
        return None
    iconset = ROOT / "build" / "MevoCompanion.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    sizes = (16, 32, 128, 256, 512)
    try:
        for size in sizes:
            _run(
                [
                    "sips",
                    "-z",
                    str(size),
                    str(size),
                    str(png),
                    "--out",
                    str(iconset / f"icon_{size}x{size}.png"),
                ]
            )
            doubled = size * 2
            _run(
                [
                    "sips",
                    "-z",
                    str(doubled),
                    str(doubled),
                    str(png),
                    "--out",
                    str(iconset / f"icon_{size}x{size}@2x.png"),
                ]
            )
        icns = ROOT / "build" / "MevoCompanion.icns"
        _run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)])
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Skipping macOS icon: {exc}", flush=True)
        return None
    return icns


def _prune(bundle: Path) -> None:
    if not bundle.exists():
        return
    for path in bundle.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if path.suffix.lower() in {".cpp", ".pyx", ".pxd", ".h", ".lib", ".exp"}:
            path.unlink()
            continue
        if "DirectShow" in name or ".UWP." in name:
            path.unlink()


def binary_dir() -> Path:
    if sys.platform == "darwin":
        return DIST / f"{APP_NAME}.app"
    return DIST / APP_NAME


def smoke_test() -> None:
    if sys.platform == "win32":
        exe = DIST / APP_NAME / f"{APP_NAME}.exe"
    elif sys.platform == "darwin":
        exe = DIST / f"{APP_NAME}.app" / "Contents" / "MacOS" / APP_NAME
    else:
        exe = DIST / APP_NAME / APP_NAME
    if not exe.exists():
        raise SystemExit(f"Missing built binary: {exe}")
    subprocess.run([str(exe), "--help"], check=True, cwd=ROOT, timeout=90)


def _zip_tree(source: Path, dest_stem: Path) -> None:
    if dest_stem.with_suffix(".zip").exists():
        dest_stem.with_suffix(".zip").unlink()
    shutil.make_archive(str(dest_stem), "zip", source.parent, source.name)


def _stage_windows() -> None:
    _zip_tree(DIST / APP_NAME, RELEASE / "MevoCompanion-windows-x64")
    plugin = ROOT / "streamdeck" / "com.mevo-companion.sdPlugin"
    if plugin.is_dir():
        _zip_tree(plugin, RELEASE / "MevoCompanion-StreamDeck")


def _stage_macos() -> None:
    app = DIST / f"{APP_NAME}.app"
    staging = ROOT / "build" / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    staged = staging / app.name
    shutil.copytree(app, staged)
    applications = staging / "Applications"
    if applications.exists() or applications.is_symlink():
        applications.unlink()
    applications.symlink_to("/Applications")
    try:
        _run(["codesign", "--force", "--deep", "--sign", "-", str(staged)])
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Ad-hoc codesign skipped: {exc}", flush=True)
    dmg = RELEASE / "MevoCompanion-macOS.dmg"
    if dmg.exists():
        dmg.unlink()
    _run(
        [
            "hdiutil",
            "create",
            "-volname",
            "Mevo Companion",
            "-srcfolder",
            str(staging),
            "-ov",
            "-format",
            "UDZO",
            str(dmg),
        ]
    )


def _deb_arch() -> str:
    try:
        return subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip() or "amd64"
    except (OSError, subprocess.CalledProcessError):
        return "amd64"


def _stage_linux(version: str) -> None:
    arch = _deb_arch()
    pkg = ROOT / "build" / "deb" / "mevo-companion"
    if pkg.exists():
        shutil.rmtree(pkg)
    opt = pkg / "opt" / "mevo-companion"
    shutil.copytree(DIST / APP_NAME, opt)
    icon = ROOT / "src" / "mevo_companion" / "assets" / "mevo.png"
    if icon.exists():
        shutil.copy(icon, opt / "mevo.png")
    launcher = pkg / "usr" / "bin" / "mevo-companion"
    launcher.parent.mkdir(parents=True)
    launcher.write_text('#!/bin/sh\nexec /opt/mevo-companion/MevoCompanion "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    apps = pkg / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "mevo-companion.desktop").write_text(
        "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                "Name=Mevo Companion",
                "Comment=Control Mevo cameras on your network",
                "Exec=mevo-companion --open-ui",
                "Icon=/opt/mevo-companion/mevo.png",
                "Terminal=false",
                "Categories=AudioVideo;Network;",
                "StartupNotify=true",
                "Actions=quit;",
                "",
                "[Desktop Action quit]",
                "Name=Quit Mevo Companion",
                "Exec=mevo-companion --quit",
                "",
            ]
        ),
        encoding="utf-8",
    )
    debian = pkg / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(
        "\n".join(
            [
                "Package: mevo-companion",
                f"Version: {version}",
                "Section: video",
                "Priority: optional",
                f"Architecture: {arch}",
                "Maintainer: Mevo Companion <https://github.com/AKASGamingYT/mevo-companion>",
                "Description: Desktop companion for Mevo cameras",
                " Finds cameras on the local network and opens a control panel.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    deb = RELEASE / f"MevoCompanion-linux-{arch}.deb"
    _run(["dpkg-deb", "--build", "--root-owner-group", str(pkg), str(deb)])
    tarball = RELEASE / "MevoCompanion-linux-x64.tar.gz"
    if tarball.exists():
        tarball.unlink()
    shutil.make_archive(str(RELEASE / "MevoCompanion-linux-x64"), "gztar", DIST, APP_NAME)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Mevo Companion for this platform")
    parser.add_argument(
        "--binary-only",
        action="store_true",
        help="Stop after the PyInstaller binary and smoke test",
    )
    args = parser.parse_args(argv)
    version = project_version()
    RELEASE.mkdir(parents=True, exist_ok=True)
    _run(_pyinstaller_args())
    _prune(binary_dir())
    smoke_test()
    if args.binary_only:
        print(f"Built {binary_dir()}", flush=True)
        return 0
    if sys.platform == "win32":
        _stage_windows()
    elif sys.platform == "darwin":
        _stage_macos()
    else:
        _stage_linux(version)
    print(f"Release files are in {RELEASE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
