"""Convert between Basecamp rich-text HTML and plain text."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK = {"div", "p", "br", "li", "blockquote", "h1", "h2", "h3", "pre", "tr"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self._mention: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if self._mention is not None:
            return
        if tag == "bc-attachment":
            a = dict(attrs)
            # @mentions are Person attachments wrapping an avatar + name; render "@Name".
            if a.get("content-type") == "application/vnd.basecamp.mention":
                self._mention = []
            elif a.get("filename") or a.get("caption"):
                self.out.append(f"[attachment: {a.get('caption') or a.get('filename')}]")
            return
        if tag in _BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag == "bc-attachment" and self._mention is not None:
            self.out.append("@" + " ".join("".join(self._mention).split()))
            self._mention = None

    def handle_data(self, data):
        (self._mention if self._mention is not None else self.out).append(data)


def html_to_text(content: str | None) -> str:
    if not content:
        return ""
    p = _Text()
    p.feed(content)
    text = "".join(p.out).replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", text)).strip()


def text_to_html(text: str) -> str:
    """Plain text -> Basecamp rich text: one <div> per line, blank lines become <br>."""
    lines = text.strip().split("\n")
    return "".join(f"<div>{html.escape(line)}</div>" if line.strip() else "<div><br></div>" for line in lines)
