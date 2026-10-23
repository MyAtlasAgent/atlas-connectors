"""Real filesystem effects, process boundaries and standalone entry points."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from atlas_connectors.adapters import AdapterError, CLIAdapter, client_environment
from atlas_connectors.broker import PermissionDenied, ToolBroker, UnsupportedCapability
from atlas_connectors.cli import adapter_for, initialize
from atlas_connectors.permissions import PermissionPolicy, load_configuration
from atlas_connectors.process import ProcessError, execute
from atlas_connectors.protocol import parse
from atlas_connectors.runner import run


def policy(default="deny", **choices):
    return PermissionPolicy.from_dict({"version": 1, "default": default, "capabilities": choices})


def request(tool="write_file", path="result.txt", ident="one", **arguments):
    return {"id": ident, "tool": tool, "arguments": {"path": path, **arguments}}


class BrokerEffects(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def test_allow_writes_actual_file(self):
        broker = ToolBroker(self.workspace, policy(write_files="allow"))
        result = broker.call(request(text="hello"))
        self.assertEqual(result["status"], "executed")
        self.assertEqual((self.workspace / "result.txt").read_text(), "hello")

    def test_deny_prevents_actual_write(self):
        broker = ToolBroker(self.workspace, policy())
        self.assertEqual(broker.call(request(text="hello"))["status"], "denied")
        self.assertFalse((self.workspace / "result.txt").exists())

    def test_ask_without_real_approver_does_not_write(self):
        broker = ToolBroker(self.workspace, policy(write_files="ask"))
        self.assertEqual(broker.call(request(text="hello"))["status"], "denied")
        self.assertFalse((self.workspace / "result.txt").exists())

    def test_ask_can_be_approved_for_one_exact_call(self):
        seen = []
        def approve(capability, value):
            seen.append((capability, value))
            return value["id"] == "yes"
        broker = ToolBroker(self.workspace, policy(write_files="ask"), approve)
        self.assertEqual(broker.call(request(ident="yes", text="approved"))["status"], "executed")
        self.assertEqual(broker.call(request(path="no.txt", ident="no", text="no"))["status"], "denied")
        self.assertEqual(len(seen), 2)
        self.assertFalse((self.workspace / "no.txt").exists())

    def test_absolute_parent_and_symlink_reads_cannot_escape(self):
        secret = self.root / "outside.txt"
        secret.write_text("outside")
        (self.workspace / "alias").symlink_to(secret)
        broker = ToolBroker(self.workspace, policy(read_files="allow"))
        for i, path in enumerate((str(secret), "../outside.txt", "alias")):
            self.assertEqual(broker.call(request("read_file", path, ident=str(i)))["status"], "denied")

    def test_symlink_parent_cannot_write_outside(self):
        (self.workspace / "alias").symlink_to(self.root, target_is_directory=True)
        broker = ToolBroker(self.workspace, policy(write_files="allow"))
        self.assertEqual(broker.call(request(path="alias/escape.txt", text="bad"))["status"], "denied")
        self.assertFalse((self.root / "escape.txt").exists())

    def test_replacing_hardlink_does_not_truncate_external_target(self):
        outside = self.root / "outside.txt"
        outside.write_text("original")
        os.link(outside, self.workspace / "link.txt")
        broker = ToolBroker(self.workspace, policy(write_files="allow"))
        broker.call(request(path="link.txt", text="changed"))
        self.assertEqual(outside.read_text(), "original")

    def test_read_and_write_preferences_are_independent(self):
        (self.workspace / "input.txt").write_text("data")
        broker = ToolBroker(self.workspace, policy(read_files="allow"))
        self.assertEqual(broker.call(request("read_file", "input.txt"))["text"], "data")
        self.assertEqual(broker.call(request(text="no", ident="write"))["status"], "denied")

    def test_credentials_require_separate_user_permission(self):
        (self.workspace / ".env").write_text("fixture only")
        broker = ToolBroker(self.workspace, policy(read_files="allow"))
        self.assertEqual(broker.call(request("read_file", ".env"))["status"], "denied")

    def test_replay_is_idempotent_and_changed_arguments_refuse(self):
        broker = ToolBroker(self.workspace, policy(write_files="allow"))
        one = request(text="first")
        broker.call(one)
        (self.workspace / "result.txt").write_text("external update")
        broker.call(one)
        self.assertEqual((self.workspace / "result.txt").read_text(), "external update")
        with self.assertRaises(PermissionDenied):
            broker.call(request(text="different"))
        self.assertEqual(len(broker.events), 1)

    def test_shell_network_and_authority_requests_never_fall_back(self):
        broker = ToolBroker(self.workspace, policy(default="allow"))
        for tool in ("run_command", "fetch_url", "git_push", "merge", "install", "mcp"):
            with self.subTest(tool=tool), self.assertRaises(UnsupportedCapability):
                broker.call(request(tool))

    def test_git_metadata_is_not_a_regular_write(self):
        (self.workspace / ".git").mkdir()
        broker = ToolBroker(self.workspace, policy(default="allow"))
        self.assertEqual(broker.call(request(path=".git/config", text="bad"))["status"], "denied")

    def test_policy_and_login_directories_cannot_be_self_modified(self):
        config = self.workspace / "config"
        config.mkdir()
        broker = ToolBroker(self.workspace, policy(default="allow"), protected_paths=(config,))
        self.assertEqual(broker.call(request(path="config/permissions.json", text="bad"))["status"], "denied")

    def test_workspace_descriptor_survives_path_replacement(self):
        broker = ToolBroker(self.workspace, policy(write_files="allow"))
        renamed = self.root / "original"
        self.workspace.rename(renamed)
        self.workspace.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(broker.call(request(text="pinned"))["status"], "executed")
        self.assertEqual((renamed / "result.txt").read_text(), "pinned")
        self.assertFalse((self.root / "result.txt").exists())

    def test_file_size_limit_and_nonregular_file_refuse(self):
        (self.workspace / "large.txt").write_bytes(b"a" * 65537)
        broker = ToolBroker(self.workspace, policy(read_files="allow", write_files="allow"))
        self.assertEqual(broker.call(request("read_file", "large.txt"))["status"], "denied")
        self.assertEqual(broker.call(request(text="a" * 65537, ident="large_write"))["status"], "denied")
        os.mkfifo(self.workspace / "pipe")
        self.assertEqual(broker.call(request("read_file", "pipe", ident="pipe"))["status"], "denied")


class StandaloneRuntime(unittest.TestCase):
    def test_initialize_loads_without_atlas_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = initialize(temporary, isolated=True)
            configuration = load_configuration(result["configuration"])
            self.assertEqual(len(configuration.providers), 3)
            self.assertEqual(configuration.policy.preference("write_files"), "ask")
            with self.assertRaises(ValueError):
                initialize(temporary)

    def test_manual_or_legacy_route_cannot_silently_launch_a_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = initialize(temporary)
            path = Path(output["configuration"])
            config = json.loads(path.read_text())
            config["providers"]["codex"]["transport"] = "manual_delivery"
            path.write_text(json.dumps(config))
            with self.assertRaises(AdapterError):
                adapter_for(load_configuration(path), "codex")

    def test_api_override_refuses_instead_of_switching_billing(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-value"}):
            with self.assertRaises(AdapterError):
                client_environment("codex", "/tmp/fixture", "/tmp/auth")

    def test_client_environment_drops_arbitrary_secrets_and_atlas_state(self):
        with patch.dict(os.environ, {"ATLAS_OPS": "/fixture", "PRIVATE_SECRET": "fixture"}, clear=True):
            env = client_environment("codex", "/tmp/fixture", "/tmp/auth")
            self.assertNotIn("PRIVATE_SECRET", env)
            self.assertNotIn("ATLAS_OPS", env)
            self.assertEqual(env["CODEX_HOME"], "/tmp/auth")

    def test_grok_desktop_has_no_silent_cli_substitution(self):
        with self.assertRaises(AdapterError):
            CLIAdapter("grok_bot_desktop")

    def test_model_cannot_forge_approval_in_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            broker = ToolBroker(temporary, policy(default="allow"))
            result = broker.call(request(text="bad", approved=True))
            self.assertEqual(result["status"], "denied")

    def test_strict_protocol_rejects_injected_fields(self):
        with self.assertRaises(ValueError):
            parse(json.dumps({"kind": "tool", "approved": True}))

    def test_actual_subprocess_has_bounded_lifetime(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ProcessError):
                execute([sys.executable, "-c", "import time; time.sleep(60)"],
                        cwd=temporary, env={}, timeout=0.1)

    def test_loop_performs_real_effect_then_returns_distinct_model_claim(self):
        class FixtureAdapter:
            observations = []
            messages = iter([
                json.dumps({"kind": "tool", "id": "one", "tool": "write_file", "arguments": '{"path":"out.txt","text":"ok"}', "text": ""}),
                json.dumps({"kind": "final", "id": "", "tool": "", "arguments": "", "text": "done"}),
            ])
            def complete(self, prompt):
                return next(self.messages)
        with tempfile.TemporaryDirectory() as temporary:
            result = run(FixtureAdapter(), ToolBroker(temporary, policy(write_files="allow")), "write")
            self.assertEqual(Path(temporary, "out.txt").read_text(), "ok")
            self.assertEqual(result["text_provenance"], "model_claim")
            self.assertEqual(result["tools"][0]["status"], "executed")


if __name__ == "__main__":
    unittest.main()
