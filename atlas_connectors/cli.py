"""Standalone setup, official client login, diagnostics and bounded execution."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .adapters import AUTH_DIRS, AUTH_ENVS, BINARIES, AdapterError, CLIAdapter, client_environment
from .codex_app import DEFAULT_MODE, DEFAULT_PROVIDER, MODES, CodexAppRoute
from .claude_app import ClaudeAppRoute, SURFACES
from .broker import ToolBroker
from .permissions import PermissionPolicy, load_configuration
from .protocol import INSTRUCTIONS, parse
from .runner import run


def initialize(directory, isolated=False):
    root = Path(directory).resolve()
    destinations = [root / "connectors.json", root / "permissions.json"]
    if any(path.exists() for path in destinations):
        raise ValueError("Configuration already exists; no files overwritten")
    root.mkdir(parents=True, exist_ok=True)
    # New users default to the Codex desktop app on their ChatGPT login.
    providers = {DEFAULT_PROVIDER: {"transport": "codex_app", "mode": DEFAULT_MODE, "executable": "codex"},
                 "claude_app": {"transport": "claude_app", "mode": "auto_send", "surface": "code",
                                "executable": "claude"}}
    for name, binary in BINARIES.items():
        route = {"transport": "subscription_cli", "executable": binary}
        if isolated:
            route["auth_directory"] = str(root / "logins" / name)
        providers[name] = route
    destinations[1].write_text(json.dumps({"version": 1, "default": "ask", "capabilities": {}}, indent=2) + "\n")
    destinations[0].write_text(json.dumps({"permission_policy": "permissions.json", "providers": providers}, indent=2) + "\n")
    return {"configuration": str(destinations[0]), "permissions": str(destinations[1]),
            "login_mode": "separate provider profiles" if isolated else "provider-managed existing logins"}


def adapter_for(configuration, provider):
    route = configuration.providers.get(provider)
    if route is None:
        raise AdapterError("Provider is not configured")
    if route.get("transport") != "subscription_cli":
        raise AdapterError("Selected route is not a standalone subscription CLI; no transport substitution")
    return CLIAdapter(provider, **{key: route[key] for key in ("executable", "model", "auth_directory", "timeout") if key in route})


def route_for(configuration, provider=DEFAULT_PROVIDER, *, mode=None, workspace=None, runner=None, opener=None,
              surface=None):
    """Build a desktop-app route; refuses other providers instead of substituting."""
    route = configuration.providers.get(provider)
    if route is None:
        raise AdapterError("Provider is not configured")
    if route.get("transport") == "claude_app":
        return ClaudeAppRoute(configuration.permissions_for(provider), mode=mode or route.get("mode", "auto_send"),
                              surface=surface or route.get("surface", "code"),
                              folder=workspace or route.get("folder"), opener=opener,
                              executable=route.get("executable", "claude"),
                              timeout=route.get("timeout", 300), runner=runner)
    if route.get("transport") != "codex_app":
        raise AdapterError("Selected route is not codex_app; use `run` for CLI providers; no substitution")
    return CodexAppRoute(configuration.permissions_for(provider), mode=mode or route.get("mode", DEFAULT_MODE),
                         executable=route.get("executable", "codex"),
                         workspace=workspace or route.get("workspace"),
                         timeout=route.get("timeout", 300), runner=runner, opener=opener)


def terminal_approval(capability, request):
    # Approval is scoped to this exact request and never read from the model's stdin.
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        return False
    print(f"Allow {capability} for {json.dumps(request, ensure_ascii=True)}? [y/N] ", file=sys.stderr, end="", flush=True)
    return input().strip().lower() == "y"


def smoke(adapter):
    """Fresh live connections plus actual allow/deny/ask filesystem checks."""
    observations = []
    with tempfile.TemporaryDirectory(prefix="connector-smoke-") as temporary:
        for preference in ("allow", "deny", "ask"):
            policy = PermissionPolicy.from_dict({"version": 1, "default": "deny", "capabilities": {"write_files": preference}})
            broker = ToolBroker(temporary, policy)
            filename = preference + ".txt"
            request = {"kind": "tool", "id": preference, "tool": "write_file",
                       "arguments": json.dumps({"path": filename, "text": "CONNECTION_OK"}), "text": ""}
            response = adapter.complete(INSTRUCTIONS + "\nReturn exactly this JSON request without executing anything yourself:\n" + json.dumps(request))
            parsed = parse(response)
            if not isinstance(parsed, dict) or parsed != {"id": preference, "tool": "write_file", "arguments": {"path": filename, "text": "CONNECTION_OK"}}:
                raise AdapterError("Live client did not return the requested smoke-test envelope")
            result = broker.call(parsed)
            expected = preference == "allow"
            if (Path(temporary) / filename).exists() != expected:
                raise AdapterError("Live permission smoke test failed")
            observations.append({"preference": preference, "tool_status": result["status"], "file_created": expected})
    return {"provider": adapter.provider, "checks": observations, "transport": adapter.observations,
            "scope": "fresh subscription conversations; existing login; no Atlas dependency"}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="atlas-connectors")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create standalone configuration; no provider calls")
    init.add_argument("directory", nargs="?", default="connector-config")
    init.add_argument("--isolated-logins", action="store_true")
    send = commands.add_parser("send", help="default route: deliver a prompt to the Codex desktop app")
    send.add_argument("prompt")
    send.add_argument("--provider", default=DEFAULT_PROVIDER, choices=[DEFAULT_PROVIDER, "claude_app"])
    send.add_argument("--mode", choices=MODES, help="auto_send (default, sends via the official CLI) or prefill (explicit opt-in; user presses Send)")
    send.add_argument("--workspace", help="absolute existing directory (codex path / claude code folder)")
    send.add_argument("--surface", choices=list(SURFACES), help="claude_app only: code (default) or chat")
    send.add_argument("--preview", action="store_true", help="print the prefill link only; launch nothing")
    send.add_argument("--config", default="connector-config/connectors.json")
    for command in ("doctor", "login", "run", "smoke"):
        sub = commands.add_parser(command)
        sub.add_argument("provider", choices=[*BINARIES, "grok_bot_desktop"])
        sub.add_argument("--config", default="connector-config/connectors.json")
        if command == "run":
            sub.add_argument("prompt")
            sub.add_argument("--workspace", required=True)
            sub.add_argument("--max-turns", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(args.directory, args.isolated_logins)
        elif args.command == "send":
            route = route_for(load_configuration(args.config), args.provider, mode=args.mode,
                              workspace=args.workspace, surface=args.surface)
            if args.preview:
                result = {"status": "preview", "link": route.preview(args.prompt), "launched": False}
            else:
                result = route.deliver(args.prompt)
            print(json.dumps(result, indent=2))
            return 0 if result["status"] in ("sent", "preview", "prefilled_awaiting_user_send") else 2
        else:
            configuration = load_configuration(args.config)
            adapter = adapter_for(configuration, args.provider)
            if args.command == "doctor":
                result = adapter.doctor()
            elif args.command == "login":
                adapter.auth_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                # This command is explicitly interactive. Only the official client handles credentials.
                with tempfile.TemporaryDirectory(prefix="connector-login-") as directory:
                    env = client_environment(args.provider, Path.home(), adapter.auth_directory,
                                             default_claude_login=adapter.default_claude_login)
                    tail = ["auth", "login"] if args.provider == "claude" else ["login"]
                    return subprocess.call([adapter.executable, *tail], cwd=directory, env=env)
            elif args.command == "smoke":
                result = smoke(adapter)
            else:
                if not 1 <= args.max_turns <= 32:
                    raise ValueError("max-turns must be between 1 and 32")
                broker = ToolBroker(args.workspace, configuration.policy, terminal_approval,
                                    protected_paths=(Path(args.config).resolve().parent, adapter.auth_directory))
                result = run(adapter, broker, args.prompt, max_turns=args.max_turns)
        print(json.dumps(result, indent=2))
        return 0 if result.get("status", "returned") == "returned" else 2
    except (AdapterError, ValueError, OSError) as error:
        # Never dump a traceback containing client environments or raw output.
        print(json.dumps({"status": "error", "type": type(error).__name__, "detail": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
