from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def store_dir() -> Path:
    override = os.environ.get("MEVO_COMPANION_STORE")
    if override:
        return Path(override)
    return repo_root() / "firmware-store"


def catalog_path() -> Path:
    return store_dir() / "catalog.json"


def state_path() -> Path:
    return store_dir() / "state.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_catalog() -> dict[str, Any]:
    return load_json(catalog_path(), {"images": [], "app": None})


def save_catalog(catalog: dict[str, Any]) -> None:
    catalog["updated_at"] = utc_now()
    save_json(catalog_path(), catalog)


def load_state() -> dict[str, Any]:
    return load_json(
        state_path(),
        {
            "status": "idle",
            "last_started": None,
            "last_success": None,
            "last_error": None,
            "message": None,
        },
    )


def save_state(state: dict[str, Any]) -> None:
    save_json(state_path(), state)
