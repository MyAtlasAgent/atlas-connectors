"""codex_app default route: app-server protocol, prefill opt-in, no launches."""
import json
import os
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from atlas_connectors.adapters import AdapterError
from atlas_connectors.cli import initialize, main, route_for
from atlas_connectors.codex_app import (
    CLIENT_INFO, DEFAULT_PROVIDER, MAX_LINK_BYTES, CodexAppRoute, prefill_link, require_workspace,
    sandbox_for, thread_link)
from atlas_connectors.permissions import PROVIDERS, PermissionPolicy, load_configuration

ROOT = Path(__file__).resolve().parents[1]
TID = "01a169f1-5263-7580-8f28-cd814fbdb775"


def policy(default="ask", **capabilities):
    return PermissionPolicy.from_dict({"version": 1, "default": default, "capabilities": capabilities})


def project_dir(test):
    """A real (non-temp) directory, as auto_send requires."""
    directory = tempfile.TemporaryDirectory(dir=ROOT, prefix=".test-workspace-")
    test.addCleanup(directory.cleanup)
    return directory.name


class FakeRunner:
    def __init__(self, version="codex-cli 0.148.0", login="Logged in using ChatGPT"):
        self.calls, self.version, self.login = [], version, login

    def __call__(self, argv, *, cwd, env, stdin="", timeout=120):
        self.calls.append(list(argv))
        if argv[1:] == ["--version"]:
            return 0, self.version, ""
        if argv[1:3] == ["login", "status"]:
            return 0, self.login, ""
        raise AssertionError("codex exec must never run")


class FakeAppServer:
    """Scripted stdio JSON-RPC server; records every client message."""

    def __init__(self, reply="Hi — Codex here", complete=True, fail=None, thread_id=TID, turn_status="completed"):
        self.sent, self.out = [], queue.Queue()
        self.reply, self.complete, self.fail, self.thread_id, self.turn_status = reply, complete, fail, thread_id, turn_status
        self.terminated, self.argv, self.cwd = False, None, None
        server = self

        class Stdin:
            def write(self, data):
                server.receive(json.loads(data.decode("utf-8")))

            def flush(self):
                pass

            def close(self):
                pass

        class Stdout:
            def readline(self):
                return server.out.get()

        self.stdin, self.stdout = Stdin(), Stdout()

    def emit(self, message):
        self.out.put((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))

    def receive(self, message):
        self.sent.append(message)
        method, ident = message.get("method"), message.get("id")
        if method == self.fail:
            self.emit({"id": ident, "error": {"code": 1, "message": "boom"}})
        elif method == "initialize":
            self.emit({"id": ident, "result": {"userAgent": "fake"}})
        elif method == "thread/start":
            self.emit({"id": ident, "result": {"thread": {"id": self.thread_id}}})
        elif method == "turn/start":
            self.emit({"id": ident, "result": {"turn": {"id": "t1", "status": "inProgress"}}})
            self.emit({"id": 99, "method": "item/commandExecution/requestApproval", "params": {}})
            self.emit({"method": "item/agentMessage/delta", "params": {"delta": "Hi "}})
            if self.complete:
                self.emit({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": self.reply}}})
                self.emit({"method": "turn/completed", "params": {"turn": {"status": self.turn_status}}})

    def __call__(self, argv, *, cwd, env):
        self.argv, self.cwd = argv, cwd
        return self

    def terminate(self):
        self.terminated = True
        self.out.put(b"")

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class Route(unittest.TestCase):
    def setUp(self):
        self.opened = []
        self.workspace = project_dir(self)
        env = patch.dict(os.environ, {k: "" for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL")})
        env.start()
        self.addCleanup(env.stop)

    def route(self, server=None, runner=None, **kwargs):
        kwargs.setdefault("policy", policy())
        kwargs.setdefault("workspace", self.workspace)
        return CodexAppRoute(kwargs.pop("policy"), runner=runner or FakeRunner(), opener=self.opened.append,
                             spawner=server or FakeAppServer(), which=lambda name: "/usr/bin/true", **kwargs)

    # prefill (opt-in)
    def test_prefill_link_encodes_every_reserved_character(self):
        prompt = "Hi & bye? #1 a+b=c / 100% \"q\" é\nnext"
        link = prefill_link(prompt)
        tail = link.split("prompt=", 1)[1]
        for char in "&?#+= /\"\n":
            self.assertNotIn(char, tail)
        self.assertEqual(parse_qs(urlsplit(link).query)["prompt"], [prompt])

    def test_path_must_be_absolute_existing_directory(self):
        link = prefill_link("x", self.workspace)
        self.assertEqual(parse_qs(urlsplit(link).query)["path"], [str(Path(self.workspace).resolve())])
        for bad in ("relative/dir", self.workspace + "/missing"):
            with self.assertRaises(AdapterError):
                prefill_link("x", bad)

    def test_overlong_or_empty_prompt_refuses(self):
        with self.assertRaisesRegex(AdapterError, "too long"):
            prefill_link("x" * MAX_LINK_BYTES)
        for bad in ("", "  ", "a\x00b", None):
            with self.assertRaises(AdapterError):
                prefill_link(bad)

    def test_prefill_is_opt_in_and_never_claims_send(self):
        self.assertEqual(CodexAppRoute(policy()).mode, "auto_send")
        result = self.route(mode="prefill").deliver("hello")
        self.assertEqual(result["status"], "prefilled_awaiting_user_send")
        self.assertFalse(result["executed"])
        self.assertEqual(len(self.opened), 1)

    # auto send via app-server
    def test_protocol_sequence_and_thread_link(self):
        server = FakeAppServer()
        result = self.route(server).deliver("--hello é")
        self.assertEqual(server.argv[1:], ["app-server"])
        self.assertEqual(server.cwd, str(Path(self.workspace).resolve()))
        methods = [m.get("method") for m in server.sent if "method" in m]
        self.assertEqual(methods, ["initialize", "initialized", "thread/start", "turn/start"])
        self.assertEqual(server.sent[0], {"id": 0, "method": "initialize", "params": {"clientInfo": CLIENT_INFO}})
        self.assertEqual(server.sent[2]["params"], {"cwd": str(Path(self.workspace).resolve()),
                                                    "sandbox": "read-only", "approvalPolicy": "never"})
        turn = next(m for m in server.sent if m.get("method") == "turn/start")
        self.assertEqual(turn["params"], {"threadId": TID, "input": [{"type": "text", "text": "--hello é"}]})
        refusal = next(m for m in server.sent if m.get("id") == 99)
        self.assertIn("error", refusal)  # server approval requests are never auto-approved
        self.assertTrue(server.terminated)
        self.assertEqual(self.opened, [f"codex://threads/{TID}"])
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["thread_id"], TID)

    def test_keep_alive_continues_same_thread_in_one_server(self):
        server = FakeAppServer()
        route = self.route(server)
        first = route.deliver("one", open_app=False, keep_alive=True)
        self.assertFalse(server.terminated)
        second = route.deliver("two", thread_id=first["thread_id"], open_app=False, keep_alive=True)
        route.close()
        self.assertTrue(server.terminated)
        methods = [m.get("method") for m in server.sent if "method" in m]
        self.assertEqual(methods, ["initialize", "initialized", "thread/start", "turn/start", "turn/start"])
        self.assertEqual(second["thread_id"], TID)
        self.assertEqual(self.opened, [])

    def test_active_writer_lock_is_a_clear_error_not_a_new_thread(self):
        class Locked(FakeAppServer):
            def receive(self, message):
                if message.get("method") == "thread/resume":
                    self.sent.append(message)
                    self.emit({"id": message["id"], "error": {"code": -32600, "message": "thread x already has an active writer"}})
                else:
                    super().receive(message)
        server = Locked()
        with self.assertRaises(AdapterError) as caught:
            self.route(server).deliver("hi", thread_id=TID)
        self.assertIn("--new-session", str(caught.exception))
        self.assertNotIn("thread/start", [m.get("method") for m in server.sent])
        self.assertEqual(self.opened, [])

    def test_reply_is_decoded_as_utf8_including_em_dash(self):
        result = self.route(FakeAppServer(reply="Hi — Codex here ✓")).deliver("hello")
        self.assertEqual(result["reply_text"], "Hi — Codex here ✓")
        self.assertNotIn("â€", result["reply_text"])

    def test_timeout_after_accepted_turn_reports_unknown_completion(self):
        server = FakeAppServer(complete=False)
        result = self.route(server, timeout=0.3).deliver("hello")
        self.assertEqual(result["status"], "sent_completion_unknown")
        self.assertEqual(result["completion"], "timeout")
        self.assertTrue(server.terminated)

    def test_protocol_failure_opens_nothing_and_never_falls_back(self):
        for kwargs in ({"fail": "initialize"}, {"fail": "thread/start"}, {"fail": "turn/start"},
                       {"thread_id": "not-a-uuid"}, {"turn_status": "failed"}):
            runner, server = FakeRunner(), FakeAppServer(**kwargs)
            with self.subTest(**kwargs), self.assertRaisesRegex(AdapterError, "no fallback"):
                self.route(server, runner).deliver("hello")
            self.assertTrue(server.terminated)
            self.assertFalse(any("exec" in c for c in runner.calls))
        self.assertEqual(self.opened, [])

    def test_preflight_refusals_spawn_nothing(self):
        for runner in (FakeRunner(version="codex-cli 0.147.0"), FakeRunner(login="Logged in using an API key")):
            server = FakeAppServer()
            with self.assertRaises(AdapterError):
                self.route(server, runner).deliver("hello")
            self.assertIsNone(server.argv)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture"}), self.assertRaises(AdapterError):
            self.route().deliver("hello")
        self.assertEqual(self.opened, [])

    def test_sandbox_follows_shared_policy(self):
        self.assertEqual(sandbox_for(policy()), "read-only")
        self.assertEqual(sandbox_for(policy("deny", read_files="allow", write_files="allow")), "workspace-write")
        with self.assertRaises(AdapterError):
            sandbox_for(policy("deny"))

    def test_bad_thread_id_refuses(self):
        with self.assertRaises(AdapterError):
            thread_link("../evil")


class Workspace(unittest.TestCase):
    def test_temp_dir_and_invalid_paths_refused_with_guidance(self):
        with tempfile.TemporaryDirectory() as temporary:
            for bad in (temporary, tempfile.gettempdir(), "relative", "/definitely/missing"):
                with self.assertRaisesRegex(AdapterError, "--workspace"):
                    require_workspace(bad)

    def test_resolution_order_flag_then_config_then_cwd(self):
        flag, config_dir, cwd = project_dir(self), project_dir(self), project_dir(self)
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".test-config-") as cfg:
            configuration = load_configuration(initialize(cfg, workspace=config_dir)["configuration"])
            self.assertEqual(route_for(configuration, workspace=flag).workspace, str(Path(flag).resolve()))
            self.assertEqual(route_for(configuration).workspace, str(Path(config_dir).resolve()))
            self.assertEqual(route_for(configuration, "claude_app").workspace, str(Path(config_dir).resolve()))
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".test-config-") as cfg:
            configuration = load_configuration(initialize(cfg)["configuration"])
            self.assertNotIn("workspace", configuration.providers["codex_app"])
            route = route_for(configuration)
            self.assertIsNone(route.workspace)
            previous = os.getcwd()
            os.chdir(cwd)
            try:
                self.assertEqual(require_workspace(route.workspace), str(Path(cwd).resolve()))
            finally:
                os.chdir(previous)

    def test_init_workspace_must_be_absolute_existing(self):
        with tempfile.TemporaryDirectory() as cfg:
            with self.assertRaises(ValueError):
                initialize(cfg, workspace="relative/dir")


class DefaultSelection(unittest.TestCase):
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
                route_for(configuration, "codex")
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
