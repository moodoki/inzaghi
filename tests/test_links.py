"""Which links a click may open, what hovering shows, and what ``y`` lists."""

from __future__ import annotations

import pytest
from rich.style import Style

from inzaghi.ui import links


@pytest.mark.parametrize(
    "href",
    ["https://example.com", "http://example.com/a?b=c", "HTTPS://Example.com/x"],
)
def test_a_web_address_may_be_opened(href):
    assert links.is_web(href)


@pytest.mark.parametrize(
    "href",
    [
        "attachments/report.pdf",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "vscode://open?file=/etc/passwd",
        "mailto:someone@example.com",
        "https:/no-host",
        "//example.com/protocol-relative",
        "http://[::1",  # urlsplit raises on this one
        "",
    ],
)
def test_anything_else_may_not(href):
    assert not links.is_web(href)


def test_hover_reads_the_address_the_widget_drew_the_link_with():
    """Textual puts ``link('<href>')`` behind a link's text, quotes and all."""
    style = Style.from_meta({"@click": "link('https://example.com/it\\'s')"})
    assert links.href_at(style) == "https://example.com/it's"


@pytest.mark.parametrize(
    "style",
    [None, Style(), Style.from_meta({"@click": "app.quit()"}), Style.from_meta({"@click": "link(1)"})],
)
def test_hover_over_anything_else_shows_nothing(style):
    assert links.href_at(style) is None


def test_the_copy_list_has_written_and_bare_links_once_each_and_none_quoted():
    text = (
        "See [the report](https://example.com/r) and https://example.com/r again.\n"
        "Bare: https://example.org/x.\n"
        "File: [notes](attachments/notes.md)\n"
        "```\nhttps://quoted.example/in-a-fence\n```\n"
        "Inline `https://quoted.example/span` too.\n"
    )
    found = links.document_links(text)
    assert [link.target for link in found] == [
        "https://example.com/r",
        "attachments/notes.md",
        "https://example.org/x",
    ]
    assert found[0].label == "the report"
