"""Write GitHub release notes for the current tag."""

from __future__ import annotations

import os
from pathlib import Path


def notes_for(version: str) -> str:
    return f"""Mevo Companion {version} is ready to install.

| Platform | Download |
| --- | --- |
| Windows 10 or later, 64-bit | `MevoCompanion-Setup-x64.exe` |
| Mac, Apple silicon | `MevoCompanion-macOS.dmg` |
| Linux, Debian and Ubuntu, 64-bit | `MevoCompanion-linux-amd64.deb` |

The Windows installer can also install the Stream Deck plugin. `MevoCompanion-StreamDeck.zip` is that plugin on its own.

Portable builds:

- `MevoCompanion-windows-x64.zip`
- `MevoCompanion-linux-x64.tar.gz`

`SHA256SUMS.txt` lists a checksum for every file in this release.
"""


def main() -> None:
    ref = os.environ.get("GITHUB_REF_NAME", "v0.1.0")
    version = ref.removeprefix("v")
    dest = Path(os.environ.get("RELEASE_NOTES", "notes.md"))
    dest.write_text(notes_for(version), encoding="utf-8")
    print(dest.resolve())


if __name__ == "__main__":
    main()
