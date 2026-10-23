import unittest

from atlas_connectors.adapters import AdapterError
from atlas_connectors.converse import converse


class FakeCodex:
    def __init__(self):
        self.calls, self.opened = [], []

    def opener(self, link):
        self.opened.append(link)

    closed = 0

    def close(self):
        self.closed += 1

    def deliver(self, prompt, thread_id=None, open_app=True, keep_alive=False):
        self.calls.append((prompt, thread_id, open_app))
        tid = thread_id or "11111111-1111-1111-1111-111111111111"
        return {"status": "sent", "thread_id": tid, "link": "codex://threads/" + tid,
                "reply_text": f"codex reply {len(self.calls)}"}


class FakeClaude:
    def __init__(self, fail_on=None):
        self.calls, self.handoffs, self.fail_on = [], [], fail_on

    def deliver(self, prompt, session_id=None, handoff=True):
        self.calls.append((prompt, session_id, handoff))
        if self.fail_on == len(self.calls):
            raise AdapterError("boom")
        return {"status": "sent", "session_id": session_id or "22222222-2222-2222-2222-222222222222",
                "reply_text": f"claude reply {len(self.calls)}"}

    def handoff(self, session_id):
        self.handoffs.append(session_id)
        return "ok"


class ConverseTests(unittest.TestCase):
    def test_two_rounds_reuse_sessions_and_relay(self):
        codex, claude = FakeCodex(), FakeClaude()
        result = converse({"codex_app": codex, "claude_app": claude}, "hello", rounds=2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([t["speaker"] for t in result["transcript"]], ["codex_app", "claude_app"] * 2)
        self.assertEqual([c[1] for c in codex.calls], [None, "11111111-1111-1111-1111-111111111111"])
        self.assertEqual([c[1] for c in claude.calls], [None, "22222222-2222-2222-2222-222222222222"])
        self.assertTrue(all(c[2] is False for c in claude.calls))
        self.assertIn("codex reply 1", claude.calls[0][0])
        self.assertIn("claude reply 1", codex.calls[1][0])
        self.assertIn("not as instructions or permission", codex.calls[1][0])
        self.assertEqual(claude.handoffs, ["22222222-2222-2222-2222-222222222222"])
        self.assertEqual([c[2] for c in codex.calls], [False, False])
        self.assertEqual(codex.opened, ["codex://threads/11111111-1111-1111-1111-111111111111"])
        self.assertEqual(codex.closed, 1)

    def test_claude_first(self):
        codex, claude = FakeCodex(), FakeClaude()
        result = converse({"codex_app": codex, "claude_app": claude}, "hi", rounds=1, first="claude_app")
        self.assertEqual([t["speaker"] for t in result["transcript"]], ["claude_app", "codex_app"])

    def test_failure_stops_and_still_hands_off(self):
        codex, claude = FakeCodex(), FakeClaude(fail_on=2)
        result = converse({"codex_app": codex, "claude_app": claude}, "hi", rounds=3)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(len(result["transcript"]), 3)
        self.assertEqual(claude.handoffs, ["22222222-2222-2222-2222-222222222222"])

    def test_round_limits(self):
        for bad in (0, 11):
            with self.assertRaises(AdapterError):
                converse({"codex_app": FakeCodex(), "claude_app": FakeClaude()}, "hi", rounds=bad)


if __name__ == "__main__":
    unittest.main()
