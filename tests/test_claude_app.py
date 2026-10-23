"""claude_app route: prefill only, honest state, no launches, shared policy."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from atlas_connectors.adapters import AdapterError
from atlas_connectors.claude_app import MAX_PROMPT_CHARS, permission_mode_for, ClaudeAppRoute, prefill_link, system_opener
from atlas_connectors.cli import initialize, main, route_for
from atlas_connectors.codex_app import DEFAULT_PROVIDER
from atlas_connectors.permissions import PROVIDERS, ConfigurationError, PermissionPolicy, load_configuration

ROOT = Path(__file__).resolve().parents[1]
POLICY = PermissionPolicy.from_dict({"version": 1, "default": "ask", "capabilities": {}})
SID = "5b2f6c1e-8d4a-4c3b-9e7f-0a1b2c3d4e5f"
NO_API = {k: "" for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                          "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                          "OPENAI_API_KEY", "OPENAI_BASE_URL", "XAI_API_KEY")}


class FakeClaude:
    def __init__(self, version="2.1.229 (Claude Code)", status=None, result=None):
        self.version, self.calls, self.stdin = version, [], []
        self.status = status if status is not None else {"loggedIn": True, "authMethod": "claude.ai"}
        self.result = result or (0, json.dumps({"type": "result", "is_error": False,
                                                 "result": "Hello back", "session_id": SID}), "")

    def __call__(self, argv, *, cwd, env, stdin="", timeout=120):
        self.calls.append(list(argv))
        if argv[1:] == ["--version"]:
            return 0, self.version, ""
        if argv[1:] == ["auth", "status"]:
            return 0, json.dumps(self.status), ""
        self.stdin.append(stdin)
        return self.result


class ClaudeApp(unittest.TestCase):
    def setUp(self):
        self.opened = []
        env = patch.dict(os.environ, NO_API)
        env.start()
        self.addCleanup(env.stop)

    def route(self, **kwargs):
        kwargs.setdefault("mode", "prefill")
        return ClaudeAppRoute(kwargs.pop("policy", POLICY), opener=self.opened.append,
                              which=lambda name: "/usr/bin/true", session_factory=lambda: SID, **kwargs)

    def send(self, runner, **kwargs):
        return self.route(mode="auto_send", runner=runner, **kwargs).deliver("--hello")

    def test_default_mode_is_auto_send(self):
        self.assertEqual(ClaudeAppRoute(POLICY).mode, "auto_send")

    def test_auto_send_success_returns_reply_session_and_resume(self):
        runner = FakeClaude()
        result = self.send(runner)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["reply_text"], "Hello back")
        self.assertEqual(result["session_id"], SID)
        self.assertEqual(result["session_id_source"], "envelope")
        self.assertEqual(result["resume_command"], f"claude --resume {SID}")
        self.assertFalse(result["app_thread_opened"])
        self.assertIn("no documented deep link", result["delivery"])
        self.assertEqual(self.opened, [])
        argv = runner.calls[-1]
        self.assertEqual(argv[1:4], ["-p", "--session-id", SID])
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
        self.assertEqual(runner.stdin, ["--hello"])  # prompt on stdin, never parsed as a flag
        for token in argv:
            self.assertNotIn("dangerously", token)
            self.assertNotIn(token, ("bypassPermissions", "dontAsk"))

    def test_session_id_falls_back_to_requested_or_reports_difference(self):
        other = "11111111-2222-4333-8444-555555555555"
        for envelope, source, expected in (({"result": "x"}, "requested_only", SID),
                                           ({"result": "x", "session_id": other}, "envelope_differs_from_requested", other)):
            result = self.send(FakeClaude(result=(0, json.dumps(envelope), "")))
            self.assertEqual((result["session_id_source"], result["session_id"]), (source, expected))

    def test_permission_mode_follows_shared_policy(self):
        allow = PermissionPolicy.from_dict({"version": 1, "default": "deny",
                                            "capabilities": {"read_files": "allow", "write_files": "allow"}})
        self.assertEqual(permission_mode_for(POLICY), "plan")
        self.assertEqual(permission_mode_for(allow), "acceptEdits")
        with self.assertRaises(AdapterError):
            permission_mode_for(PermissionPolicy.from_dict({"version": 1, "default": "deny", "capabilities": {}}))

    def test_preflight_refusals_send_nothing(self):
        for runner in (FakeClaude(version="2.0.1"), FakeClaude(status={"loggedIn": False, "authMethod": "claude.ai"}),
                       FakeClaude(status={"loggedIn": True, "authMethod": "api_key"}),
                       FakeClaude(status={"loggedIn": True, "authMethod": "console"})):
            with self.assertRaises(AdapterError):
                self.send(runner)
            self.assertEqual(runner.stdin, [])
        for key in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK"):
            runner = FakeClaude()
            with patch.dict(os.environ, {key: "fixture"}), self.assertRaises(AdapterError):
                self.send(runner)
            self.assertEqual(runner.calls, [])
        self.assertEqual(self.opened, [])

    def test_cli_failure_runs_once_opens_nothing_no_fallback(self):
        for result in ((1, "", "boom"), (0, json.dumps({"is_error": True, "result": "x"}), ""), (0, "not json", "")):
            runner = FakeClaude(result=result)
            with self.assertRaisesRegex(AdapterError, "no fallback"):
                self.send(runner)
            self.assertEqual(len(runner.stdin), 1)
        self.assertEqual(self.opened, [])

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

    def test_unknown_mode_refused(self):
        with self.assertRaises(AdapterError):
            self.route(mode="keystrokes")
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
            self.assertEqual(route.mode, "auto_send")
            self.assertEqual(route_for(configuration, "claude_app", mode="prefill").mode, "prefill")
            with patch("sys.stdout"):
                self.assertEqual(main(["send", "hi", "--provider", "claude_app", "--surface", "chat",
                                       "--preview", "--config", str(path)]), 0)
            config = json.loads(path.read_text())
            config["providers"]["claude_app"]["mode"] = "keystrokes"
            path.write_text(json.dumps(config))
            with self.assertRaises(ConfigurationError):
                load_configuration(path)
        self.assertEqual(self.opened, [])


if __name__ == "__main__":
    unittest.main()
