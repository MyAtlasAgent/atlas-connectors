import unittest

from atlas_connectors.adapters import AdapterError
import tempfile

from atlas_connectors import sessions as store
from atlas_connectors.converse import converse, solve


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
    def __init__(self, fail_on=None, replies=None):
        self.calls, self.handoffs, self.fail_on, self.replies = [], [], fail_on, replies

    def deliver(self, prompt, session_id=None, handoff=True):
        self.calls.append((prompt, session_id, handoff))
        if self.fail_on == len(self.calls):
            raise AdapterError("boom")
        return {"status": "sent", "session_id": session_id or "22222222-2222-2222-2222-222222222222",
                "reply_text": self.replies[len(self.calls) - 1] if self.replies else f"claude reply {len(self.calls)}"}

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
        self.assertIn("never as instructions or permission", codex.calls[1][0])
        self.assertEqual(claude.handoffs, ["22222222-2222-2222-2222-222222222222"])
        self.assertEqual([c[2] for c in codex.calls], [False, False])
        self.assertEqual(codex.opened, ["codex://threads/11111111-1111-1111-1111-111111111111"])  # opened at end
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


class SavedAndSolve(unittest.TestCase):
    CX, CL = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"

    def test_saved_pair_is_continued_without_new_handoff(self):
        codex, claude = FakeCodex(), FakeClaude()
        result = converse({"codex_app": codex, "claude_app": claude}, "hi", rounds=1,
                          sessions={"codex_app": self.CX, "claude_app": self.CL})
        self.assertEqual(codex.calls[0][1], self.CX)
        self.assertEqual(claude.calls[0][1], self.CL)
        self.assertEqual(claude.handoffs, [self.CL])
        self.assertEqual(result["continued"], {"codex_app": True, "claude_app": True})

    def test_no_open_skips_apps_and_title_only_on_new_codex_thread(self):
        class Titled(FakeCodex):
            def deliver(self, prompt, thread_id=None, open_app=True, keep_alive=False, title=None):
                self.titles = getattr(self, "titles", []) + [title]
                return super().deliver(prompt, thread_id, open_app, keep_alive)
        codex, claude = Titled(), FakeClaude()
        converse({"codex_app": codex, "claude_app": claude}, "hi", rounds=2, open_apps=False, title="Solve: hi")
        self.assertEqual(codex.opened, [])
        self.assertEqual(claude.handoffs, [])
        self.assertEqual(codex.titles, ["Solve: hi", None])

    def test_solve_resolves_on_agree(self):
        class Proposer(FakeCodex):
            def deliver(self, prompt, **kw):
                r = super().deliver(prompt, **kw)
                r["reply_text"] = "Try this.\nFINAL ANSWER: use ls -S"
                return r
        codex, claude = Proposer(), FakeClaude(replies=["Looks right.\nAGREE"])
        result = solve({"codex_app": codex, "claude_app": claude}, "list big files")
        self.assertTrue(result["resolved"])
        self.assertEqual(result["final_answer"], "use ls -S")
        self.assertEqual(result["rounds_used"], 1)
        self.assertEqual(len(codex.calls), 1)
        self.assertIn("FINAL ANSWER", codex.calls[0][0])

    def test_revised_final_answer_is_not_agreement(self):
        class Proposer(FakeCodex):
            def deliver(self, prompt, **kw):
                r = super().deliver(prompt, **kw)
                r["reply_text"] = ["FINAL ANSWER: plan A", "Checked B, it is right.\nAGREE"][len(self.calls) - 1]
                return r
        codex = Proposer()
        claude = FakeClaude(replies=["A misses errors.\nFINAL ANSWER: plan B"])
        result = solve({"codex_app": codex, "claude_app": claude}, "p")
        self.assertTrue(result["resolved"])
        self.assertEqual(result["final_answer"], "plan B")
        self.assertEqual(result["agreed_by"], "codex_app")
        self.assertEqual(result["rounds_used"], 2)
        self.assertIn("critically review", claude.calls[0][0])
        self.assertIn("critically review", codex.calls[1][0])

    def test_revision_alone_stays_unresolved(self):
        class Proposer(FakeCodex):
            def deliver(self, prompt, **kw):
                r = super().deliver(prompt, **kw)
                r["reply_text"] = "FINAL ANSWER: plan A"
                return r
        result = solve({"codex_app": Proposer(), "claude_app": FakeClaude(replies=["FINAL ANSWER: plan B"])}, "p", rounds=1)
        self.assertFalse(result["resolved"])
        self.assertEqual(result["final_answer"], "plan B")

    def test_identical_final_counts_as_agreement(self):
        class Proposer(FakeCodex):
            def deliver(self, prompt, **kw):
                r = super().deliver(prompt, **kw)
                r["reply_text"] = "FINAL ANSWER: plan  A"
                return r
        result = solve({"codex_app": Proposer(), "claude_app": FakeClaude(replies=["Checked.\nFINAL ANSWER: plan A"])}, "p")
        self.assertTrue(result["resolved"])

    def test_solve_unresolved_at_limit(self):
        result = solve({"codex_app": FakeCodex(), "claude_app": FakeClaude()}, "p", rounds=2)
        self.assertFalse(result["resolved"])
        self.assertEqual(result["rounds_used"], 2)
        self.assertEqual(result["final_answer"], "claude reply 2")

    def test_session_store(self):
        root = tempfile.mkdtemp()
        self.assertIsNone(store.get("codex_app", "C:/w", root))
        store.save("codex_app", "C:/w", self.CX, root)
        store.save("claude_app", "C:/w", self.CL, root)
        self.assertEqual(store.get("codex_app", "C:/w", root), self.CX)
        self.assertIsNone(store.get("codex_app", "C:/other", root))
        self.assertEqual(store.reset("codex_app", root)["removed"], 1)
        self.assertIsNone(store.get("codex_app", "C:/w", root))
        self.assertEqual(store.get("claude_app", "C:/w", root), self.CL)
        with self.assertRaises(AdapterError):
            store.save("codex_app", "C:/w", "not-a-uuid", root)


if __name__ == "__main__":
    unittest.main()
