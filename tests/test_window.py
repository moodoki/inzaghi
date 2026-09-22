"""Only part of a long channel is built at a time.

A channel that has been running a month has thousands of entries, and the cost
of building a row is paid on every rebuild, every tab switch and every search.
Nobody reads to the bottom of one, so the bottom is not built until something
asks for it.
"""

from __future__ import annotations

import pytest
from textual.widgets import OptionList, TabbedContent

from conftest import write
from inzaghi.ui.channel_view import WINDOW_ROWS, ChannelPane
from inzaghi.ui.rows import Row, clip_divider, older_label
from test_filtering import search_for
from test_ui import make_app, settle

#: Comfortably past the window, and small enough that writing them is quick.
ENTRIES = WINDOW_ROWS + 60


def row(key: str, *, unread: bool = False) -> Row:
    return Row(key=key, doc=None, kind="milestone", label=key, unread=unread, readable=True)


# -- where the divider goes when it falls past the fold -------------------


def test_a_divider_inside_the_window_is_left_alone():
    rows = [row("a", unread=True), row("b"), row("c")]
    assert clip_divider((0, 1), rows, window=3) == (0, 1)


def test_a_divider_past_the_fold_moves_to_the_fold_and_counts_what_is_above():
    rows = [row(str(n), unread=True) for n in range(5)] + [row("old")]
    assert clip_divider((4, 5), rows, window=3) == (2, 3)


def test_no_divider_when_nothing_above_the_fold_is_new():
    rows = [row("a"), row("b"), row("c", unread=True), row("d")]
    assert clip_divider((2, 1), rows, window=2) is None


def test_the_foot_of_the_window_says_how_many_and_how_many_more():
    label = older_label(1203, 400)
    assert "1,203 older" in label
    assert "400 more" in label


# -- through the UI -------------------------------------------------------


@pytest.fixture
def long_channel(channel_root):
    """The fixture channel, with enough entries to run past the window."""
    for n in range(ENTRIES):
        day, hour, minute = 1 + n // 1440, (n // 60) % 24, n % 60
        write(
            channel_root / "notifications" / f"2026-08-{day:02d}_{hour:02d}{minute:02d}_note_e{n}.md",
            f"# [note] Entry {n}\n\nThe body of entry {n}.\n",
        )
    return channel_root


async def open_channel(app, pilot):
    await settle(app, pilot)
    app.query_one("#tabs", TabbedContent).active = "ch0"
    await pilot.pause()
    return app.query_one(ChannelPane)


def rows_shown(pane) -> list[str]:
    """The keys actually built, in order, ignoring the rules and the foot."""
    timeline = pane.query_one("#timeline", OptionList)
    return [
        timeline.get_option_at_index(index).id
        for index in range(timeline.option_count)
        if timeline.get_option_at_index(index).id is not None
    ]


async def test_a_long_channel_builds_a_window_of_it(long_channel):
    app = make_app(long_channel)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        assert len(pane._rows) > WINDOW_ROWS  # the channel really is longer
        assert len(rows_shown(pane)) == WINDOW_ROWS
        assert rows_shown(pane) == [row.key for row in pane._rows[:WINDOW_ROWS]]


async def test_the_last_option_is_the_foot_of_the_window(long_channel):
    app = make_app(long_channel)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        timeline = pane.query_one("#timeline", OptionList)
        foot = timeline.get_option_at_index(timeline.option_count - 1)
        assert foot.id is None  # not a row: nothing restores a cursor to it
        assert f"{len(pane._rows) - WINDOW_ROWS:,} older" in foot.prompt.plain


async def test_selecting_the_foot_shows_more_of_the_channel(long_channel):
    app = make_app(long_channel)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        first_hidden = pane._rows[WINDOW_ROWS].key

        timeline = pane.query_one("#timeline", OptionList)
        timeline.focus()
        timeline.highlighted = timeline.option_count - 1
        await pilot.press("enter")
        await pilot.pause()

        assert len(rows_shown(pane)) == len(pane._rows)  # the rest of this one
        assert pane._selected == first_hidden  # reading carries on where it stopped


async def test_g_reveals_the_whole_channel(long_channel):
    """The one motion that names the far end pays for the far end."""
    app = make_app(long_channel)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        pane.query_one("#timeline", OptionList).focus()
        await pilot.press("G")
        await pilot.pause()

        assert len(rows_shown(pane)) == len(pane._rows)
        assert pane._selected == pane._rows[-1].key


async def test_a_search_finds_an_entry_from_beyond_the_window(long_channel):
    """Filtering is over the whole channel; the window is only what is built."""
    app = make_app(long_channel)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        oldest = pane._rows[-1]
        assert oldest.key not in rows_shown(pane)

        await search_for(pilot, "body of entry 0.")
        assert [r.key for r in pane._rows] == [oldest.key]
        assert rows_shown(pane) == [oldest.key]


async def test_an_arriving_message_does_not_shut_a_window_someone_opened(long_channel):
    app = make_app(long_channel)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        pane.query_one("#timeline", OptionList).focus()
        await pilot.press("G")
        await pilot.pause()
        opened = len(rows_shown(pane))

        write(
            long_channel / "notifications" / "2026-09-05_0400_note_new.md",
            "# [note] One more\n\nArrived while you were reading.\n",
        )
        app.rescan()
        await settle(app, pilot)

        assert len(rows_shown(pane)) == opened + 1


async def test_a_new_filter_starts_the_window_again(long_channel):
    app = make_app(long_channel)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        pane.query_one("#timeline", OptionList).focus()
        await pilot.press("G")
        await pilot.pause()
        assert len(rows_shown(pane)) > WINDOW_ROWS
        await pilot.press("g", "g")  # back to the top, so nothing holds it open
        await pilot.pause()

        await search_for(pilot, "entry")
        assert len(rows_shown(pane)) == WINDOW_ROWS


async def test_the_divider_counts_what_is_above_it_when_it_is_clipped(long_channel):
    """Read the oldest entry only: the line that divides them is past the fold."""
    app = make_app(long_channel)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        key = str(long_channel)
        app.state.mark_read(key, app.snapshots[key].events[-1])
        app.rescan()
        await settle(app, pilot)

        assert pane._divider is not None
        assert pane._divider[0] >= WINDOW_ROWS  # the true line is past the fold

        timeline = pane.query_one("#timeline", OptionList)
        labels = [
            timeline.get_option_at_index(index).prompt.plain
            for index in range(timeline.option_count)
        ]
        above = sum(1 for r in pane._rows[:WINDOW_ROWS] if r.unread)
        assert any(f"{above} new above" in label for label in labels)
