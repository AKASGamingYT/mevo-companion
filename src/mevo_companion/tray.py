"""System tray icon for Mevo Companion."""

from __future__ import annotations

import webbrowser
from pathlib import Path

from PIL import Image


def icon_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / "mevo.png"


def load_icon() -> Image.Image:
    image = Image.open(icon_path()).convert("RGBA")
    # The tray slot is small. A 64px master stays sharper than scaling the full icon.
    if max(image.size) != 64:
        image = image.resize((64, 64), Image.Resampling.LANCZOS)
    return image


def run_tray(url: str) -> None:
    import pystray

    def open_ui(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        webbrowser.open(url)

    def quit_app(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open web UI", open_ui, default=True),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("mevo-companion", load_icon(), "Mevo Companion", menu)
    icon.run()
