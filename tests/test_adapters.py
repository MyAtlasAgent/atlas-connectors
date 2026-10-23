"""Check the actual launch settings without starting a vendor client."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlas_connectors.adapters import API_OVERRIDES, AdapterError, CLIAdapter, failure_category

FINAL = {"kind": "final", "id": "", "tool": "", "arguments": "", "text": "fixture"}


class LaunchBoundary(unittest.TestCase):
    def test_every_transport_removes_native_effect_paths(self):
        for provider, version in (("claude", "2.1.246"), ("codex", "0.153.4"), ("supergrok_cli", "1.0.13")):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temporary:
                auth = Path(temporary) / "auth"
                auth.mkdir()
                (auth / "auth.json").write_text("fixture")
                calls = []
                def process(argv, **kwargs):
                    calls.append((argv, kwargs))
                    if "--version" in argv:
                        return 0, "client " + version, ""
                    if argv[1:] == ["auth", "status"]:
                        return 0, json.dumps({"authMethod": "claude.ai"}), ""
                    if argv[1:] == ["login", "status"]:
                        return 0, "Logged in using ChatGPT", ""
                    if "inspect" in argv:
                        return 0, "MCP Servers (0)\nPlugins (0)\nHooks (0)", ""
                    if provider == "codex":
                        Path(argv[argv.index("-o") + 1]).write_text(json.dumps(FINAL))
                        return 0, "", ""
                    return 0, json.dumps({"structured_output": FINAL}), ""
                with patch.dict(os.environ, {}, clear=True), patch("shutil.which", return_value="/fixture/client"), patch("atlas_connectors.adapters.execute", side_effect=process):
                    adapter = CLIAdapter(provider, auth_directory=str(auth))
                    self.assertEqual(json.loads(adapter.complete("fixture prompt")), FINAL)
                argv, options = calls[-1]
                self.assertNotIn("--continue", argv)
                self.assertNotIn("--resume", argv)
                self.assertNotIn(str(auth), " ".join(argv))
                self.assertTrue(Path(options["cwd"]).name.startswith("connector-client-"))
                if provider == "claude":
                    self.assertIn("--safe-mode", argv)
                    self.assertEqual(argv[argv.index("--tools") + 1], "")
                    self.assertIn("--strict-mcp-config", argv)
                elif provider == "codex":
                    self.assertIn("--ignore-user-config", argv)
                    self.assertIn("--ephemeral", argv)
                    self.assertIn("features.shell_tool=false", argv)
                    self.assertIn("features.hooks=false", argv)
                    self.assertIn("mcp_servers={}", argv)
                else:
                    self.assertIn("--prompt-file", argv)
                    self.assertNotIn("fixture prompt", argv)
                    self.assertEqual(argv[argv.index("--tools") + 1], "")
                    self.assertIn("--no-subagents", argv)
                    self.assertIn("--disable-web-search", argv)
                    self.assertEqual(argv[argv.index("--deny") + 1], "*")

    def test_unknown_client_version_never_reaches_inference(self):
        with patch("shutil.which", return_value="/fixture/client"), patch("atlas_connectors.adapters.execute", return_value=(0, "client 99.0.0", "")) as execute:
            with self.assertRaises(AdapterError):
                CLIAdapter("codex").complete("fixture")
            self.assertEqual(execute.call_count, 1)

    def test_credential_and_prompt_errors_use_fixed_categories(self):
        self.assertEqual(failure_category("private prompt: invalid api key fixture", ""), "AUTH_REQUIRED")
        self.assertEqual(failure_category("private prompt: quota fixture", ""), "CAPACITY_WAIT")
        self.assertEqual(failure_category("private data", ""), "PROVIDER_FAILURE")

    def test_cached_api_login_refuses_before_inference(self):
        for provider, version in (("claude", "2.1.246"), ("codex", "0.153.4")):
            with self.subTest(provider=provider), patch.dict(os.environ, {}, clear=True), patch("shutil.which", return_value="/fixture/client"):
                def process(argv, **kwargs):
                    if "--version" in argv:
                        return 0, "client " + version, ""
                    if provider == "claude":
                        return 0, json.dumps({"authMethod": "api_key"}), ""
                    return 0, "Logged in using an API key", ""
                with patch("atlas_connectors.adapters.execute", side_effect=process) as execute:
                    with self.assertRaises(AdapterError):
                        CLIAdapter(provider).complete("fixture")
                    self.assertEqual(execute.call_count, 2)


if __name__ == "__main__":
    unittest.main()
