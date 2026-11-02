"""The operator's announcements are read defensively: the portal's data is unseen."""

from __future__ import annotations

import pytest

from custom_components.ibok.announcements import (
    ANNOUNCEMENT,
    NEWS,
    announcement_key,
    html_to_text,
    parse_announcements,
)


@pytest.mark.parametrize("data", [None, "", {}, 42, "tekst"])
def test_anything_but_a_list_is_no_announcements(data) -> None:
    assert parse_announcements(data) == []


def test_a_row_becomes_an_item_and_unknown_fields_are_ignored() -> None:
    rows = [{"type": 1, "title": "T", "short": "S", "full": "F", "id": 7, "x": [1]}]
    assert parse_announcements(rows) == [
        {"type": ANNOUNCEMENT, "title": "T", "short": "S", "text": "F"}
    ]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [(0, NEWS), ("0", NEWS), (1, ANNOUNCEMENT), ("1", ANNOUNCEMENT)],
)
def test_the_type_is_an_int_or_a_numeric_string(kind, expected) -> None:
    assert parse_announcements([{"type": kind, "title": "T"}])[0]["type"] == expected


@pytest.mark.parametrize("kind", [2, "2", "x", None, True, 0.5])
def test_an_unknown_or_missing_type_is_skipped(kind) -> None:
    assert parse_announcements([{"type": kind, "title": "T"}]) == []
    assert parse_announcements([{"title": "T"}]) == []


def test_a_row_with_neither_title_nor_short_is_skipped() -> None:
    rows = [
        {"type": 0},
        {"type": 0, "title": None, "short": None},
        {"type": 0, "title": " ", "short": ""},
        {"type": 0, "short": "S"},
    ]
    assert parse_announcements(rows) == [
        {"type": NEWS, "title": "", "short": "S", "text": ""}
    ]


def test_rows_that_are_not_dicts_are_skipped_and_the_order_kept() -> None:
    rows = [
        "x",
        None,
        {"type": 0, "title": "A"},
        7,
        {"type": 1, "title": "B"},
    ]
    assert [i["title"] for i in parse_announcements(rows)] == ["A", "B"]


def test_the_full_text_loses_its_html() -> None:
    full = "<p>Przerwa w dostawie&nbsp;wody</p><p>Od 8:00<br>do 12:00</p><script>x()</script>"
    item = parse_announcements([{"type": 1, "title": "T", "full": full}])[0]
    assert item["text"] == "Przerwa w dostawie wody\nOd 8:00\ndo 12:00"


def test_a_missing_full_text_is_empty() -> None:
    assert parse_announcements([{"type": 1, "title": "T"}])[0]["text"] == ""


def test_html_to_text_handles_blocks_entities_and_whitespace() -> None:
    value = (
        "<style>p{}</style><div>a   b</div>\n\n\n<ul><li>c &amp; d</li><li>e</li></ul>"
    )
    assert html_to_text(value) == "a b\n\nc & d\ne"
    assert html_to_text("<h3>Przerwa</h3>Od 8:00") == "Przerwa\nOd 8:00"
    assert html_to_text(None) == ""
    assert html_to_text(5) == "5"


def test_the_key_is_stable_and_follows_title_and_short() -> None:
    item = {"type": NEWS, "title": "T", "short": "S", "text": "F"}
    key = announcement_key(item)
    assert key == announcement_key({**item, "text": "inny", "type": ANNOUNCEMENT})
    assert len(key) == 16
    assert key != announcement_key({**item, "short": "S2"})
    # The separator keeps the two fields apart.
    assert announcement_key({"title": "ab", "short": "c"}) != announcement_key(
        {"title": "a", "short": "bc"}
    )
