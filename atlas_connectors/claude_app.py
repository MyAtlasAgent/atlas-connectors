"""Claude route: auto-send in the Claude desktop app's Code tab (default) or prefill opt-in.

``auto_send`` (default), verified live on Windows (session
991819ea-393c-4b51-9c10-2fddf255b61d visible in the Claude app Code tab):
run ``claude -p --permission-mode <plan|acceptEdits> --output-format json`` in
the user's real workspace with the prompt on stdin (UTF-8), parse ``result``
and ``session_id``, then hand off with ``claude --desktop --resume <id>``
launched detached with its own console and no redirected stdio (``--desktop``
refuses to run non-interactively). Requires Claude Code >= 2.1.285 and a
``claude auth login`` subscription login (paid plan), separate from the
desktop app login. API key/Console/Bedrock/Vertex are refused.

``prefill`` (explicit opt-in) opens ``claude://code/new?q=`` or
``claude://claude.ai/new?q=``; the user presses Send. No keystroke/UI automation.
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

from .adapters import AdapterError, client_environment
from .codex_app import PREFILLED, UUID, WINDOWS_ENV, _prompt, _workspace, require_workspace, system_runner
from .permissions import PermissionPolicy

PROVIDER = "claude_app"
MODES = ("auto_send", "prefill")
DEFAULT_MODE = "auto_send"
SURFACES = {"code": "claude://code/new", "chat": "claude://claude.ai/new"}
DEFAULT_SURFACE = "code"
MAX_PROMPT_CHARS = 14_000  # Claude truncates q around here; refuse instead
MAX_SEND_BYTES = 200_000
MIN_VERSION = (2, 1, 285)  # first release whose --desktop handoff was verified
HANDOFF_TIMEOUT = 30
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


def system_launcher(argv, *, cwd, env, timeout=HANDOFF_TIMEOUT):
    """Detached launch with its own console and inherited (not piped) stdio.

    Returns the exit code, or None on timeout (the child is then killed).
    """
    if sys.platform == "win32":
        flags = {"creationflags": subprocess.CREATE_NEW_CONSOLE}
    else:
        flags = {"start_new_session": True}
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=None, stdout=None, stderr=None, **flags)
    try:
        return child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        return None


class ClaudeAppRoute:
    provider = PROVIDER

    def __init__(self, policy, *, mode=DEFAULT_MODE, surface=DEFAULT_SURFACE, folder=None, opener=None,
                 executable="claude", timeout=300, runner=None, which=None, launcher=None, workspace=None):
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
        self.launcher = launcher or system_launcher
        self.workspace = _workspace(workspace) or self.folder

    def preview(self, prompt):
        return prefill_link(prompt, self.surface, self.folder)

    def _prefill(self, prompt):
        link = self.preview(prompt)
        self.opener(link)
        return {"status": PREFILLED, "provider": self.provider, "mode": "prefill", "surface": self.surface,
                "link": link, "delivery": "prefilled, awaiting user send", "executed": False, "returned": False,
                "folder_confirmation": "Claude shows an in-app dialog" if self.folder else None}

    def _auto_send(self, prompt, session_id=None, handoff=True):
        prompt = _prompt(prompt)
        if len(prompt.encode()) > MAX_SEND_BYTES:
            raise AdapterError("Prompt exceeds the supported limit")
        workspace = require_workspace(self.workspace)
        permission_mode = permission_mode_for(self.policy)
        found = self.which(self.executable)
        if not found:
            raise AdapterError("Install the official Claude Code CLI for auto_send; no API fallback")
        executable = str(Path(found).resolve())
        # Refuses ANTHROPIC_API_KEY/AUTH_TOKEN/BASE_URL/Bedrock/Vertex before anything runs.
        env = client_environment("claude", Path.home(), None, default_claude_login=True)
        for key in WINDOWS_ENV + ("CLAUDE_CONFIG_DIR",):
            if os.environ.get(key):
                env[key] = os.environ[key]
        code, out, _ = self.runner([executable, "--version"], cwd=workspace, env=env, timeout=15)
        version = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", out or "")
        if code or not version or tuple(map(int, version.groups())) < MIN_VERSION:
            raise AdapterError("Claude Code >= 2.1.285 required for desktop handoff; nothing sent")
        code, status, _ = self.runner([executable, "auth", "status"], cwd=workspace, env=env, timeout=15)
        try:
            status = json.loads(status)
        except (ValueError, TypeError):
            status = {}
        if (code or not isinstance(status, dict) or status.get("loggedIn") is not True
                or status.get("authMethod") != "claude.ai"):
            raise AdapterError("Claude subscription login required (claude auth login, paid plan); "
                               "API key/Console/unknown authentication refused; nothing sent")
        argv = [executable, "-p", "--permission-mode", permission_mode, "--output-format", "json"]
        if session_id is not None:
            if not isinstance(session_id, str) or not re.fullmatch(UUID, session_id):
                raise AdapterError("Invalid Claude session id")
            argv += ["--resume", session_id]
        # Prompt on stdin (UTF-8), so it can never be read as a flag.
        code, out, _ = self.runner(argv, cwd=workspace, env=env, stdin=prompt, timeout=self.timeout)
        if code:
            raise AdapterError(f"claude_app auto_send: Claude CLI exit {code}; nothing opened, no fallback")
        try:
            envelope = json.loads(out)
        except (ValueError, TypeError):
            envelope = None
        if not isinstance(envelope, dict) or envelope.get("is_error"):
            raise AdapterError("claude_app auto_send: invalid or error response; nothing opened, no fallback")
        session = envelope.get("session_id")
        result = {"provider": self.provider, "mode": "auto_send", "permission_mode": permission_mode,
                  "client_version": ".".join(version.groups()), "executed": True, "workspace": workspace,
                  "reply_text": envelope.get("result") or "", "text_provenance": "model_claim"}
        if not isinstance(session, str) or not re.fullmatch(UUID, session):
            result.update(status="sent_session_unknown", session_id=None, app_handoff="not_attempted",
                          delivery="sent via Claude Code CLI; no session id, desktop handoff not attempted")
            return result
        resume = f"claude --resume {session}"
        result["continued"] = session_id is not None
        if not handoff:
            result.update(session_id=session, resume_command=resume, status="sent", app_handoff="deferred",
                          handoff_command=f"claude --desktop --resume {session}",
                          delivery="sent via Claude Code CLI; desktop handoff deferred")
            return result
        try:
            handoff = self.launcher([executable, "--desktop", "--resume", session], cwd=workspace, env=env)
        except OSError:
            handoff = "launch_error"
        result.update(session_id=session, resume_command=resume,
                      handoff_command=f"claude --desktop --resume {session}")
        if handoff == 0:
            result.update(status="sent", app_handoff="ok",
                          delivery="sent via Claude Code CLI; handed off to the Claude desktop app Code tab")
        else:
            result.update(status="sent_handoff_failed",
                          app_handoff="timeout" if handoff is None else f"exit {handoff}" if isinstance(handoff, int) else handoff,
                          delivery="sent via Claude Code CLI; desktop handoff failed; use resume_command")
        return result

    def handoff(self, session_id):
        """Open an existing session in the Claude desktop app (detached)."""
        if not isinstance(session_id, str) or not re.fullmatch(UUID, session_id):
            raise AdapterError("Invalid Claude session id")
        found = self.which(self.executable)
        if not found:
            raise AdapterError("Claude Code CLI not found")
        env = client_environment("claude", Path.home(), None, default_claude_login=True)
        for key in WINDOWS_ENV + ("CLAUDE_CONFIG_DIR",):
            if os.environ.get(key):
                env[key] = os.environ[key]
        try:
            code = self.launcher([str(Path(found).resolve()), "--desktop", "--resume", session_id],
                                 cwd=require_workspace(self.workspace), env=env)
        except OSError:
            return "launch_error"
        return "ok" if code == 0 else "timeout" if code is None else f"exit {code}"

    def deliver(self, prompt, session_id=None, handoff=True):
        if session_id is not None or not handoff:
            if self.mode != "auto_send":
                raise AdapterError("Continuing a session requires auto_send mode")
            return self._auto_send(prompt, session_id, handoff)
        return self._auto_send(prompt) if self.mode == "auto_send" else self._prefill(prompt)
