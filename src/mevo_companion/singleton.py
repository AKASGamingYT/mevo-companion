"""Ensure only one Mevo Companion process is running."""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path


DEFAULT_KEY = "MevoCompanion"


class AlreadyRunning(RuntimeError):
    """Raised when another Mevo Companion process holds the instance lock."""

    def __init__(self, key: str = DEFAULT_KEY) -> None:
        super().__init__("Mevo Companion is already running")
        self.key = key


class InstanceLock:
    """OS-level lock that is released when this process exits or crashes.

    Windows uses a named mutex. Other platforms use an exclusive file lock.
    """

    def __init__(self, key: str = DEFAULT_KEY) -> None:
        self.key = key or DEFAULT_KEY
        self._handle: int | None = None
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._handle is not None or self._fd is not None:
            return
        if sys.platform == "win32":
            self._acquire_mutex()
        else:
            self._acquire_file()

    def release(self) -> None:
        if sys.platform == "win32":
            self._release_mutex()
        else:
            self._release_file()

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    def _mutex_name(self) -> str:
        return f"Local\\{self.key}"

    def _lock_path(self) -> Path:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in self.key)
        return Path(runtime) / f"{safe}.lock"

    def _acquire_mutex(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, self._mutex_name())
        error = ctypes.get_last_error()
        already_exists = 183
        access_denied = 5
        if not handle:
            if error in (already_exists, access_denied):
                raise AlreadyRunning(self.key)
            raise OSError(error, f"CreateMutexW failed ({error})")
        if error == already_exists:
            kernel32.CloseHandle(handle)
            raise AlreadyRunning(self.key)
        self._handle = handle

    def _release_mutex(self) -> None:
        handle = self._handle
        self._handle = None
        if handle:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(handle)

    def _acquire_file(self) -> None:
        import fcntl

        path = self._lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        except BlockingIOError as exc:
            os.close(fd)
            raise AlreadyRunning(self.key) from exc
        except Exception:
            os.close(fd)
            raise
        self._fd = fd

    def _release_file(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
