"""Tiny key-value store for app-level state. Survives across launches."""
from __future__ import annotations

import json
from typing import Any

from app.core.paths import STATE_FILE, ensure_dirs


def load_state() -> dict[str, Any]:
    ensure_dirs()
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_setup_complete() -> bool:
    return bool(load_state().get("setup_complete"))


def mark_setup_complete() -> None:
    state = load_state()
    state["setup_complete"] = True
    save_state(state)
