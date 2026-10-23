"""Claude desktop app route: prefill only, the user presses Send.

``claude://code/new?q=...`` (Claude Code, default) or ``claude://claude.ai/new?q=...``
(chat) opens a new session with the prompt prefilled. Since Claude Desktop
1.1.2321 links never auto-submit, so this route never claims a send, never
automates keystrokes and never falls back to the claude CLI or API billing.
Claude Code and the Claude app require a paid Claude plan.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote

from .adapters import AdapterError
from .codex_app import PREFILLED, _prompt, _workspace
from .permissions import PermissionPolicy

PROVIDER = "claude_app"
MODES = ("prefill",)
SURFACES = {"code": "claude://code/new", "chat": "claude://claude.ai/new"}
DEFAULT_SURFACE = "code"
MAX_PROMPT_CHARS = 14_000  # Claude truncates q around here; refuse instead


def prefill_link(prompt, surface=DEFAULT_SURFACE, folder=None):
    if surface not in SURFACES:
        raise AdapterError("claude_app surface must be code or chat")
    prompt = _prompt(prompt)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise AdapterError(f"Prompt too long for a claude:// link ({len(prompt)} > {MAX_PROMPT_CHARS} chars); "
                           "Claude would silently truncate it, so it is refused")
    link = SURFACES[surface] + "?q=" + quote(prompt, safe="")
    if folder is not None:
        if surface != "code":
            raise AdapterError("folder is only supported for the code surface")
        # Claude always shows an in-app confirmation dialog for folder.
        link += "&folder=" + quote(_workspace(folder), safe="")
    return link


def system_opener(url):
    """Hand a claude:// URL to the OS handler. Never used by tests."""
    if not isinstance(url, str) or not url.startswith("claude://"):
        raise AdapterError("Only claude:// links are opened")
    if sys.platform == "win32":
        os.startfile(url)  # noqa: S606 - OS URL handler
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", url], check=True, timeout=30)


class ClaudeAppRoute:
    provider = PROVIDER

    def __init__(self, policy, *, mode="prefill", surface=DEFAULT_SURFACE, folder=None, opener=None):
        if mode != "prefill":
            raise AdapterError("claude_app is prefill-only: Claude Desktop links never auto-submit; "
                               "auto_send refused, no fallback to the claude CLI or API")
        if not isinstance(policy, PermissionPolicy):
            raise AdapterError("claude_app requires the shared permission policy")
        if surface not in SURFACES:
            raise AdapterError("claude_app surface must be code or chat")
        if folder is not None and surface != "code":
            raise AdapterError("folder is only supported for the code surface")
        self.policy, self.mode, self.surface = policy, mode, surface
        self.folder = _workspace(folder)
        self.opener = opener or system_opener

    def preview(self, prompt):
        return prefill_link(prompt, self.surface, self.folder)

    def deliver(self, prompt):
        link = self.preview(prompt)
        self.opener(link)
        return {"status": PREFILLED, "provider": self.provider, "mode": "prefill", "surface": self.surface,
                "link": link, "delivery": "prefilled, awaiting user send", "executed": False, "returned": False,
                "folder_confirmation": "Claude shows an in-app dialog" if self.folder else None}
