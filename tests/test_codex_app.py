"""codex_app default route: encoding, validation, honest delivery, no launches."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from atlas_connectors.adapters import AdapterError
from atlas_connectors.cli import initialize, main, route_for
from atlas_connectors.codex_app import (
    DEFAULT_PROVIDER, MAX_LINK_BYTES, CodexAppRoute, prefill_link, sandbox_for, thread_link)
from atlas_connectors.permissions import PROVIDERS, PermissionPolicy, load_configuration

ROOT = Path(__file__).resolve().parents[1]
SID = "01a169df-495e-79f3-891a-b3c96eed6b34"


def policy(default="ask", **capabilities):
    return PermissionPolicy.from_dict({"version": 1, "default": default, "capabilities": capabilities})


class FakeRunner:
    def __init__(self, exec_result=(0, "", f"session id: {SID}\n"), version="codex-cli 0.153.4",
                 login="Logged in using ChatGPT"):
        self.calls, self.exec_result, self.version, self.login = [], exec_result, version, login

    def __call__(self, argv, *, cwd, env, stdin="", timeout=120):
        self.calls.append(list(argv))
        if argv[1:] == ["--version"]:
            return 0, self.version, ""
        if argv[1:3] == ["login", "status"]:
            return 0, self.login, ""
        output = Path(argv[argv.index("-o") + 1])
        if self.exec_result[0] == 0:
            output.write_text("hello back")
        return self.exec_result


class Route(unittest.TestCase):
    def setUp(self):
        self.opened = []
        self.sessions = tempfile.TemporaryDirectory()
        self.addCleanup(self.sessions.cleanup)
        env = patch.dict(os.environ, {k: "" for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL")})
        env.start()
        self.addCleanup(env.stop)

    def route(self, runner=None, **kwargs):
        kwargs.setdefault("policy", policy())
        return CodexAppRoute(kwargs.pop("policy"), runner=runner or FakeRunner(), opener=self.opened.append,
                             sessions_dir=self.sessions.name, which=lambda name: "/usr/bin/true", **kwargs)

    # prefill link
    def test_prefill_link_encodes_every_reserved_character(self):
        prompt = "Hi & bye? #1 a+b=c / 100% \"q\" é\nnext"
        link = prefill_link(prompt)
        self.assertTrue(link.startswith("codex://threads/new?prompt="))
        tail = link.split("prompt=", 1)[1]
        for char in "&?#+= /\"\n":
            self.assertNotIn(char, tail)
        self.assertEqual(parse_qs(urlsplit(link).query)["prompt"], [prompt])

    def test_path_must_be_absolute_existing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            link = prefill_link("x", directory)
            self.assertEqual(parse_qs(urlsplit(link).query)["path"], [str(Path(directory).resolve())])
            for bad in ("relative/dir", directory + "/missing"):
                with self.assertRaises(AdapterError):
                    prefill_link("x", bad)
            file = Path(directory) / "f"
            file.write_text("")
            with self.assertRaises(AdapterError):
                prefill_link("x", str(file))

    def test_overlong_or_empty_prompt_refuses_with_clear_error(self):
        with self.assertRaisesRegex(AdapterError, "too long"):
            prefill_link("x" * MAX_LINK_BYTES)
        for bad in ("", "  ", "a\x00b", None):
            with self.assertRaises(AdapterError):
                prefill_link(bad)

    def test_prefill_mode_opens_link_and_never_claims_send(self):
        result = self.route(mode="prefill").deliver("hello")
        self.assertEqual(self.opened, [prefill_link("hello")])
        self.assertEqual(result["status"], "prefilled_awaiting_user_send")
        self.assertFalse(result["executed"])
        self.assertFalse(result["returned"])
        self.assertNotIn("sent", result["status"])

    def test_prefill_refusal_never_calls_opener(self):
        with self.assertRaises(AdapterError):
            self.route(mode="prefill").deliver("x" * MAX_LINK_BYTES)
        with self.assertRaises(AdapterError):
            self.route(mode="prefill", workspace="relative")
        self.assertEqual(self.opened, [])

    # auto send
    def test_auto_send_success_opens_thread_with_parsed_uuid(self):
        runner = FakeRunner()
        result = self.route(runner).deliver("hello")
        self.assertEqual(self.opened, [f"codex://threads/{SID}"])
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["session_id"], SID)
        self.assertEqual(result["reply_text"], "hello back")
        self.assertIn("not verified", result["delivery"])
        argv = runner.calls[-1]
        self.assertEqual(argv[1:3], ["exec", "--skip-git-repo-check"])
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")
        self.assertEqual(argv[-1], "hello")

    def test_uuid_from_json_event_or_rollout_file(self):
        result = self.route(FakeRunner(exec_result=(0, json.dumps({"type": "thread.started", "thread_id": SID}), ""))).deliver("hi")
        self.assertEqual(result["session_id"], SID)
        day = Path(self.sessions.name) / "2026" / "10" / "23"
        day.mkdir(parents=True)
        (day / f"rollout-2026-10-23T12-00-00-{SID}.jsonl").write_text("{}")
        result = self.route(FakeRunner(exec_result=(0, "", ""))).deliver("hi")
        self.assertEqual(result["session_id_source"], "rollout_file")

    def test_missing_uuid_reports_unknown_and_opens_nothing(self):
        result = self.route(FakeRunner(exec_result=(0, "", "no id here"))).deliver("hello")
        self.assertEqual(result["status"], "sent_session_unknown")
        self.assertFalse(result["app_thread_opened"])
        self.assertIsNone(result["link"])
        self.assertEqual(self.opened, [])

    def test_cli_failure_opens_nothing_and_never_falls_back(self):
        runner = FakeRunner(exec_result=(1, "", "error"))
        with self.assertRaisesRegex(AdapterError, "no fallback"):
            self.route(runner).deliver("hello")
        self.assertEqual(self.opened, [])
        self.assertEqual(sum(1 for c in runner.calls if "exec" in c), 1)

    def test_unknown_version_and_api_login_refuse_before_send(self):
        self.assertEqual(self.route(FakeRunner(version="codex-cli 0.148.0")).deliver("hi")["status"], "sent")
        self.opened.clear()
        for runner in (FakeRunner(version="codex-cli 0.147.0"), FakeRunner(login="Logged in using an API key")):
            with self.assertRaises(AdapterError):
                self.route(runner).deliver("hello")
            self.assertFalse(any("exec" in c for c in runner.calls))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture"}):
            with self.assertRaises(AdapterError):
                self.route().deliver("hello")
        self.assertEqual(self.opened, [])

    def test_sandbox_follows_shared_policy_and_refuses_rather_than_broadens(self):
        self.assertEqual(sandbox_for(policy())[0], "read-only")
        self.assertFalse(sandbox_for(policy())[1]["features.shell_tool"])
        self.assertEqual(sandbox_for(policy("deny", read_files="allow", write_files="allow"))[0], "workspace-write")
        with self.assertRaises(AdapterError):
            sandbox_for(policy("deny"))

    def test_option_like_prompt_and_bad_thread_id_refuse(self):
        with self.assertRaises(AdapterError):
            self.route().deliver("--yolo")
        with self.assertRaises(AdapterError):
            thread_link("../evil")


class DefaultSelection(unittest.TestCase):
    def test_auto_send_is_default_and_prefill_is_opt_in(self):
        self.assertEqual(CodexAppRoute(policy()).mode, "auto_send")

    def test_codex_app_is_default_in_examples_init_and_cli(self):
        self.assertEqual(PROVIDERS[0], DEFAULT_PROVIDER)
        example = load_configuration(ROOT / "examples/operators.example.json")
        self.assertEqual(next(iter(example.providers)), "codex_app")
        self.assertIs(example.permissions_for("codex_app"), example.policy)
        with tempfile.TemporaryDirectory() as temporary:
            configuration = load_configuration(initialize(temporary)["configuration"])
            self.assertEqual(configuration.providers["codex_app"]["mode"], "auto_send")
            route = route_for(configuration, opener=lambda url: self.fail("launched"))
            self.assertEqual(route.mode, "auto_send")
            self.assertIs(route.policy, configuration.policy)
            with self.assertRaises(AdapterError):
                route_for(configuration, "codex")  # CLI providers stay on `run`
            with patch("atlas_connectors.codex_app.system_opener", side_effect=AssertionError("launched")), \
                    patch("sys.stdout"):
                self.assertEqual(main(["send", "hi", "--preview", "--config",
                                       str(Path(temporary) / "connectors.json")]), 0)

    def test_codex_app_route_cannot_be_given_to_another_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(initialize(temporary)["configuration"])
            config = json.loads(path.read_text())
            config["providers"]["codex"]["transport"] = "codex_app"
            path.write_text(json.dumps(config))
            with self.assertRaises(Exception):
                load_configuration(path)


if __name__ == "__main__":
    unittest.main()
