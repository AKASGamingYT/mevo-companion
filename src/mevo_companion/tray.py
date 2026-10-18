"""System tray icon for Mevo Companion."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from mevo_companion.runtime import bundle_dir, is_frozen


def icon_path() -> Path:
    if is_frozen():
        return bundle_dir() / "mevo_companion" / "assets" / "mevo.png"
    return Path(__file__).resolve().parent / "assets" / "mevo.png"


def load_icon() -> Image.Image:
    image = Image.open(icon_path()).convert("RGBA")
    # The tray slot is small. A 64px master stays sharper than scaling the full icon.
    if max(image.size) != 64:
        image = image.resize((64, 64), Image.Resampling.LANCZOS)
    return image


def run_tray(url: str, on_ready: Callable[..., None] | None = None) -> None:
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
    if on_ready is not None:
        on_ready(icon)
    icon.run()
