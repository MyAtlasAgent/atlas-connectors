"""Claude route: auto-send through the Claude Code CLI (default) or prefill opt-in.

``auto_send`` (default) runs the official ``claude`` CLI on the user's Claude
subscription login in a new session with a generated UUID and returns the
reply plus ``claude --resume <id>``. The Claude desktop app has no documented
deep link that opens a CLI session, so nothing is opened and the result never
claims the conversation appears in the app.

``prefill`` (explicit opt-in) opens ``claude://code/new?q=`` or
``claude://claude.ai/new?q=``; Claude Desktop 1.1.2321+ never auto-submits, so
the user presses Send. No keystroke/UI automation, no API billing fallback.
Claude Code requires a paid Claude plan via ``claude auth login``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
import uuid

from .adapters import TESTED, AdapterError, client_environment
from .codex_app import PREFILLED, UUID, _prompt, _workspace, system_runner
from .permissions import PermissionPolicy

PROVIDER = "claude_app"
MODES = ("auto_send", "prefill")
DEFAULT_MODE = "auto_send"
SURFACES = {"code": "claude://code/new", "chat": "claude://claude.ai/new"}
DEFAULT_SURFACE = "code"
MAX_PROMPT_CHARS = 14_000  # Claude truncates q around here; refuse instead
MAX_SEND_BYTES = 200_000
# Route-specific qualification: 2.1.229 is installed on the user's PC.
ACCEPTED_VERSIONS = frozenset(TESTED["claude"]) | {"2.1.229"}
REFUSED_MODES = ("bypassPermissions", "dontAsk", "auto")


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


def permission_mode_for(policy: PermissionPolicy):
    """Shared policy -> Claude permission mode; refuse rather than broaden."""
    if policy.preference("read_files") == "deny":
        raise AdapterError("Shared policy denies read_files; Claude cannot run without reading the workspace")
    mode = "acceptEdits" if policy.preference("write_files") == "allow" else "plan"
    assert mode not in REFUSED_MODES
    return mode


class ClaudeAppRoute:
    provider = PROVIDER

    def __init__(self, policy, *, mode=DEFAULT_MODE, surface=DEFAULT_SURFACE, folder=None, opener=None,
                 executable="claude", timeout=300, runner=None, which=None, session_factory=None):
        if mode not in MODES:
            raise AdapterError("claude_app mode must be auto_send or prefill; no other fallback")
        if not isinstance(policy, PermissionPolicy):
            raise AdapterError("claude_app requires the shared permission policy")
        if surface not in SURFACES:
            raise AdapterError("claude_app surface must be code or chat")
        if folder is not None and surface != "code":
            raise AdapterError("folder is only supported for the code surface")
        self.policy, self.mode, self.surface = policy, mode, surface
        self.folder = _workspace(folder)
        self.opener = opener or system_opener
        self.executable, self.timeout = executable, timeout
        self.runner, self.which = runner or system_runner, which or shutil.which
        self.session_factory = session_factory or (lambda: str(uuid.uuid4()))

    def preview(self, prompt):
        return prefill_link(prompt, self.surface, self.folder)

    def _prefill(self, prompt):
        link = self.preview(prompt)
        self.opener(link)
        return {"status": PREFILLED, "provider": self.provider, "mode": "prefill", "surface": self.surface,
                "link": link, "delivery": "prefilled, awaiting user send", "executed": False, "returned": False,
                "folder_confirmation": "Claude shows an in-app dialog" if self.folder else None}

    def _auto_send(self, prompt):
        prompt = _prompt(prompt)
        if len(prompt.encode()) > MAX_SEND_BYTES:
            raise AdapterError("Prompt exceeds the supported limit")
        permission_mode = permission_mode_for(self.policy)
        found = self.which(self.executable)
        if not found:
            raise AdapterError("Install the official Claude Code CLI for auto_send; no API fallback")
        executable = str(Path(found).resolve())
        # Refuses ANTHROPIC_API_KEY/AUTH_TOKEN/BASE_URL/Bedrock/Vertex before anything runs.
        env = client_environment("claude", Path.home(), None, default_claude_login=True)
        for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "CLAUDE_CONFIG_DIR"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        with tempfile.TemporaryDirectory(prefix="connector-claude-app-") as directory:
            cwd = self.folder or directory
            code, out, _ = self.runner([executable, "--version"], cwd=cwd, env=env, timeout=15)
            version = re.search(r"\b(\d+\.\d+\.\d+)\b", out or "")
            if code or not version or version[1] not in ACCEPTED_VERSIONS:
                raise AdapterError("Claude Code version is not qualified; nothing sent")
            code, status, _ = self.runner([executable, "auth", "status"], cwd=cwd, env=env, timeout=15)
            try:
                status = json.loads(status)
            except (ValueError, TypeError):
                status = {}
            if (code or not isinstance(status, dict) or status.get("loggedIn") is not True
                    or status.get("authMethod") != "claude.ai"):
                raise AdapterError("Claude subscription login required (claude auth login, paid plan); "
                                   "API key/Console/unknown authentication refused; nothing sent")
            session = self.session_factory()
            if not re.fullmatch(UUID, session):
                raise AdapterError("Invalid generated session id")
            argv = [executable, "-p", "--session-id", session, "--permission-mode", permission_mode,
                    "--output-format", "json"]
            # Prompt goes on stdin (as the repo's Claude adapter does), so it can never be read as a flag.
            code, out, _ = self.runner(argv, cwd=cwd, env=env, stdin=prompt, timeout=self.timeout)
        if code:
            raise AdapterError(f"claude_app auto_send: Claude CLI exit {code}; nothing opened, no fallback")
        try:
            envelope = json.loads(out)
        except ValueError:
            envelope = None
        if not isinstance(envelope, dict) or envelope.get("is_error"):
            raise AdapterError("claude_app auto_send: invalid or error response; no fallback")
        reported = envelope.get("session_id")
        if isinstance(reported, str) and re.fullmatch(UUID, reported):
            session_source = "envelope" if reported == session else "envelope_differs_from_requested"
            session = reported
        else:
            session_source = "requested_only"
        return {"status": "sent", "provider": self.provider, "mode": "auto_send",
                "permission_mode": permission_mode, "client_version": version[1], "executed": True,
                "reply_text": envelope.get("result") or "", "text_provenance": "model_claim",
                "session_id": session, "session_id_source": session_source,
                "resume_command": f"claude --resume {session}", "app_thread_opened": False,
                "delivery": "sent via Claude Code CLI; the Claude desktop app has no documented deep link "
                            "to open this session, so it is not claimed to appear there"}

    def deliver(self, prompt):
        return self._auto_send(prompt) if self.mode == "auto_send" else self._prefill(prompt)
