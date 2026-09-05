"""Filtering and search over a channel's timeline."""

from __future__ import annotations

import pytest
from textual.widgets import Input, OptionList, TabbedContent

from conftest import NOW, write
from inzaghi.ui.channel_view import ChannelPane
from inzaghi.ui.rows import ALL, Filter, build_rows, filter_bar, kind_cycle
from test_ui import make_app, settle


@pytest.fixture
def rows(channel, channel_root):
    write(
        channel_root / "notifications" / "2026-09-05_0100_error_disk-full.md",
        "# [error] Disk full on /scratch\n\nThe run stopped writing checkpoints.\n",
    )
    return build_rows(channel.scan(now=NOW), unread=set(), now=NOW)


def kinds(rows) -> list[str]:
    return [row.kind for row in rows]


def test_no_filter_shows_everything(rows):
    assert Filter().apply(rows) == rows
    assert Filter().active is False


def test_kind_filter_keeps_only_that_kind(rows):
    assert kinds(Filter(kind="milestone").apply(rows)) == ["milestone"]
    assert kinds(Filter(kind="pinned").apply(rows)) == ["pinned"] * 3


def test_search_matches_the_body_not_just_the_title(rows):
    """The title says "Disk full"; only the body says "checkpoints"."""
    found = Filter(query="checkpoints").apply(rows)
    assert [row.doc.path.name for row in found] == ["2026-09-05_0100_error_disk-full.md"]


def test_search_is_case_insensitive(rows):
    assert Filter(query="DISK FULL").apply(rows) == Filter(query="disk full").apply(rows)


def test_search_and_kind_compose(rows):
    assert Filter(kind="error", query="checkpoints").apply(rows)
    assert Filter(kind="milestone", query="checkpoints").apply(rows) == []


def test_a_search_with_no_matches_returns_nothing(rows):
    assert Filter(query="no such words anywhere").apply(rows) == []


def test_whitespace_only_search_is_not_a_filter(rows):
    assert Filter(query="   ").apply(rows) == rows
    assert Filter(query="   ").active is False


def test_kind_cycle_starts_at_all_and_covers_what_is_present(rows):
    cycle = kind_cycle(rows)
    assert cycle[0] == ALL
    assert set(cycle[1:]) == set(kinds(rows))


def test_filter_bar_marks_the_active_kind(rows):
    bar = filter_bar(rows, Filter(kind="milestone"))
    assert "[reverse b] milestone 1 [/]" in bar
    assert "[dim]ack 1[/]" in bar


def test_filter_bar_shows_the_query(rows):
    assert "/disk" in filter_bar(rows, Filter(query="disk"))


# -- through the UI -------------------------------------------------------


async def open_channel(app, pilot):
    await settle(app, pilot)
    app.query_one("#tabs", TabbedContent).active = "ch0"
    await pilot.pause()
    return app.query_one(ChannelPane)


async def test_f_cycles_the_kind_filter(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        assert pane.filter.kind == ALL
        await pilot.press("f")
        assert pane.filter.kind == kind_cycle(pane._all_rows)[1]
        assert kinds(pane._rows) == [pane.filter.kind]


async def test_shift_f_cycles_backwards(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        await pilot.press("F")
        assert pane.filter.kind == kind_cycle(pane._all_rows)[-1]


async def test_slash_opens_search_and_typing_filters_live(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        await pilot.press("slash")
        assert app.screen.query_one("#search", Input).display is True
        for key in "shard":
            await pilot.press(key)
        await pilot.pause()
        assert pane.filter.query == "shard"
        assert all("shard" in row.text for row in pane._rows)


async def test_enter_hides_the_box_but_keeps_the_search(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        await pilot.press("slash", "s", "h", "a", "r", "d", "enter")
        await pilot.pause()
        assert app.screen.query_one("#search", Input).display is False
        assert pane.filter.query == "shard"


async def test_escape_clears_both_filters(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        await pilot.press("f")
        await pilot.press("slash", "s", "h", "a", "r", "d", "enter")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert pane.filter == Filter()
        assert len(pane._rows) == len(pane._all_rows)


async def test_an_empty_result_does_not_leave_a_stale_selection(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        await pilot.press("slash")
        for key in "zzzz":
            await pilot.press(key)
        await pilot.pause()
        assert pane._rows == []
        assert app.screen.query_one(OptionList).option_count == 1  # the "no matches" line


async def test_filtering_survives_a_rescan(channel_root):
    """A poll must not quietly drop the filter someone is reading through."""
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        await pilot.press("f")
        kind = pane.filter.kind
        app.rescan()
        await settle(app, pilot)
        assert pane.filter.kind == kind
        assert kinds(pane._rows) == [kind]
