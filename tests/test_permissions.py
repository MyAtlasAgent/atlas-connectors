"""User choices stay equal when the transport/provider changes."""
import copy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import unittest

from atlas_connectors.model_policy import ModelPinError, ensure_unpinned_command
from atlas_connectors.permissions import (
    CAPABILITIES, PROVIDERS, ConfigurationError, PermissionPolicy, load_configuration,
)

ROOT = Path(__file__).resolve().parents[1]


class SharedPermissions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = json.loads((ROOT / "examples/operators.example.json").read_text())
        self.policy = {"version": 1, "default": "ask", "capabilities": {}}

    def load(self):
        (self.root / "permissions.json").write_text(json.dumps(self.policy))
        path = self.root / "operators.json"
        path.write_text(json.dumps(self.config))
        return load_configuration(path)

    def test_all_four_providers_share_the_same_policy_object(self):
        loaded = self.load()
        self.assertEqual(set(loaded.providers), set(PROVIDERS))
        for provider in PROVIDERS:
            self.assertIs(loaded.permissions_for(provider), loaded.policy)

    def test_all_providers_use_the_model_the_user_selects_in_their_session(self):
        loaded = self.load()
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                route = loaded.model_selection_for(provider)
                self.assertEqual(route.strategy, "provider_session")
                self.assertIsNone(route.requested_model)
                self.assertEqual(route.observed_model, "UNKNOWN")
                self.assertEqual(route.observation_source, "UNKNOWN")
                self.assertNotIn("model", loaded.providers[provider])
                self.assertNotIn("model_id", loaded.providers[provider])

    def test_a_model_pin_is_not_a_connection_requirement(self):
        # Account models may disappear or be renamed without changing Atlas.
        for provider in PROVIDERS:
            for field in ("model", "model_id", "preferred_model", "fallback_model",
                          "model_candidates", "required_model"):
                with self.subTest(provider=provider, field=field):
                    old = copy.deepcopy(self.config)
                    self.config["providers"][provider][field] = "obsolete-model-id"
                    with self.assertRaises(ConfigurationError):
                        self.load()
                    self.config = old

    def test_model_selector_flags_are_refused_for_any_provider(self):
        for provider in PROVIDERS:
            for args in (["--model", "obsolete"], ["--model=obsolete"],
                         ["--model-id", "obsolete"], ["--fallback-model=obsolete"],
                         ["--preferred-model", "obsolete"], ["-m", "obsolete"],
                         ["-m=obsolete"]):
                with self.subTest(provider=provider, args=args):
                    old = copy.deepcopy(self.config)
                    self.config["providers"][provider]["cmd"] = ["provider-cli", *args]
                    with self.assertRaises(ModelPinError):
                        self.load()
                    self.config = old

    def test_post_routing_model_injection_is_refused(self):
        # The existing extracted build_cmd calls apply_model_routing AFTER argv
        # assembly. Future adapters must validate that final command too.
        for command in (["claude", "-p", "prompt"],
                        ["codex", "exec", "-o", "return.md", "prompt"],
                        ["grok-mission", "packet", "out", "work"]):
            with self.subTest(command=command[0]):
                self.assertEqual(ensure_unpinned_command(command), tuple(command))
                with self.assertRaises(ModelPinError):
                    ensure_unpinned_command([*command, "--model=removed-from-free-tier"])

    def test_bad_provider_cannot_claim_a_model_selection(self):
        with self.assertRaises(ConfigurationError):
            self.load().model_selection_for("not-configured")

    def test_unselected_actions_ask_equally(self):
        loaded = self.load()
        for provider in PROVIDERS:
            for capability in CAPABILITIES:
                self.assertEqual(loaded.permissions_for(provider).preference(capability), "ask")

    def test_user_change_applies_to_every_provider_including_codex(self):
        self.policy["capabilities"] = {"write_files": "allow", "network": "deny", "git_push": "ask"}
        loaded = self.load()
        for provider in PROVIDERS:
            self.assertEqual(loaded.permissions_for(provider).preference("write_files"), "allow")
            self.assertEqual(loaded.permissions_for(provider).preference("network"), "deny")
            self.assertEqual(loaded.permissions_for(provider).preference("git_push"), "ask")

    def test_user_can_select_each_shared_default(self):
        for choice in ("allow", "ask", "deny"):
            self.policy["default"] = choice
            loaded = self.load()
            for provider in PROVIDERS:
                self.assertEqual(set(loaded.permissions_for(provider).as_dict().values()), {choice})

    def test_provider_specific_override_is_refused(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                self.config["providers"][provider]["permissions"] = {"write_files": "allow"}
                with self.assertRaises(ConfigurationError):
                    self.load()
                del self.config["providers"][provider]["permissions"]

    def test_native_policy_flags_cannot_reintroduce_provider_defaults(self):
        for flag in ("-s", "-sread-only", "--sandbox=read-only", "--permission-mode=acceptEdits",
                     "--allowedTools", "--settings", "-c", "--yolo"):
            with self.subTest(flag=flag):
                config = copy.deepcopy(self.config)
                self.config["providers"]["codex"]["cmd"].append(flag)
                with self.assertRaises(ConfigurationError):
                    self.load()
                self.config = config

    def test_missing_user_policy_is_not_replaced_by_vendor_defaults(self):
        del self.config["permission_policy"]
        with self.assertRaises(ConfigurationError):
            self.load()

    def test_unknown_capability_or_invalid_choice_is_refused(self):
        for capabilities in ({"invented": "allow"}, {"read_files": True}, {"read_files": "auto"}):
            with self.subTest(capabilities=capabilities):
                self.policy["capabilities"] = capabilities
                with self.assertRaises(ConfigurationError):
                    self.load()

    def test_unknown_provider_cannot_silently_inherit_a_native_default(self):
        self.config["providers"]["unknown"] = {"cmd": ["fixture"]}
        with self.assertRaises(ConfigurationError):
            self.load()

    def test_policy_snapshot_and_routes_cannot_be_mutated(self):
        loaded = self.load()
        with self.assertRaises(FrozenInstanceError):
            loaded.policy._choices = ()
        with self.assertRaises(TypeError):
            loaded.providers["codex"]["cmd"] = ("fixture",)

    def test_policy_path_cannot_escape_through_parent_or_symlink(self):
        self.config["permission_policy"] = "../outside.json"
        with self.assertRaises(ConfigurationError):
            self.load()
        (self.root / "linked.json").symlink_to(self.root.parent / "outside.json")
        self.config["permission_policy"] = "linked.json"
        with self.assertRaises(ConfigurationError):
            self.load()

    def test_policy_fingerprint_changes_when_user_changes_permissions(self):
        first = self.load().policy.fingerprint
        self.policy["capabilities"]["write_files"] = "allow"
        self.assertNotEqual(first, self.load().policy.fingerprint)

    def test_invalid_direct_policy_cannot_bypass_normalization(self):
        with self.assertRaises(ConfigurationError):
            PermissionPolicy((("read_files", "allow"),))


if __name__ == "__main__":
    unittest.main()
