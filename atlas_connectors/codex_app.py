"""Default new-user route: the Codex desktop app on the user's ChatGPT login.

* ``auto_send`` (default): spawn ``codex app-server`` (stdio JSON-RPC, one
  message per line, normal CODEX_HOME), start a thread in the user's real
  workspace, start a turn with the prompt, collect the reply until
  ``turn/completed``, terminate the server, then open ``codex://threads/<id>``.
  Verified live on Windows: thread 01a169f1-5263-7580-8f28-cd814fbdb775 was
  visible in the Codex app. The prompt travels inside JSON, never argv.
* ``prefill`` (explicit opt-in only): ``codex://threads/new?prompt=...``; the
  app never auto-submits, so the user presses Send.

Spawner, runner and opener are injectable so tests launch nothing. Any failure
runs once, opens nothing and never falls back to ``codex exec``, another
provider or API billing. No keystroke/UI automation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote

from .adapters import TESTED, AdapterError, client_environment
from .permissions import PermissionPolicy
from .process import execute

DEFAULT_PROVIDER = "codex_app"
MODES = ("auto_send", "prefill")
DEFAULT_MODE = "auto_send"
MAX_LINK_BYTES = 8000  # conservative OS URL-handler limit
MAX_PROMPT_BYTES = 200_000
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
PREFILLED = "prefilled_awaiting_user_send"
# Route-specific qualification: 0.148.0 verified live (ChatGPT login, app-server).
ACCEPTED_VERSIONS = frozenset(TESTED["codex"]) | {"0.148.0"}
CLIENT_INFO = {"name": "codex_vscode", "title": "Atlas Connectors", "version": "0.1"}
WINDOWS_ENV = ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMDATA", "SystemDrive", "COMSPEC", "PATHEXT")


def _workspace(path):
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        raise AdapterError("Workspace/path must be an absolute directory")
    if not candidate.is_dir():
        raise AdapterError("Workspace/path must be an existing directory")
    return str(candidate.resolve())


def temporary_root():
    return Path(tempfile.gettempdir()).resolve()


def require_workspace(path):
    """Resolve the auto_send workspace: explicit/config value, else the current directory.

    It must be an absolute existing directory outside the system temp dir.
    """
    how = " Set one with --workspace DIR or `atlas-connectors init --workspace DIR`, or run from your project directory."
    chosen = path if path is not None else os.getcwd()
    try:
        resolved = Path(_workspace(chosen))
    except AdapterError as error:
        raise AdapterError(f"auto_send workspace {chosen!r} is invalid: {error}.{how}") from None
    if resolved == temporary_root() or resolved.is_relative_to(temporary_root()):
        raise AdapterError(f"auto_send refuses a workspace inside the system temp directory ({resolved}).{how}")
    return str(resolved)


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
        raise AdapterError("Invalid Codex thread id")
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
    """Bounded child process; output always decoded as UTF-8 (also on Windows)."""
    if os.name == "posix":
        return execute(argv, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
    done = subprocess.run(argv, cwd=cwd, env=env, input=stdin, capture_output=True, timeout=timeout,
                          text=True, encoding="utf-8", errors="replace")
    return done.returncode, done.stdout, done.stderr


def system_spawner(argv, *, cwd, env):
    """Start a long-lived stdio child with binary pipes (decoded by the caller)."""
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, **flags)


def client_env(home_dir):
    env = client_environment("codex", Path.home(), home_dir)
    for key in WINDOWS_ENV:
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def sandbox_for(policy: PermissionPolicy):
    """Shared policy -> app-server sandbox; refuse rather than broaden."""
    if policy.preference("read_files") == "deny":
        raise AdapterError("Shared policy denies read_files; Codex cannot run without reading the workspace")
    return "workspace-write" if policy.preference("write_files") == "allow" else "read-only"


class AppServerSession:
    """Line-delimited JSON-RPC over the child's stdio, UTF-8 decoded explicitly."""

    def __init__(self, process):
        self.process, self.lines = process, queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            for raw in iter(self.process.stdout.readline, b""):
                self.lines.put(raw.decode("utf-8", errors="replace"))
        finally:
            self.lines.put(None)

    def send(self, message):
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def next(self, deadline):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError from None
            if line is None:
                raise EOFError
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                if "id" in message and "method" in message:
                    # Server-initiated request (e.g. approval): refuse; never auto-approve.
                    self.send({"id": message["id"], "error": {"code": -32601, "message": "Refused by connector"}})
                    continue
                return message

    def call(self, ident, method, params, deadline, collect):
        self.send({"id": ident, "method": method, "params": params})
        while True:
            message = self.next(deadline)
            if message.get("id") == ident:
                if "error" in message:
                    raise AdapterError(f"codex app-server {method} failed; nothing opened, no fallback")
                return message.get("result") or {}
            collect(message)

    def close(self):
        try:
            self.process.stdin.close()
        except Exception:
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass


class CodexAppRoute:
    provider = DEFAULT_PROVIDER

    def __init__(self, policy, *, mode=DEFAULT_MODE, executable="codex", workspace=None,
                 timeout=300, runner=None, opener=None, spawner=None, codex_home=None, which=None):
        if mode not in MODES:
            raise AdapterError("codex_app mode must be auto_send or prefill; no other fallback")
        if not isinstance(policy, PermissionPolicy):
            raise AdapterError("codex_app requires the shared permission policy")
        self.policy, self.mode, self.timeout = policy, mode, timeout
        self.executable, self.workspace = executable, _workspace(workspace)
        self.runner, self.opener = runner or system_runner, opener or system_opener
        self.spawner, self.which = spawner or system_spawner, which or shutil.which
        self.codex_home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")

    def preview(self, prompt):
        return prefill_link(prompt, self.workspace)

    def _prefill(self, prompt):
        link = prefill_link(prompt, self.workspace)
        self.opener(link)
        return {"status": PREFILLED, "provider": self.provider, "mode": "prefill", "link": link,
                "delivery": "prefilled, awaiting user send", "executed": False, "returned": False}

    def _preflight(self, env, cwd):
        found = self.which(self.executable)
        if not found:
            raise AdapterError("Install the official Codex CLI for auto_send; no API fallback")
        executable = str(Path(found).resolve())
        code, out, _ = self.runner([executable, "--version"], cwd=cwd, env=env, timeout=15)
        version = re.search(r"\b(\d+\.\d+\.\d+)\b", out or "")
        if code or not version or version[1] not in ACCEPTED_VERSIONS:
            raise AdapterError("Codex client version is not qualified; nothing sent")
        code, out, err = self.runner([executable, "login", "status"], cwd=cwd, env=env, timeout=15)
        if code or "chatgpt" not in (out + err).lower():
            raise AdapterError("Codex ChatGPT login required; API/unknown authentication refused; nothing sent")
        return executable, version[1]

    def _auto_send(self, prompt, thread_id=None, open_app=True):
        prompt = _prompt(prompt)
        if len(prompt.encode()) > MAX_PROMPT_BYTES:
            raise AdapterError("Prompt exceeds the supported limit")
        workspace = require_workspace(self.workspace)
        sandbox = sandbox_for(self.policy)
        env = client_env(self.codex_home)  # refuses API key/base URL overrides
        executable, version = self._preflight(env, workspace)
        deadline = time.monotonic() + self.timeout
        try:
            process = self.spawner([executable, "app-server"], cwd=workspace, env=env)
        except OSError:
            raise AdapterError("codex app-server could not start; nothing sent, no fallback") from None
        session = AppServerSession(process)
        deltas, finals, turn = [], [], {}

        def collect(message):
            method, params = message.get("method"), message.get("params") or {}
            if method == "item/agentMessage/delta" and isinstance(params.get("delta"), str):
                deltas.append(params["delta"])
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                    finals.append(item["text"])
            elif method == "turn/completed":
                turn["completed"] = params.get("turn") or {}

        continuing = thread_id is not None
        if continuing:
            thread_link(thread_id)  # validates the id before anything runs
        try:
            try:
                session.call(0, "initialize", {"clientInfo": CLIENT_INFO}, deadline, collect)
                session.send({"method": "initialized", "params": {}})
                if continuing:
                    session.call(1, "thread/resume", {"threadId": thread_id, "cwd": workspace, "sandbox": sandbox,
                                                      "approvalPolicy": "never"}, deadline, collect)
                else:
                    started = session.call(1, "thread/start", {"cwd": workspace, "sandbox": sandbox,
                                                               "approvalPolicy": "never"}, deadline, collect)
                    thread_id = (started.get("thread") or {}).get("id") or started.get("threadId")
                if not isinstance(thread_id, str) or not re.fullmatch(UUID, thread_id):
                    raise AdapterError("codex app-server returned no thread id; nothing opened, no fallback")
                session.call(2, "turn/start", {"threadId": thread_id,
                                               "input": [{"type": "text", "text": prompt}]}, deadline, collect)
            except (TimeoutError, EOFError, OSError):
                raise AdapterError("codex app-server did not accept the message; nothing opened, no fallback") from None
            # The turn is accepted: the message is in the thread. Wait for completion.
            try:
                while "completed" not in turn:
                    collect(session.next(deadline))
                completion = "completed"
            except TimeoutError:
                completion = "timeout"
            except EOFError:
                completion = "server_exited"
        finally:
            session.close()
        turn_status = turn.get("completed", {}).get("status") if completion == "completed" else None
        if completion == "completed" and turn_status == "failed":
            raise AdapterError("Codex turn failed; nothing opened, no fallback")
        link = thread_link(thread_id)
        if open_app:
            self.opener(link)
        status = "sent" if completion == "completed" else "sent_completion_unknown"
        return {"status": status, "provider": self.provider, "mode": "auto_send", "sandbox": sandbox,
                "client_version": version, "executed": True, "thread_id": thread_id, "link": link,
                "app_thread_opened": bool(open_app), "continued": continuing, "completion": completion, "turn_status": turn_status,
                "reply_text": finals[-1] if finals else "".join(deltas), "text_provenance": "model_claim",
                "delivery": "sent via codex app-server; app asked to open the thread" + (
                    "" if completion == "completed" else "; reply completion not observed")}

    def deliver(self, prompt, thread_id=None, open_app=True):
        if thread_id is not None or not open_app:
            if self.mode != "auto_send":
                raise AdapterError("Continuing a thread requires auto_send mode")
            return self._auto_send(prompt, thread_id, open_app)
        return self._auto_send(prompt) if self.mode == "auto_send" else self._prefill(prompt)
