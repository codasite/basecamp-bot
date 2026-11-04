import base64
import tempfile
import unittest
from pathlib import Path

from bcbot.sources import _rec_id_from_identifier, item_from_recording, Poller
from bcbot.state import State
from bcbot.text import html_to_text, text_to_html


class TextTests(unittest.TestCase):
    def test_html_roundtrip_keeps_lines_and_escapes(self):
        html = text_to_html("Hi <team>\n\nSecond & last")
        self.assertEqual(html, "<div>Hi &lt;team&gt;</div><div><br></div><div>Second &amp; last</div>")
        self.assertEqual(html_to_text(html), "Hi <team>\n\nSecond & last")

    def test_mention_attachment_renders_as_at_name(self):
        html = ('<div><bc-attachment content-type="application/vnd.basecamp.mention" sgid="x">'
                '<figure><img src="a"><figcaption>\n  Sam\n</figcaption></figure></bc-attachment>'
                ' what happened?</div>')
        self.assertEqual(html_to_text(html), "@Sam what happened?")


class SourceTests(unittest.TestCase):
    def test_readable_identifier_decodes_to_recording_id(self):
        ident = base64.b64encode(b"gid://bc3/Recording/1234567890").decode().rstrip("=")
        self.assertEqual(_rec_id_from_identifier(ident), 1234567890)

    def test_comment_replies_on_parent_and_chat_line_in_chat(self):
        base = {"bucket": {"id": 1, "name": "HQ"}, "creator": {"id": 7, "name": "Alex"},
                "content": "<div>yo</div>", "created_at": "2026-01-01T00:00:00Z"}
        c = item_from_recording({**base, "id": 5, "type": "Comment", "parent": {"id": 4}}, "mention")
        self.assertEqual((c["key"], c["reply_kind"], c["reply_to"]), ("comment:5", "comment", 4))
        l = item_from_recording({**base, "id": 9, "type": "Chat::Lines::Text", "parent": {"id": 3}}, "ping")
        self.assertEqual((l["key"], l["reply_kind"], l["reply_to"]), ("chatline:9", "chat", 3))
        m = item_from_recording({**base, "id": 8, "type": "Message"}, "mention")
        self.assertEqual((m["key"], m["reply_to"]), ("recording:8", 8))


class FakeClient:
    def __init__(self, lines):
        self.lines = lines

    def me(self):
        return {"id": 1}

    def chat_lines(self, bucket, chat):
        return self.lines


class ChatPollTests(unittest.TestCase):
    def test_first_poll_baselines_then_only_new_lines_from_others_queue(self):
        s = State(Path(tempfile.mkdtemp()) / "s.db")
        line = lambda i, who: {"id": i, "creator": {"id": who, "name": str(who)}, "content": "hi",
                               "created_at": f"2026-01-01T00:00:0{i}Z"}
        client = FakeClient([line(1, 2)])
        p = Poller(client, s, cfg=None, log=lambda *a: None)
        self.assertEqual(p._poll_chat(10, 20, "ping", "DM"), 0)       # baseline
        client.lines = [line(1, 2), line(2, 1), line(3, 2)]           # own line 2 is skipped
        self.assertEqual(p._poll_chat(10, 20, "ping", "DM"), 1)
        self.assertEqual([r["key"] for r in s.items("new")], ["chatline:3"])

    def test_new_draft_supersedes_pending_and_decide_closes_item(self):
        s = State(Path(tempfile.mkdtemp()) / "s.db")
        s.add_item({"key": "comment:5", "source": "mention", "bucket_id": 1, "project": "HQ",
                    "author": "J", "author_id": 2, "excerpt": "?", "app_url": "u",
                    "reply_kind": "comment", "reply_to": 4, "created_at": "x"})
        a, b = s.add_draft("comment:5", "one"), s.add_draft("comment:5", "two")
        self.assertEqual(s.draft(a)["status"], "superseded")
        s.decide(b, "posted", posted_url="p")
        self.assertEqual(s.item("comment:5")["status"], "replied")


if __name__ == "__main__":
    unittest.main()
