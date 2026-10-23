"""Default new-user route: the Codex desktop app on the user's ChatGPT login.

Two delivery modes, both explicit and honest about what was observed:

* ``auto_send`` (default): the official Codex CLI sends the prompt on the
  user's ChatGPT subscription in a fresh session, then the app is pointed at
  that session with ``codex://threads/<uuid>``. No keystrokes or UI automation.
* ``prefill`` (explicit opt-in only): ``codex://threads/new?prompt=...`` opens a new desktop thread
  with the composer prefilled. The app never auto-submits; the user presses Send.

Runner and opener are injectable so tests launch nothing. A failure never falls
back to another mode, provider, CLI or API billing.
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
import time
from urllib.parse import quote

from .adapters import TESTED, AdapterError, client_environment
from .permissions import PermissionPolicy
from .process import execute

DEFAULT_PROVIDER = "codex_app"
MODES = ("auto_send", "prefill")
DEFAULT_MODE = "auto_send"
MAX_LINK_BYTES = 8000  # conservative OS URL-handler limit (Windows ShellExecute ~ 2K-32K)
MAX_PROMPT_BYTES = 200_000
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
PREFILLED = "prefilled_awaiting_user_send"
# Route-specific qualification: 0.148.0 verified live (ChatGPT login, exec -s read-only).
ACCEPTED_VERSIONS = frozenset(TESTED["codex"]) | {"0.148.0"}


def _workspace(path):
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        raise AdapterError("codex_app path must be an absolute directory")
    if not candidate.is_dir():
        raise AdapterError("codex_app path must be an existing directory")
    return str(candidate.resolve())


def _prompt(prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        raise AdapterError("Prompt must be nonempty text")
    if "\x00" in prompt:
        raise AdapterError("Prompt contains a NUL byte")
    return prompt


def prefill_link(prompt, path=None):
    """Build codex://threads/new?prompt=...; every reserved character is encoded."""
    link = "codex://threads/new?prompt=" + quote(_prompt(prompt), safe="")
    workspace = _workspace(path)
    if workspace:
        link += "&path=" + quote(workspace, safe="")
    if len(link.encode()) > MAX_LINK_BYTES:
        raise AdapterError(f"Prompt too long for a codex:// link ({len(link.encode())} > {MAX_LINK_BYTES} bytes); "
                           "shorten it or use auto_send mode")
    return link


def thread_link(session_id):
    if not isinstance(session_id, str) or not re.fullmatch(UUID, session_id):
        raise AdapterError("Invalid Codex session id")
    return "codex://threads/" + session_id


def system_opener(url):
    """Hand a codex:// URL to the OS handler. Never used by tests."""
    if not url.startswith("codex://"):
        raise AdapterError("Only codex:// links are opened")
    if sys.platform == "win32":
        os.startfile(url)  # noqa: S606 - OS URL handler
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", url], check=True, timeout=30)


def system_runner(argv, *, cwd, env, stdin="", timeout=120):
    if os.name == "posix":
        return execute(argv, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
    done = subprocess.run(argv, cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=timeout)
    return done.returncode, done.stdout, done.stderr


def sandbox_for(policy: PermissionPolicy):
    """Map the shared policy to Codex native controls; refuse rather than broaden.

    Codex exec cannot ask interactively, so "ask" maps to the narrower setting.
    """
    if policy.preference("read_files") == "deny":
        raise AdapterError("Shared policy denies read_files; Codex cannot run without reading the workspace")
    sandbox = "workspace-write" if policy.preference("write_files") == "allow" else "read-only"
    overrides = {"approval_policy": "never",
                 "features.shell_tool": policy.preference("run_commands") == "allow",
                 "web_search": "live" if policy.preference("network") == "allow" else "disabled",
                 "mcp_servers": {}, "plugins": {}}
    if policy.preference("connected_tools") != "allow":
        overrides["apps._default.enabled"] = False
    return sandbox, overrides


class CodexAppRoute:
    provider = DEFAULT_PROVIDER

    def __init__(self, policy, *, mode=DEFAULT_MODE, executable="codex", workspace=None,
                 timeout=300, runner=None, opener=None, sessions_dir=None, which=None):
        if mode not in MODES:
            raise AdapterError("codex_app mode must be auto_send or prefill; no other fallback")
        if not isinstance(policy, PermissionPolicy):
            raise AdapterError("codex_app requires the shared permission policy")
        self.policy, self.mode, self.timeout = policy, mode, timeout
        self.executable, self.workspace = executable, _workspace(workspace)
        self.runner, self.opener = runner or system_runner, opener or system_opener
        self.which = which or shutil.which
        self.sessions_dir = Path(sessions_dir) if sessions_dir else (
            Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions")

    # -- prefill -----------------------------------------------------------
    def preview(self, prompt):
        return prefill_link(prompt, self.workspace)

    def _prefill(self, prompt):
        link = prefill_link(prompt, self.workspace)
        self.opener(link)
        return {"status": PREFILLED, "provider": self.provider, "mode": "prefill", "link": link,
                "delivery": "prefilled, awaiting user send", "executed": False, "returned": False}

    # -- auto send ---------------------------------------------------------
    def _session_id(self, stdout, stderr, started):
        text = stdout + "\n" + stderr
        match = (re.search(r"session id:\s*(" + UUID + ")", text, re.I)
                 or re.search(r'"(?:thread_id|session_id)"\s*:\s*"(' + UUID + ')"', text))
        if match:
            return match[1], "cli_output"
        if self.sessions_dir.is_dir():
            found = [p for p in self.sessions_dir.rglob("rollout-*.jsonl") if p.stat().st_mtime >= started - 1]
            ids = {m[1] for p in found if (m := re.search("(" + UUID + r")\.jsonl$", p.name))}
            if len(ids) == 1:
                return ids.pop(), "rollout_file"
        return None, None

    def _auto_send(self, prompt):
        prompt = _prompt(prompt)
        if len(prompt.encode()) > MAX_PROMPT_BYTES:
            raise AdapterError("Prompt exceeds the supported limit")
        if prompt.startswith("-"):
            raise AdapterError("Prompt may not start with '-' (would be read as a CLI option)")
        sandbox, overrides = sandbox_for(self.policy)
        found = self.which(self.executable)
        if not found:
            raise AdapterError("Install the official Codex CLI for auto_send; no API fallback")
        executable = str(Path(found).resolve())
        with tempfile.TemporaryDirectory(prefix="connector-codex-app-") as directory:
            root = Path(directory)
            env = client_environment("codex", Path.home(), self.sessions_dir.parent)
            for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
                if key in os.environ:
                    env[key] = os.environ[key]
            code, out, _ = self.runner([executable, "--version"], cwd=root, env=env, timeout=15)
            version = re.search(r"\b(\d+\.\d+\.\d+)\b", out or "")
            if code or not version or version[1] not in ACCEPTED_VERSIONS:
                raise AdapterError("Codex client version is not qualified; nothing sent")
            code, out, err = self.runner([executable, "login", "status"], cwd=root, env=env, timeout=15)
            if code or "chatgpt" not in (out + err).lower():
                raise AdapterError("Codex ChatGPT login required; API/unknown authentication refused; nothing sent")
            output = root / "return.md"
            argv = [executable, "exec", "--skip-git-repo-check", "-s", sandbox, "-o", str(output)]
            for key, value in overrides.items():
                argv += ["-c", key + "=" + json.dumps(value)]
            if self.workspace:
                argv += ["-C", self.workspace]
            argv.append(prompt)
            started = time.time()
            code, out, err = self.runner(argv, cwd=self.workspace or root, env=env, timeout=self.timeout)
            if code:
                # Do not open the app, retry, or switch to prefill/API on failure.
                raise AdapterError(f"codex_app auto_send: Codex CLI exit {code}; nothing opened, no fallback")
            reply = output.read_text() if output.is_file() else ""
        session, source = self._session_id(out, err, started)
        result = {"provider": self.provider, "mode": "auto_send", "sandbox": sandbox,
                  "client_version": version[1], "executed": True, "reply_text": reply,
                  "text_provenance": "model_claim", "session_id": session, "session_id_source": source}
        if session is None:
            result.update(status="sent_session_unknown", app_thread_opened=False, link=None,
                          delivery="sent via Codex CLI; session id not found, app thread not opened")
            return result
        link = thread_link(session)
        self.opener(link)
        result.update(status="sent", app_thread_opened=True, link=link,
                      delivery="sent via Codex CLI; app asked to open thread (visibility in app not verified)")
        return result

    def deliver(self, prompt):
        return self._auto_send(prompt) if self.mode == "auto_send" else self._prefill(prompt)
