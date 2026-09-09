"""Finding a word inside the document on screen.

Two questions that look alike and are not: the channel search asks *which
entries mention this*, and this asks *where in this one does it say that*.
They share a key -- `/` -- and which one you get depends on where the keyboard
is, so most of what is worth testing here is that boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import OptionList, Static, TabbedContent

from conftest import write

from inzaghi.ui import find
from inzaghi.ui.channel_view import ChannelPane
from test_ui import make_app, settle

# -- the matching itself --------------------------------------------------


def test_matches_come_back_in_order_with_their_line():
    text = "first line\nsecond has shard 3\nthird has shard 4\n"
    found = find.find_all(text, "shard")
    assert [(m.line, text[m.start : m.end]) for m in found] == [(1, "shard"), (2, "shard")]


def test_matching_ignores_case_without_losing_the_offsets():
    """Against the original string, not a lowercased copy: casefolding is not
    length-preserving for every character, and a shifted offset underlines the
    wrong words."""
    text = "İstanbul: SHARD three, shard four"
    found = find.find_all(text, "shard")
    assert [text[m.start : m.end] for m in found] == ["SHARD", "shard"]
    assert len(text.lower()) != len(text), "the case that makes this worth testing"


def test_nothing_to_find_is_no_matches():
    assert find.find_all("anything", "") == []
    assert find.find_all("", "shard") == []
    assert find.find_all("prose", "shard") == []


def test_a_query_is_taken_literally_not_as_a_pattern():
    """The box is one row with nowhere to report an unbalanced bracket, and a
    session's prose is full of brackets."""
    assert len(find.find_all("a [milestone] line", "[milestone]")) == 1
    assert find.find_all("a.b", "a.b") != []
    assert find.find_all("axb", "a.b") == []


def test_stepping_wraps_the_way_n_does():
    matches = find.find_all("a a a", "a")
    assert find.next_index(matches, None, 1) == 0
    assert find.next_index(matches, None, -1) == 2
    assert find.next_index(matches, 2, 1) == 0
    assert find.next_index(matches, 0, -1) == 2
    assert find.next_index([], None, 1) is None


def test_a_wrapped_row_counts_the_lines_above_it():
    text = "short\n" + "x" * 30 + "\nafter\n"
    assert find.wrapped_row(text, 0, 10) == 0
    assert find.wrapped_row(text, 1, 10) == 1
    assert find.wrapped_row(text, 2, 10) == 4  # the long line took three rows
    assert find.wrapped_row(text, 2, 0) == 0  # no width yet, no guess


# -- in the app -----------------------------------------------------------

ENTRY = "notifications/2026-09-05_0530_milestone_sweep-closed.md"


@pytest.fixture
def reading(channel_root: Path) -> Path:
    """A channel whose newest entry is long, repetitive, and delivers files."""
    folder = channel_root / "notifications" / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "notes.md").write_text("# Notes\n\nShard 3 was reindexed twice.\n")
    (folder / "tail.txt").write_text(
        "".join(f"worker-2 line {i}: shard {i % 5} ok\n" for i in range(40))
    )
    write(
        channel_root / ENTRY,
        "# [milestone] Sweep closed\n\n"
        + "".join(f"Paragraph {i}: routine prose.\n\n" for i in range(12))
        + "The checksum mismatch was on shard 3.\n\n"
        + "".join(f"Paragraph {i}: more prose.\n\n" for i in range(12, 20))
        + "Notes: [what shard 3 did](attachments/notes.md).\n"
        + "Tail: [the last lines](attachments/tail.txt).\n",
    )
    return channel_root


async def open_reader(app, pilot) -> ChannelPane:
    """Select the long entry and put the keyboard in the reader."""
    await settle(app, pilot)
    app.query_one("#tabs", TabbedContent).active = "ch0"
    await pilot.pause()
    timeline = app.screen.query_one("#timeline", OptionList)
    timeline.highlighted = timeline.get_option_index(str(Path(app.channels[0].root) / ENTRY))
    await pilot.pause()
    await pilot.press("ctrl+w", "l")
    await pilot.pause()
    return app.query_one(ChannelPane)


def count(app) -> str:
    return str(app.screen.query_one("#find-count", Static).content)


async def type_query(pilot, *keys: str) -> None:
    await pilot.press("slash")
    await pilot.pause()
    await pilot.press(*keys)
    await pilot.pause()


async def test_slash_in_the_timeline_is_still_the_channel_search(reading):
    app = make_app(reading)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert app.focused.id == "search"
        assert app.screen.query_one("#find-bar").display is False


async def test_slash_in_the_reader_searches_the_document(reading):
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_reader(app, pilot)
        await type_query(pilot, "s", "h", "a", "r", "d")

        assert app.focused.id == "find"
        assert "1/2" in count(app)
        assert pane._lit is not None
        assert "shard" in (pane._lit.source or "").lower(), "a block that does not match"


async def test_n_and_N_step_the_matches_after_the_box_has_gone(reading):
    """Vim keeps the query after the search line closes, and so does this."""
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_reader(app, pilot)
        await type_query(pilot, "s", "h", "a", "r", "d")
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.query_one("#find-bar").display is False
        assert app.focused.id == "reader"
        first = pane._lit

        await pilot.press("n")
        await pilot.pause()
        assert "2/2" in count(app)
        assert pane._lit is not first

        await pilot.press("n")  # wraps
        await pilot.pause()
        assert "1/2" in count(app)
        await pilot.press("N")
        await pilot.pause()
        assert "2/2" in count(app)


async def test_the_reader_scrolls_to_the_match(reading):
    app = make_app(reading)
    async with app.run_test(size=(100, 24)) as pilot:
        await open_reader(app, pilot)
        reader = app.screen.query_one("#reader")
        assert reader.scroll_offset.y == 0

        await type_query(pilot, "m", "i", "s", "m", "a", "t", "c", "h")
        assert "1/1" in count(app)
        assert reader.scroll_offset.y > 0, "the match was below the fold"


async def test_a_query_with_no_matches_says_so(reading):
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_reader(app, pilot)
        await type_query(pilot, "z", "z", "z")
        assert "no matches" in count(app)
        assert pane._lit is None


async def test_escape_clears_the_document_search_and_leaves_the_filter(reading):
    """Two things escape can undo, and the nearer one goes first."""
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_reader(app, pilot)
        await pilot.press("ctrl+w", "h")  # the timeline, for the channel search
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("m", "i", "l", "e")
        await pilot.press("enter")
        await pilot.pause()
        assert pane.filter.query == "mile"

        await pilot.press("ctrl+w", "l")
        await pilot.pause()
        await type_query(pilot, "s", "h", "a", "r", "d")
        await pilot.press("escape")
        await pilot.pause()

        assert pane._find == ""
        assert pane._lit is None
        assert app.screen.query_one("#find-bar").display is False
        assert pane.filter.query == "mile", "the channel search went with it"

        await pilot.press("escape")  # now the filter's turn
        await pilot.pause()
        assert pane.filter.query == ""


async def test_a_rewritten_document_keeps_the_query(reading):
    """A session rewrites its own files. Being thrown back to the first match
    for a status update nobody asked about is worse than being one match out."""
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_reader(app, pilot)
        await type_query(pilot, "s", "h", "a", "r", "d")
        await pilot.press("enter")
        await pilot.press("n")
        await pilot.pause()
        assert "2/2" in count(app)

        write(
            reading / ENTRY,
            "# [milestone] Sweep closed\n\nshard 3, shard 4 and shard 5 all reindexed.\n",
        )
        app.rescan()
        await settle(app, pilot)

        assert pane._find == "shard"
        assert "/3" in count(app), count(app)


async def test_moving_to_another_entry_keeps_the_query_but_not_the_place(reading):
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_reader(app, pilot)
        await type_query(pilot, "s", "h", "a", "r", "d")
        await pilot.press("enter")
        await pilot.pause()

        timeline = app.screen.query_one("#timeline", OptionList)
        timeline.highlighted = timeline.get_option_index("pin:STATUS.md")
        await settle(app, pilot)

        # The fixture's STATUS.md mentions a shard too, so there is something
        # to find here -- and nothing is lit until n asks for it.
        assert pane._find == "shard"
        assert pane._lit is None
        assert "1 match" in count(app), count(app)

        await pilot.press("n")
        await pilot.pause()
        assert pane._lit is not None
        assert "1/" in count(app)


# -- inside an opened file ------------------------------------------------


async def open_file(app, pilot) -> ChannelPane:
    pane = await open_reader(app, pilot)
    await pilot.press("]", "f")  # the first reference: notes.md
    await pilot.press("]", "f")  # the second: tail.txt
    await pilot.press("g", "f")
    await settle(app, pilot)
    return pane


async def test_the_search_follows_the_keyboard_into_an_opened_file(reading):
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_file(app, pilot)
        assert pane.preview.showing_name == "tail.txt"
        assert app.focused.id == "preview-body"

        await type_query(pilot, "s", "h", "a", "r", "d")
        assert pane._find_in == "file"
        assert "1/40" in count(app)


async def test_a_delivered_text_file_is_highlighted_in_place(reading):
    """We laid that pane out ourselves, so the match itself can be lit rather
    than the block around it."""
    app = make_app(reading)
    async with app.run_test() as pilot:
        await open_file(app, pilot)
        await type_query(pilot, "w", "o", "r", "k", "e", "r")
        await pilot.press("enter")
        await pilot.pause()

        spans = app.screen.query_one("#preview-text", Static).content.spans
        assert len(spans) == 40
        assert sorted({span.style for span in spans}) == ["reverse", "underline"]
        assert sum(span.style == "reverse" for span in spans) == 1

        await pilot.press("n")
        await pilot.pause()
        moved = app.screen.query_one("#preview-text", Static).content.spans
        assert moved[0].style == "underline" and moved[1].style == "reverse"


async def test_a_match_below_the_fold_scrolls_the_file(reading):
    app = make_app(reading)
    async with app.run_test(size=(100, 24)) as pilot:
        await open_file(app, pilot)
        body = app.screen.query_one("#preview-body")
        await type_query(pilot, "l", "i", "n", "e", " ", "3", "7")
        assert "1/1" in count(app)
        assert body.scroll_offset.y > 0


async def test_escape_clears_the_search_before_it_closes_the_file(reading):
    """One key undoing two things, in the order they were done."""
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_file(app, pilot)
        await type_query(pilot, "s", "h", "a", "r", "d")
        await pilot.press("enter")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert pane._find == ""
        assert pane.preview.display is True, "the file went with the search"

        await pilot.press("escape")
        await pilot.pause()
        assert pane.preview.display is False


async def test_closing_the_file_forgets_a_search_that_was_inside_it(reading):
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_file(app, pilot)
        await type_query(pilot, "s", "h", "a", "r", "d")
        await pilot.press("enter")
        await pilot.pause()
        assert pane._matches

        pane._close_preview()
        await pilot.pause()
        assert pane._find == ""
        assert pane._matches == []


# -- the files a document references --------------------------------------


async def test_bracket_f_steps_the_references_and_gf_opens_one(reading):
    """]f and [f walk what the notification points at; gf is go-to-file."""
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_reader(app, pilot)
        strip = app.screen.query_one("#attachments", OptionList)

        await pilot.press("]", "f")
        await pilot.pause()
        assert strip.highlighted == 0, "the first file, not the second"

        await pilot.press("]", "f")
        await pilot.pause()
        assert strip.highlighted == 1

        await pilot.press("[", "f")
        await pilot.pause()
        assert strip.highlighted == 0

        await pilot.press("g", "f")
        await settle(app, pilot)
        assert pane.preview.showing_name == "notes.md"


async def test_stepping_a_reference_scrolls_the_prose_to_it(reading):
    """The line that names a file is the part of the document it is about."""
    app = make_app(reading)
    async with app.run_test(size=(100, 24)) as pilot:
        await open_reader(app, pilot)
        reader = app.screen.query_one("#reader")
        assert reader.scroll_offset.y == 0

        await pilot.press("]", "f")
        await pilot.pause()
        assert reader.scroll_offset.y > 0, "the reference is at the foot of a long entry"


async def test_the_reference_keys_do_nothing_on_prose(reading):
    """Most notifications deliver no files at all."""
    app = make_app(reading)
    async with app.run_test() as pilot:
        pane = await open_reader(app, pilot)
        timeline = app.screen.query_one("#timeline", OptionList)
        timeline.highlighted = timeline.get_option_index("pin:STATUS.md")
        await settle(app, pilot)

        await pilot.press("]", "f")
        await pilot.press("g", "f")
        await settle(app, pilot)
        assert pane.preview.display is False
