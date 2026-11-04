import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bcbot import approvals
from bcbot.config import Approval
from bcbot.llm import DraftError, _parse
from bcbot.state import State


class ParseTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(approvals.parse("send 12"), approvals.Command("send", 12))
        self.assertEqual(approvals.parse("OK #3"), approvals.Command("send", 3))
        self.assertEqual(approvals.parse("skip"), approvals.Command("skip", None))
        self.assertEqual(approvals.parse("edit 4: Thanks!\nOn it."), approvals.Command("edit", 4, "Thanks!\nOn it."))

    def test_conversation_is_not_a_command(self):
        for line in ("ok thanks for the heads up", "edit 4", "sounds good", "no idea, ask Sam"):
            self.assertIsNone(approvals.parse(line), line)


class FakeClient:
    def __init__(self):
        self.lines, self.posted = [], []

    def chat_lines(self, bucket, chat):
        return self.lines

    def post_chat_line(self, bucket, chat, text):
        self.posted.append(text)
        return {"id": 999}


def _state_with_draft():
    s = State(Path(tempfile.mkdtemp()) / "s.db")
    s.add_item({"key": "comment:5", "source": "mention", "bucket_id": 1, "project": "HQ", "author": "J",
                "author_id": 2, "excerpt": "?", "app_url": "u", "reply_kind": "comment", "reply_to": 4,
                "created_at": "x"})
    return s, s.add_draft("comment:5", "draft text")


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(approval=Approval(chat="https://3.basecamp.com/1/buckets/7/chats/8",
                                                     approvers=[42]))
        self.s, self.draft = _state_with_draft()
        self.c = FakeClient()
        self.posts = []
        self.post = lambda i, body, approver=None: self.posts.append((i, body, approver)) or "url"

    def _tick(self, *lines):
        self.c.lines = [{"id": 1, "creator": {"id": 1}, "content": "old"}] + list(lines)
        return approvals.process(self.c, self.s, self.cfg, self.post, log=lambda *a: None)

    def test_first_contact_baselines_without_acting(self):
        self.c.lines = [{"id": 1, "creator": {"id": 42}, "content": f"send {self.draft}"}]
        self.assertEqual(approvals.process(self.c, self.s, self.cfg, self.post), 0)
        self.assertEqual(self.posts, [])

    def test_only_listed_approvers_can_release_a_post(self):
        self._tick()  # baseline
        self._tick({"id": 2, "creator": {"id": 77, "name": "Rando"}, "content": f"send {self.draft}"})
        self.assertEqual(self.posts, [])
        self._tick({"id": 3, "creator": {"id": 42, "name": "Boss"}, "content": "<div>edit: Better text</div>"})
        self.assertEqual(self.posts, [(self.draft, "Better text", "Boss")])

    def test_skip_rejects(self):
        self._tick()
        self._tick({"id": 2, "creator": {"id": 42, "name": "Boss"}, "content": f"skip {self.draft}"})
        self.assertEqual(self.s.draft(self.draft)["status"], "rejected")


class LLMParseTests(unittest.TestCase):
    def test_reply_and_ignore(self):
        self.assertEqual(_parse('{"action":"reply","body":"Hi"}').body, "Hi")
        self.assertEqual(_parse('Sure:\n{"action": "ignore", "reason": "FYI"}').action, "ignore")
        with self.assertRaises(DraftError):
            _parse("I think you should reply")


class ConfigTests(unittest.TestCase):
    def test_example_config_loads(self):
        from bcbot.config import load
        cfg = load(Path(__file__).parent.parent / "config.example.toml")
        self.assertEqual(cfg.llm.provider, "")
        self.assertIsNone(cfg.approval.bucket_chat)
