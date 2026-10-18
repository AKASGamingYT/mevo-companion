"""Put the bundled NDI libraries on the Windows DLL search path."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32" and getattr(sys, "frozen", False):
    dll_dir = Path(getattr(sys, "_MEIPASS", "")) / "cyndilib" / "wrapper" / "bin"
    if dll_dir.is_dir():
        os.add_dll_directory(str(dll_dir))
        os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
