"""Saved app sessions, keyed by provider + workspace, so sends continue one conversation.

Stored in <per-user data dir>/sessions.json. Session ids are not secrets but the
file stays in the per-user data folder.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .adapters import AdapterError
from .codex_app import UUID
from .peers import _atomic_write, _now, data_dir

PROVIDERS = ("codex_app", "claude_app")


def path(root=None):
    return Path(root or data_dir()) / "sessions.json"


def load(root=None):
    try:
        document = json.loads(path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def key(provider, workspace):
    if provider not in PROVIDERS:
        raise AdapterError("Unknown session provider")
    return f"{provider}|{workspace}"


def get(provider, workspace, root=None):
    entry = load(root).get(key(provider, workspace))
    return entry.get("session_id") if isinstance(entry, dict) else None


def save(provider, workspace, session_id, root=None):
    if not isinstance(session_id, str) or not re.fullmatch(UUID, session_id):
        raise AdapterError("Invalid session id")
    document = load(root)
    document[key(provider, workspace)] = {"provider": provider, "workspace": workspace,
                                          "session_id": session_id, "updated": _now()}
    Path(path(root)).parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path(root), document)


def reset(provider=None, root=None):
    document = load(root)
    kept = {k: v for k, v in document.items() if provider is not None and not k.startswith(provider + "|")}
    removed = len(document) - len(kept)
    Path(path(root)).parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path(root), kept)
    return {"status": "reset", "removed": removed, "provider": provider or "all"}


def listing(root=None):
    return list(load(root).values())
