"""Text out of a channel, on its way into a markup string.

A row label is markup; a title is whatever the session wrote. The titles
genuinely look like ``[milestone] Shard 2 reindexed``, so the join between the
two is where a channel gets to decide how the reader renders -- which it does
not get to do.
"""

from __future__ import annotations

import pytest
from textual.content import Content
from textual.widgets import OptionList, TabbedContent

from conftest import write
from inzaghi.ui.channel_view import ChannelPane
from inzaghi.ui.markup import escape
from test_ui import make_app, settle

AWKWARD = [
    "[milestone] Shard 2 reindexed",
    "a [/] in the middle",
    "[dim]not a style[/]",
    "[[doubled]]",
    "escaped bracket \\[b] already",
    "a lone \\ backslash",
    "backslash then bracket \\[",
    "$variable and $$doubled",
    ":not_an_emoji: either",
]


@pytest.mark.parametrize("text", AWKWARD)
def test_escaped_text_renders_as_itself(text):
    assert Content.from_markup(escape(text)).plain == text


@pytest.mark.parametrize("text", AWKWARD)
def test_escaped_text_carries_no_styling(text):
    """Nothing a channel writes may light up, however it is spelled."""
    assert Content.from_markup(escape(text)).spans == []


def test_a_backslash_at_the_end_costs_the_tag_after_it():
    """The one thing this escaping cannot do, written down so nobody assumes it.

    Textual drops a backslash standing in front of a tag and renders the tag
    as text. Doubling it -- Rich's answer, to Rich's parser -- loses the tag
    here just the same, and doubles the character everywhere no tag follows.
    So the rare case pays: a styled row whose text ends in a backslash runs
    its colour to the end of the line. One row renders oddly, which is where
    this kind of thing is allowed to end.
    """
    assert Content.from_markup(escape("ends with a backslash \\")).plain == (
        "ends with a backslash \\"
    )
    abutted = Content.from_markup(f"[b]{escape('ends with a backslash ')}\\[/]")
    assert abutted.plain == "ends with a backslash [/]"


def test_escaping_leaves_the_markup_around_it_working():
    content = Content.from_markup(f"[dim]{escape('[milestone] done')}[/]")
    assert content.plain == "[milestone] done"
    assert [span.style for span in content.spans] == ["dim"]


async def test_a_title_in_brackets_reaches_the_timeline_intact(channel_root):
    """End to end: what the session wrote is what the row says."""
    write(
        channel_root / "notifications" / "2026-09-05_0300_note_odd.md",
        "# [note] A title with [brackets] and a [dim]style[/] in it\n\nBody.\n",
    )
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        pane = app.query_one(ChannelPane)
        timeline = pane.query_one("#timeline", OptionList)
        lines = [
            timeline.get_option_at_index(index).prompt.plain
            for index in range(timeline.option_count)
        ]
        assert any("A title with [brackets] and a [dim]style[/] in it" in line for line in lines)
