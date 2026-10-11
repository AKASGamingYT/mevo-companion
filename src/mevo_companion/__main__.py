from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
import webbrowser

import httpx
import uvicorn

from mevo_companion import DEFAULT_HOST, DEFAULT_PORT
from mevo_companion.api import create_app
from mevo_companion.autostart import AutostartError, ensure_autostart, install_autostart, uninstall_autostart
from mevo_companion.discovery import CameraRegistry
from mevo_companion.singleton import AlreadyRunning, InstanceLock


class _SilentStream:
    """File-like sink whose isatty() is false.

    Windows reports the NUL device as a console, so opening os.devnull is
    not enough: uvicorn would still take the color-logging path.
    """

    def __init__(self) -> None:
        self._raw = open(os.devnull, "w", encoding="utf-8")

    def write(self, data: str) -> int:
        return self._raw.write(data)

    def flush(self) -> None:
        self._raw.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._raw.fileno()

    def writable(self) -> bool:
        return True

    def close(self) -> None:
        self._raw.close()


def _ensure_stdio() -> None:
    """Give pythonw.exe real stdout and stderr streams.

    The windowless interpreter sets both to None. Uvicorn's log formatter
    calls isatty() while configuring logging, and that exits the process
    before the tray or API can start.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    sink = _SilentStream()
    if sys.stdout is None:
        sys.stdout = sink  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = sink  # type: ignore[assignment]


def main(argv: list[str] | None = None) -> int:
    _ensure_stdio()
    parser = argparse.ArgumentParser(description="Mevo Companion desktop app")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Run the local API only (for Stream Deck / headless use)",
    )
    parser.add_argument(
        "--window",
        action="store_true",
        help="Open the desktop window instead of the system tray",
    )
    parser.add_argument(
        "--open-ui",
        action="store_true",
        help="Open the web UI in a browser after the API starts",
    )
    parser.add_argument(
        "--install-startup",
        action="store_true",
        help="Start in the system tray at Windows sign-in",
    )
    parser.add_argument(
        "--uninstall-startup",
        action="store_true",
        help="Stop launching Mevo Companion at Windows sign-in",
    )
    args = parser.parse_args(argv)
    if args.install_startup and args.uninstall_startup:
        parser.error("use either --install-startup or --uninstall-startup")
    if args.no_window and args.window:
        parser.error("use either --window or --no-window")
    if args.uninstall_startup:
        try:
            info = uninstall_autostart()
        except AutostartError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("Removed sign-in startup")
        print(info["path"])
        return 0
    if args.install_startup:
        try:
            info = install_autostart()
        except AutostartError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("Mevo Companion will start at sign-in and stay in the system tray")
        print(info["path"])
        return 0

    url = f"http://{args.host}:{args.port}"
    open_ui = args.open_ui

    lock = InstanceLock()
    try:
        lock.acquire()
    except AlreadyRunning:
        return _already_running(url, open_ui=args.open_ui or not args.no_window)

    try:
        ensure_autostart()
    except AutostartError as exc:
        print(f"Could not register sign-in startup: {exc}", file=sys.stderr, flush=True)

    registry = CameraRegistry()
    registry.start()
    app = create_app(registry)
    server = None
    try:
        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        _wait_for_server(url)
        from mevo_companion.firmware.login_check import start_login_check

        start_login_check()
        if args.open_ui:
            webbrowser.open(url)

        if args.no_window:
            print(f"Mevo Companion API at {url}", flush=True)
            print("Ctrl+C to stop", flush=True)
            while thread.is_alive():
                time.sleep(0.4)
        elif args.window:
            _open_window(url)
        else:
            try:
                from mevo_companion.tray import run_tray

                run_tray(url)
            except Exception as exc:
                print(f"System tray unavailable ({exc}). API is still running.", flush=True)
                print("Ctrl+C to stop", flush=True)
                while thread.is_alive():
                    time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.should_exit = True
        preview = getattr(app.state, "preview", None)
        if preview is not None:
            preview.stop()
        registry.stop()
        lock.release()
    return 0


def _already_running(url: str, *, open_ui: bool) -> int:
    print("Mevo Companion is already running.", file=sys.stderr, flush=True)
    print(f"Use the existing instance at {url}", file=sys.stderr, flush=True)
    if open_ui and not _focus_existing_window():
        try:
            httpx.get(f"{url}/api/health", timeout=0.4)
            webbrowser.open(url)
        except httpx.HTTPError:
            pass
    return 1


def _focus_existing_window(title: str = "Mevo Companion") -> bool:
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def _wait_for_server(url: str, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(f"{url}/api/health", timeout=0.4)
            return
        except httpx.HTTPError:
            time.sleep(0.05)
    raise RuntimeError(f"API did not start at {url}")


def _open_window(url: str) -> None:
    try:
        import webview
    except ImportError:
        webbrowser.open(url)
        print(f"Opened browser at {url} (pywebview not installed)")
        while True:
            time.sleep(1)

    webview.create_window(
        "Mevo Companion",
        url,
        width=1180,
        height=880,
        min_size=(860, 640),
        background_color="#141414",
    )
    webview.start()


if __name__ == "__main__":
    sys.exit(main())
