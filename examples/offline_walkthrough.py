"""Exercise existing extracted functions using synthetic data; no provider launch."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from atlas_connectors.permissions import CAPABILITIES, load_configuration


def no_external_execution(event, args):
    if event in {"subprocess.Popen", "os.system", "socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError("Offline walkthrough refuses external execution/network")


def main():
    sys.addaudithook(no_external_execution)
    manifest = json.loads((ROOT / "SOURCE_MANIFEST.json").read_text())
    for entry in manifest["files"]:
        actual = hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest()
        if actual != entry["packaged_sha256"]:
            raise RuntimeError("Reference hash mismatch: " + entry["path"])
    print(f"Reference integrity: {len(manifest['files'])} files match the manifest")

    # Select a function, never import the live dispatcher's top-level program.
    source = (ROOT / "reference/shared/build_cmd.py.txt").read_text()
    parsed = ast.parse(source)
    selected = [n for n in parsed.body if isinstance(n, ast.FunctionDef)
                and n.name == "build_cmd"]
    if len(selected) != 1:
        raise RuntimeError("Expected exactly one existing build_cmd function")
    receipts = []
    with tempfile.TemporaryDirectory(prefix="atlas-connectors-offline-") as temporary:
        worktree = Path(temporary)
        packet = worktree / "packet.md"
        packet.write_text((ROOT / "examples/packet.md").read_text())
        context = {
            "OPS": temporary,
            "CFG": {"forbidden_flags": ["--dangerously-skip-permissions"]},
            # Demo substitutions: no real routing or durable receipt writer.
            "apply_model_routing": lambda op, cmd, packet: cmd,
            "write_route_receipt": lambda *args: receipts.append("DEMO_MEMORY_ONLY"),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "existing_build_cmd", "exec"), context)
        configuration = load_configuration(ROOT / "examples/operators.example.json")
        routes = configuration.providers
        for provider in routes:
            assert configuration.permissions_for(provider) is configuration.policy
            assert all(configuration.permissions_for(provider).preference(capability) == "ask"
                       for capability in CAPABILITIES)
        print("All six providers inherit the same user-selected permissions (example: ask)")
        for name in ("claude", "codex", "supergrok_cli"):
            command = context["build_cmd"](routes[name], str(packet), temporary, callsign="DEMO")
            assert command and "{prompt}" not in " ".join(command)
            display = ["<scoped prompt>" if len(arg) > 250 else arg.replace(temporary, "<TEMP>")
                       for arg in command]
            print(name + " argv preview (not executed): " + json.dumps(display))
        assert len(receipts) == 3
        assert routes["grok_bot_desktop"]["cmd"] is None
        print("Grok Bot desktop: manual packet delivery, no automatic command")

        from atlas_connectors.codex_app import CodexAppRoute, thread_link
        opened = []
        app = CodexAppRoute(configuration.permissions_for("codex_app"), mode=routes["codex_app"]["mode"],
                            workspace=temporary, opener=opened.append,
                            runner=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no launch")))
        link = app.preview("Say hello & name your model?")
        print("codex_app (default route, mode=" + app.mode + ") prefill opt-in link preview (not opened): "
              + link.replace(quote(str(Path(temporary).resolve()), safe=""), "<TEMP>"))
        from atlas_connectors.codex_app import sandbox_for
        print("codex_app auto_send would run (not executed): codex app-server -> initialize, initialized, "
              "thread/start{cwd:<workspace>, sandbox:" + sandbox_for(app.policy) + ", approvalPolicy:never}, "
              "turn/start, wait turn/completed")
        print("codex_app auto_send would then open (not opened): "
              + thread_link("00000000-0000-0000-0000-000000000000"))
        from atlas_connectors.claude_app import ClaudeAppRoute
        claude = ClaudeAppRoute(configuration.permissions_for("claude_app"), mode=routes["claude_app"]["mode"],
                                surface=routes["claude_app"]["surface"], opener=opened.append)
        from atlas_connectors.claude_app import permission_mode_for
        print("claude_app (mode=" + claude.mode + ") would run (not executed): claude -p --permission-mode "
              + permission_mode_for(claude.policy) + " --output-format json  (prompt on stdin, cwd=<workspace>), "
              "then detached: claude --desktop --resume <session_id>")
        print("claude_app prefill opt-in link preview (not opened): "
              + claude.preview("Say hello & name your model?"))
        assert opened == []

        from atlas_connectors import peers
        mailroot = worktree / "peer-data"
        message = peers.send("claude_app", "grok_bot_desktop", "Status update — synthetic", root=mailroot)
        received = peers.inbox("grok_bot_desktop", root=mailroot, mark_read=True)
        assert [m["id"] for m in received] == [message["id"]] and received[0]["trust"].startswith("untrusted_data")
        answer = peers.reply("grok_bot_desktop", message["id"], "Acknowledged", root=mailroot)
        assert answer["to"] == "claude_app" and answer["reply_to"] == message["id"]
        print("Peer mailbox: send -> inbox (untrusted data) -> reply round trip in a temp dir; nothing delivered")

        forbidden = {"prompt_mode": "pointer", "cmd": ["fixture", "--dangerously-skip-permissions"]}
        try:
            context["build_cmd"](forbidden, str(packet), temporary)
        except RuntimeError as error:
            assert "forbidden flag" in str(error)
        else:
            raise AssertionError("Existing forbidden-flag check did not refuse")
        print("Negative control: forbidden command flag refused")

        # Inspected validator has no top-level execution except its __main__ guard.
        validator = runpy.run_path(str(ROOT / "reference/grok/grok-return-validate.py.txt"))
        observed = "a" * 40
        cases = [
            ("matching", f"STATE: RETURNED\nHEAD: {observed}\nPR: none\nSynthetic findings", 0, "RETURNED"),
            ("wrong_sha", f"STATE: RETURNED\nHEAD: {'b' * 40}\nPR: none\nSynthetic findings", 0, "BLOCKED"),
            ("placeholder", "STATE: RETURNED\nHEAD: <exact sha>\nPR: none", 4, None),
            ("missing_state", "Synthetic prose with no return header", 3, None),
        ]
        for name, raw, expected_rc, expected_state in cases:
            incoming = worktree / (name + ".candidate")
            outgoing = worktree / (name + ".return")
            incoming.write_text(raw)
            args = ["fixture-validator", str(incoming), observed, str(outgoing), "SAMPLE-001"]
            with patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
                rc = validator["main"]()
            assert rc == expected_rc, (name, rc)
            if expected_state:
                assert outgoing.read_text().startswith(f"STATE: {expected_state}\nHEAD: {observed}\n")
            else:
                assert not outgoing.exists()
            print(f"Return case {name}: exit={rc}, state={expected_state or 'not persisted'}")

    print("PASS: offline extraction walkthrough; no live provider or authority qualification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
