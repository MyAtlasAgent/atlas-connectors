"""Subscription CLI clients used only for reasoning; effects go to ToolBroker.

No Atlas imports, daemons, registry or account configuration are needed. Vendor
clients and their authentication are trusted dependencies, not adversaries.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .process import ProcessError, execute
from .protocol import SCHEMA

BINARIES = {"claude": "claude", "codex": "codex", "supergrok_cli": "grok"}
TESTED = {"claude": {"2.1.246"}, "codex": {"0.153.4"}, "supergrok_cli": {"1.0.13"}}
AUTH_ENVS = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "supergrok_cli": "GROK_HOME"}
AUTH_DIRS = {"claude": ".claude", "codex": ".codex", "supergrok_cli": ".grok"}
INHERIT = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TZ",
           "SYSTEMROOT", "SSL_CERT_FILE", "SSL_CERT_DIR")
API_OVERRIDES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "XAI_API_KEY",
                 "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                 "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


class AdapterError(RuntimeError):
    pass


def failure_category(stdout, stderr):
    lowered = (stdout + "\n" + stderr).lower()
    for category, markers in (
        ("CAPACITY_WAIT", ("usage limit", "rate limit", "quota", "overloaded", "resets at")),
        ("AUTH_REQUIRED", ("not logged", "invalid api key", "authentication", "please run /login")),
        ("INVALID_SCHEMA", ("invalid schema", "invalid_json_schema", "output schema")),
        ("CLIENT_OPTIONS_UNSUPPORTED", ("unknown option", "unexpected argument", "unrecognized")),
    ):
        if any(marker in lowered for marker in markers):
            return category
    return "PROVIDER_FAILURE"


def client_environment(provider, home, auth_root, *, default_claude_login=False):
    # Never fall back from subscription login to API or marketplace billing.
    if any(os.environ.get(key) for key in API_OVERRIDES):
        raise AdapterError("API/billing override present; subscription-only adapter refuses it")
    env = {key: os.environ[key] for key in INHERIT if key in os.environ}
    # Claude's default macOS Keychain namespace changes when CONFIG_DIR is set,
    # even to its usual directory. Preserve that supported auth path; safe-mode,
    # empty setting sources and strict empty MCP isolate its customization.
    if provider != "claude" or not default_claude_login:
        env["HOME"] = str(home)
        env[AUTH_ENVS[provider]] = str(auth_root)
    env["NO_COLOR"] = "1"
    return env


class CLIAdapter:
    def __init__(self, provider, *, executable=None, model=None, auth_directory=None, timeout=120):
        if provider not in BINARIES:
            raise AdapterError("Automatic Grok Bot desktop execution is unsupported; use a CLI provider")
        if (executable is not None and (not isinstance(executable, str) or not executable.strip())
                or model is not None and (not isinstance(model, str) or not model.strip())
                or auth_directory is not None and (not isinstance(auth_directory, str) or not auth_directory.strip())
                or type(timeout) not in (int, float) or not 0 < timeout <= 300):
            raise AdapterError("Invalid executable, model, login directory or timeout")
        found = shutil.which(executable or BINARIES[provider])
        if not found:
            raise AdapterError(f"Install the official {BINARIES[provider]} client first")
        self.provider, self.executable, self.model = provider, str(Path(found).resolve()), model
        self.default_claude_login = provider == "claude" and auth_directory is None and not os.environ.get("CLAUDE_CONFIG_DIR")
        self.auth_directory = Path(auth_directory or os.environ.get(AUTH_ENVS[provider])
                                   or Path.home() / AUTH_DIRS[provider]).expanduser().resolve()
        self.timeout = timeout
        self.version = None
        self.observations = []

    def doctor(self):
        env = {key: os.environ[key] for key in INHERIT if key in os.environ}
        with tempfile.TemporaryDirectory(prefix="connector-doctor-") as directory:
            code, out, _ = execute([self.executable, "--version"], cwd=directory, env=env, timeout=15)
        match = re.search(r"\b(\d+\.\d+\.\d+)\b", out)
        if code or not match or match[1] not in TESTED[self.provider]:
            raise AdapterError("Client version is not qualified; see supported versions in README")
        self.version = match[1]
        return {"provider": self.provider, "version": self.version,
                "authentication": "not checked", "native_tools": "disabled by connector launch settings"}

    def complete(self, prompt):
        if len(prompt.encode()) > 200_000:
            raise AdapterError("Conversation exceeds the supported prompt limit")
        if self.version is None:
            self.doctor()
        with tempfile.TemporaryDirectory(prefix="connector-client-") as directory:
            root = Path(directory)
            auth = self.auth_directory
            if self.provider == "supergrok_cli":
                # Reuse only the provider-managed login, never its plugins/MCP/hooks.
                isolated = root / "grok"
                isolated.mkdir(mode=0o700)
                login = auth / "auth.json"
                if not login.is_file():
                    raise AdapterError("Grok login unavailable: run grok login first")
                try:
                    (isolated / "auth.json").symlink_to(login)
                except OSError:
                    # Windows without Developer Mode cannot symlink; a hard link keeps one shared file.
                    try:
                        os.link(login, isolated / "auth.json")
                    except OSError:
                        raise AdapterError("Could not link the Grok login into the isolated client directory "
                                           "(symlink and hard link both refused); nothing sent") from None
                (isolated / "config.toml").write_text('[cli]\nauto_update = false\nuse_leader = false\n')
                auth = isolated
            env = client_environment(self.provider, root, auth, default_claude_login=self.default_claude_login)
            if self.provider == "claude":
                code, status, _ = execute([self.executable, "auth", "status"], cwd=root, env=env, timeout=15)
                try:
                    method = json.loads(status).get("authMethod")
                except (ValueError, AttributeError):
                    method = None
                if code or method != "claude.ai":
                    raise AdapterError("Claude subscription login required; API/helper/unknown authentication refused")
            elif self.provider == "codex":
                code, status, status_err = execute([self.executable, "login", "status"], cwd=root, env=env, timeout=15)
                if code or "chatgpt" not in (status + status_err).lower():
                    raise AdapterError("Codex ChatGPT login required; API/unknown authentication refused")
            schema = root / "response-schema.json"
            schema.write_text(json.dumps(SCHEMA))
            if self.provider == "claude":
                argv = [self.executable, "-p", "--safe-mode", "--tools", "",
                        "--setting-sources", "",
                        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                        "--no-session-persistence", "--output-format", "json",
                        "--json-schema", json.dumps(SCHEMA)]
                stdin = prompt
            elif self.provider == "codex":
                output = root / "response.json"
                argv = [self.executable, "exec", "--ignore-user-config", "--ignore-rules",
                        "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                        "--output-schema", str(schema), "-o", str(output), "--json"]
                overrides = {"approval_policy": "never", "features.shell_tool": False,
                             "features.hooks": False, "features.multi_agent": False,
                             "agents.enabled": False, "features.memories": False,
                             "features.skill_mcp_dependency_install": False,
                             "tools.view_image": False, "web_search": "disabled",
                             "apps._default.enabled": False, "mcp_servers": {},
                             "plugins": {}, "project_doc_max_bytes": 0}
                for key, value in overrides.items():
                    # TOML inline empty table is {}, other values share JSON syntax.
                    argv.extend(["-c", key + "=" + json.dumps(value)])
                stdin = prompt
            else:
                code, inspected, _ = execute([self.executable, "inspect"], cwd=root, env=env, timeout=15)
                if code or any(not re.search(label + r"\s*\(0\)", inspected)
                               for label in ("MCP Servers", "Plugins", "Hooks")):
                    raise AdapterError("Grok inherited customizations; isolated transport refused")
                prompt_file = root / "prompt.txt"
                prompt_file.write_text(prompt)
                prompt_file.chmod(0o600)
                argv = [self.executable, "--prompt-file", str(prompt_file), "--tools", "", "--deny", "*",
                        "--permission-mode", "dontAsk", "--no-subagents", "--disable-web-search",
                        "--session-id", str(uuid.uuid4()), "--max-turns", "1",
                        "--json-schema", json.dumps(SCHEMA), "--output-format", "json"]
                stdin = ""
            if self.model:
                argv.extend(["--model", self.model])
            try:
                code, stdout, stderr = execute(argv, cwd=root, env=env, stdin=stdin, timeout=self.timeout)
            except ProcessError as error:
                raise AdapterError(str(error)) from None
            # Never put raw client errors, prompts or authentication material in errors/logs.
            if code:
                category = failure_category(stdout, stderr)
                raise AdapterError(f"{self.provider}: {category}, exit {code}; no retry")
            if self.provider == "codex":
                if not output.is_file():
                    raise AdapterError("Codex returned no structured response")
                text = output.read_text()
            else:
                try:
                    envelope = json.loads(stdout)
                    if envelope.get("is_error"):
                        raise AdapterError("Provider reported an error; no retry")
                    structured = envelope.get("structured_output")
                    if isinstance(structured, dict):
                        text = json.dumps(structured)
                    else:
                        text = envelope.get("result") or envelope.get("text") or ""
                except (ValueError, AttributeError):
                    raise AdapterError("Provider returned an invalid response envelope") from None
            if not isinstance(text, str) or not text.strip():
                raise AdapterError("Provider returned no structured connector message")
            self.observations.append({"provider": self.provider, "client_version": self.version,
                                      "process_exit": code, "fresh_session": True})
            return text
