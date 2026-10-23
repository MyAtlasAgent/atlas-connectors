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
from . import peers as peerlib
from . import workspaces as wslib
from .broker import ToolBroker
from .permissions import PROVIDERS, PermissionPolicy, load_configuration
from .protocol import INSTRUCTIONS, parse
from .runner import run


def initialize(directory, isolated=False, workspace=None):
    root = Path(directory).resolve()
    destinations = [root / "connectors.json", root / "permissions.json"]
    if any(path.exists() for path in destinations):
        raise ValueError("Configuration already exists; no files overwritten")
    if workspace is not None:
        workspace = Path(workspace)
        if not workspace.is_absolute() or not workspace.is_dir():
            raise ValueError("--workspace must be an absolute existing directory")
        workspace = str(workspace.resolve())
    root.mkdir(parents=True, exist_ok=True)
    # New users default to the Codex desktop app on their ChatGPT login.
    providers = {DEFAULT_PROVIDER: {"transport": "codex_app", "mode": DEFAULT_MODE, "executable": "codex"},
                 "claude_app": {"transport": "claude_app", "mode": "auto_send", "surface": "code",
                                "executable": "claude"}}
    if workspace is not None:
        for name in (DEFAULT_PROVIDER, "claude_app"):
            providers[name]["workspace"] = workspace
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
                              folder=(workspace or route.get("folder")) if (mode or route.get("mode")) == "prefill" else route.get("folder"),
                              workspace=workspace or route.get("workspace"), opener=opener,
                              executable=route.get("executable", "claude"),
                              timeout=route.get("timeout", 300), runner=runner)
    if route.get("transport") != "codex_app":
        raise AdapterError("Selected route is not codex_app; use `run` for CLI providers; no substitution")
    return CodexAppRoute(configuration.permissions_for(provider), mode=mode or route.get("mode", DEFAULT_MODE),
                         executable=route.get("executable", "codex"),
                         workspace=workspace or route.get("workspace"),
                         timeout=route.get("timeout", 300), runner=runner, opener=opener)


def peer_command(args, *, detector=None, route_factory=None):
    root = Path(args.data_dir) if getattr(args, "data_dir", None) else None
    if args.command == "peers":
        return (detector or peerlib.detect)(root=root)
    if args.command == "register":
        detected = (detector or peerlib.detect)(root=root).get(args.provider, {})
        return peerlib.register(args.provider, root=root, routes=detected.get("routes") or None)
    if args.mail_command == "inbox":
        return peerlib.inbox(args.provider, root=root, mark_read=args.mark_read)
    if args.mail_command == "reply":
        return peerlib.reply(args.sender, args.message_id, args.body, root=root)
    if args.deliver and args.recipient not in peerlib.DELIVERABLE:
        raise ValueError(f"{args.recipient} is mailbox-only; --deliver supports {', '.join(peerlib.DELIVERABLE)}")
    message = peerlib.send(args.sender, args.recipient, args.body, root=root)
    result = {"status": "queued", "message": message}
    if args.deliver:
        # Uses the recipient's normal auto_send route and the user's shared policy, unchanged.
        factory = route_factory or (lambda: route_for(load_configuration(args.config), args.recipient,
                                                       mode="auto_send", workspace=args.workspace))
        result["delivery"] = factory().deliver(peerlib.delivery_prompt(message))
        result["status"] = result["delivery"]["status"]
    return result


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
    init.add_argument("--peer-instructions", action="store_true",
                      help="print a CLAUDE.md/AGENTS.md snippet about peers and mail; writes nothing")
    init.add_argument("--workspace", help="absolute project directory used by auto_send (default: current directory)")
    send = commands.add_parser("send", help="default route: deliver a prompt to the Codex desktop app")
    send.add_argument("prompt")
    send.add_argument("--provider", default=DEFAULT_PROVIDER, choices=[DEFAULT_PROVIDER, "claude_app"])
    send.add_argument("--mode", choices=MODES, help="auto_send (default, sends via the official CLI) or prefill (explicit opt-in; user presses Send)")
    send.add_argument("--workspace", help="absolute project directory; overrides config `workspace`, else the current directory")
    send.add_argument("--surface", choices=list(SURFACES), help="claude_app only: code (default) or chat")
    send.add_argument("--preview", action="store_true", help="print the prefill link only; launch nothing")
    send.add_argument("--config", default="connector-config/connectors.json")
    data = argparse.ArgumentParser(add_help=False)
    data.add_argument("--data-dir", help="override the per-user atlas-connectors data directory")
    peers = commands.add_parser("peers", parents=[data], help="detect local providers; sends nothing")
    peers.add_argument("--json", action="store_true")
    reg = commands.add_parser("register", parents=[data], help="record a provider in the local peer registry")
    reg.add_argument("provider", choices=list(PROVIDERS))
    mail = commands.add_parser("mail", help="local mailbox between providers")
    mailcmd = mail.add_subparsers(dest="mail_command", required=True)
    msend = mailcmd.add_parser("send", parents=[data])
    msend.add_argument("--from", dest="sender", required=True, choices=list(PROVIDERS))
    msend.add_argument("--to", dest="recipient", required=True, choices=list(PROVIDERS))
    msend.add_argument("body")
    msend.add_argument("--deliver", action="store_true", help="also auto_send into codex_app/claude_app")
    msend.add_argument("--config", default="connector-config/connectors.json")
    msend.add_argument("--workspace")
    minbox = mailcmd.add_parser("inbox", parents=[data])
    minbox.add_argument("provider", choices=list(PROVIDERS))
    minbox.add_argument("--mark-read", action="store_true")
    mreply = mailcmd.add_parser("reply", parents=[data])
    mreply.add_argument("--from", dest="sender", required=True, choices=list(PROVIDERS))
    mreply.add_argument("message_id")
    mreply.add_argument("body")
    ws = commands.add_parser("workspace", help="shared folders where providers work together")
    wscmd = ws.add_subparsers(dest="workspace_command", required=True)
    wcreate = wscmd.add_parser("create", parents=[data])
    wcreate.add_argument("name")
    wcreate.add_argument("--base", help="parent directory (default: <Documents>/Atlas Workspaces)")
    wcreate.add_argument("--exist-ok", action="store_true")
    wcreate.add_argument("--set-default", action="store_true")
    wcreate.add_argument("--with-agent-instructions", action="store_true",
                         help="write CLAUDE.md and AGENTS.md inside the new folder only")
    wcreate.add_argument("--config", default="connector-config/connectors.json")
    wscmd.add_parser("list", parents=[data])
    wuse = wscmd.add_parser("use", parents=[data])
    wuse.add_argument("name")
    wuse.add_argument("--config", default="connector-config/connectors.json")
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
        if args.command == "init" and args.peer_instructions:
            print(peerlib.PEER_INSTRUCTIONS, end="")
            return 0
        if args.command == "workspace":
            root = Path(args.data_dir) if args.data_dir else None
            if args.workspace_command == "create":
                result = wslib.create(args.name, base=args.base, exist_ok=args.exist_ok,
                                      with_agent_instructions=args.with_agent_instructions, root=root)
                if args.set_default:
                    result["routes_updated"] = wslib.set_default(args.config, result["path"])
            elif args.workspace_command == "list":
                result = wslib.registered(root)
            else:
                result = wslib.use(args.name, args.config, root)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command in ("peers", "register", "mail"):
            result = peer_command(args)
            if args.command == "peers" and not args.json:
                for name, entry in result.items():
                    state = "present" if entry["present"] else "absent"
                    routes = ", ".join(entry["routes"]) or "none"
                    print(f"{name}: {state}; routes: {routes}" + (
                        "; why not: " + "; ".join(entry["why_not"]) if entry["why_not"] else ""))
                return 0
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if not isinstance(result, dict) or result.get("status") != "error" else 2
        if args.command == "init":
            result = initialize(args.directory, args.isolated_logins, args.workspace)
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
