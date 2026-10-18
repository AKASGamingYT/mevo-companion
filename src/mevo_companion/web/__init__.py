from pathlib import Path

from mevo_companion.runtime import bundle_dir, is_frozen


def _web_dir() -> Path:
    if is_frozen():
        return bundle_dir() / "mevo_companion" / "web"
    return Path(__file__).resolve().parent


WEB_DIR = _web_dir()

