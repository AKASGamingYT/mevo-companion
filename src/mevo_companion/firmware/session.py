"""Windows sign-in time and network connection, without opening a console."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import os


def network_connected() -> bool:
    """True when Windows reports a configured network connection."""
    if os.name != "nt":
        return True
    flags = wintypes.DWORD()
    wininet = ctypes.WinDLL("wininet", use_last_error=True)
    wininet.InternetGetConnectedState.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
    ]
    wininet.InternetGetConnectedState.restype = wintypes.BOOL
    connected = bool(wininet.InternetGetConnectedState(ctypes.byref(flags), 0))
    offline = 0x20
    return connected and not bool(flags.value & offline)


def current_logon_time() -> datetime | None:
    """Interactive sign-in time for this Windows session, in UTC."""
    if os.name != "nt":
        return None
    try:
        return _windows_logon_time()
    except OSError:
        return None


def _windows_logon_time() -> datetime | None:
    class WTSINFOW(ctypes.Structure):
        _fields_ = [
            ("State", wintypes.DWORD),
            ("SessionId", wintypes.DWORD),
            ("IncomingBytes", wintypes.DWORD),
            ("OutgoingBytes", wintypes.DWORD),
            ("IncomingFrames", wintypes.DWORD),
            ("OutgoingFrames", wintypes.DWORD),
            ("IncomingCompressedBytes", wintypes.DWORD),
            ("OutgoingCompressedBytes", wintypes.DWORD),
            ("WinStationName", wintypes.WCHAR * 32),
            ("Domain", wintypes.WCHAR * 17),
            ("UserName", wintypes.WCHAR * 21),
            ("ConnectTime", wintypes.LARGE_INTEGER),
            ("DisconnectTime", wintypes.LARGE_INTEGER),
            ("LastInputTime", wintypes.LARGE_INTEGER),
            ("LogonTime", wintypes.LARGE_INTEGER),
            ("CurrentTime", wintypes.LARGE_INTEGER),
        ]

    wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
    wts.WTSQuerySessionInformationW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    wts.WTSQuerySessionInformationW.restype = wintypes.BOOL
    wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    wts.WTSFreeMemory.restype = None

    current_session = 0xFFFFFFFF
    session_info = 24
    buffer = ctypes.c_void_p()
    size = wintypes.DWORD()
    ok = wts.WTSQuerySessionInformationW(
        None,
        current_session,
        session_info,
        ctypes.byref(buffer),
        ctypes.byref(size),
    )
    if not ok or not buffer:
        return None
    try:
        info = ctypes.cast(buffer, ctypes.POINTER(WTSINFOW)).contents
        return _filetime_to_datetime(int(info.LogonTime))
    finally:
        wts.WTSFreeMemory(buffer)


def _filetime_to_datetime(value: int) -> datetime | None:
    if value <= 0:
        return None
    return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=value / 10)
