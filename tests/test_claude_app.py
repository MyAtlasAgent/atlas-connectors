"""claude_app route: prefill only, honest state, no launches, shared policy."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from atlas_connectors.adapters import AdapterError
from atlas_connectors.claude_app import MAX_PROMPT_CHARS, ClaudeAppRoute, prefill_link, system_opener
from atlas_connectors.cli import initialize, main, route_for
from atlas_connectors.codex_app import DEFAULT_PROVIDER
from atlas_connectors.permissions import PROVIDERS, ConfigurationError, PermissionPolicy, load_configuration

ROOT = Path(__file__).resolve().parents[1]
POLICY = PermissionPolicy.from_dict({"version": 1, "default": "ask", "capabilities": {}})


class ClaudeApp(unittest.TestCase):
    def setUp(self):
        self.opened = []

    def route(self, **kwargs):
        return ClaudeAppRoute(POLICY, opener=self.opened.append, **kwargs)

    def test_surfaces_and_encoding(self):
        prompt = "Hi & bye? #1 a+b=c / 100% \"q\" é\nnext"
        code, chat = prefill_link(prompt), prefill_link(prompt, "chat")
        self.assertTrue(code.startswith("claude://code/new?q="))
        self.assertTrue(chat.startswith("claude://claude.ai/new?q="))
        for link in (code, chat):
            tail = link.split("q=", 1)[1]
            for char in "&?#+= /\"\n":
                self.assertNotIn(char, tail)
            self.assertEqual(parse_qs(urlsplit(link).query)["q"], [prompt])
        with self.assertRaises(AdapterError):
            prefill_link("x", "cowork")

    def test_overlong_prompt_refused_instead_of_truncated(self):
        prefill_link("x" * MAX_PROMPT_CHARS)
        with self.assertRaisesRegex(AdapterError, "truncate"):
            self.route().deliver("x" * (MAX_PROMPT_CHARS + 1))
        self.assertEqual(self.opened, [])

    def test_folder_must_be_absolute_existing_dir_and_code_only(self):
        with tempfile.TemporaryDirectory() as directory:
            link = self.route(folder=directory).deliver("hi")["link"]
            self.assertEqual(parse_qs(urlsplit(link).query)["folder"], [str(Path(directory).resolve())])
            for kwargs in ({"folder": "relative"}, {"folder": directory + "/missing"},
                           {"folder": directory, "surface": "chat"}):
                with self.assertRaises(AdapterError):
                    self.route(**kwargs)
        self.assertEqual(len(self.opened), 1)

    def test_auto_send_refused_without_cli_fallback(self):
        with patch("atlas_connectors.adapters.CLIAdapter", side_effect=AssertionError("cli")):
            with self.assertRaisesRegex(AdapterError, "prefill-only"):
                self.route(mode="auto_send")
        self.assertEqual(self.opened, [])

    def test_deliver_is_prefill_only_and_honest(self):
        result = self.route().deliver("hello")
        self.assertEqual(self.opened, ["claude://code/new?q=hello"])
        self.assertEqual(result["status"], "prefilled_awaiting_user_send")
        self.assertFalse(result["executed"])
        self.assertFalse(result["returned"])

    def test_real_opener_accepts_only_claude_links(self):
        with patch("subprocess.run", side_effect=AssertionError("launched")), \
                patch("os.startfile", side_effect=AssertionError("launched"), create=True):
            for url in ("codex://threads/new", "https://example.com", "file:///etc/passwd"):
                with self.assertRaises(AdapterError):
                    system_opener(url)

    def test_shared_policy_parity_and_config(self):
        self.assertIn("claude_app", PROVIDERS)
        example = load_configuration(ROOT / "examples/operators.example.json")
        self.assertIs(example.permissions_for("claude_app"), example.policy)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(initialize(temporary)["configuration"])
            configuration = load_configuration(path)
            self.assertEqual(next(iter(configuration.providers)), DEFAULT_PROVIDER)
            route = route_for(configuration, "claude_app", opener=self.opened.append)
            self.assertIs(route.policy, configuration.policy)
            self.assertEqual(route.surface, "code")
            with patch("sys.stdout"):
                self.assertEqual(main(["send", "hi", "--provider", "claude_app", "--surface", "chat",
                                       "--preview", "--config", str(path)]), 0)
                with patch("sys.stderr"):
                    self.assertEqual(main(["send", "hi", "--provider", "claude_app", "--mode", "auto_send",
                                           "--config", str(path)]), 2)
            config = json.loads(path.read_text())
            config["providers"]["claude_app"]["mode"] = "auto_send"
            path.write_text(json.dumps(config))
            with self.assertRaises(ConfigurationError):
                load_configuration(path)
        self.assertEqual(self.opened, [])


if __name__ == "__main__":
    unittest.main()
