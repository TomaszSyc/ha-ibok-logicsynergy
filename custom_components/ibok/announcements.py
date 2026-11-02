"""The operator's announcements, read from the portal's home page module.

No real answer has been seen yet (the portal returns null today), only the
template that shows it, so every step assumes as little as possible.
"""

from __future__ import annotations

import re
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
from typing import Any, Final

NEWS: Final = "news"
ANNOUNCEMENT: Final = "announcement"
# The portal's `type` column: 0 is its "News" tab, 1 its "Announcements".
_TYPES: Final = {0: NEWS, 1: ANNOUNCEMENT}

_BLOCK_ENDS = {
    "br",
    "p",
    "div",
    "li",
    "ul",
    "ol",
    "tr",
    *(f"h{level}" for level in range(1, 7)),
}
_SKIPPED = {"script", "style"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _SKIPPED:
            self._skipping += 1
        elif tag == "br":
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCK_ENDS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_to_text(value: Any) -> str:
    """The text of a piece of HTML, with line breaks where its blocks ended."""
    if value is None:
        return ""
    parser = _Text()
    parser.feed(str(value))
    parser.close()
    # A no-break space is whitespace to the reader, so it collapses like one.
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _kind(value: Any) -> str | None:
    # bool is an int subclass and a float would be truncated: neither is a type.
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        return None
    try:
        return _TYPES.get(int(value))
    except (TypeError, ValueError):
        return None


def _plain(value: Any) -> str:
    return "" if value is None else unescape(str(value)).strip()


def parse_announcements(data: Any) -> list[dict[str, str]]:
    """The portal's rows as ``{"type", "title", "short", "text"}``, in its order.

    A row that is not a dict, has no known type, or has neither a title nor a
    short text is left out.
    """
    if not isinstance(data, list):
        return []
    items = []
    for row in data:
        if not isinstance(row, dict):
            continue
        kind = _kind(row.get("type"))
        title, short = _plain(row.get("title")), _plain(row.get("short"))
        if kind is None or not (title or short):
            continue
        items.append(
            {
                "type": kind,
                "title": title,
                "short": short,
                "text": html_to_text(row.get("full")),
            }
        )
    return items


def announcement_key(item: dict[str, str]) -> str:
    """What makes an announcement the same one again: its title and short text.

    Only this digest is stored, not the text, which the portal owns.
    """
    return sha256(f"{item['title']}\x1f{item['short']}".encode()).hexdigest()[:16]
