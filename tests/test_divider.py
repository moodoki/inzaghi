"""The "new since you last looked" divider."""

from __future__ import annotations

import pytest
from textual.widgets import OptionList, TabbedContent

from conftest import NOW, write
from inzaghi.state import ReadState
from inzaghi.ui.channel_view import ChannelPane
from inzaghi.ui.rows import Row, build_rows, divider_label, unread_divider
from test_filtering import search_for
from test_ui import make_app, settle


def row(key: str, *, unread: bool, readable: bool = True) -> Row:
    return Row(key=key, doc=None, kind="milestone", label=key, unread=unread, readable=readable)


def test_no_unread_means_no_divider():
    assert unread_divider([row("a", unread=False), row("b", unread=False)]) is None


def test_divider_sits_after_the_last_unread_row():
    rows = [row("a", unread=True), row("b", unread=True), row("c", unread=False)]
    assert unread_divider(rows) == (1, 2)


def test_all_unread_needs_no_divider():
    """With nothing read beneath it, the line would divide nothing."""
    assert unread_divider([row("a", unread=True), row("b", unread=True)]) is None


def test_a_rewritten_old_entry_keeps_the_line_honest():
    """Unread is not always contiguous; the line must still sit below all of it."""
    rows = [row("new", unread=True), row("old-read", unread=False), row("rewritten", unread=True)]
    assert unread_divider(rows) is None  # the last unread is the last row

    rows.append(row("older", unread=False))
    assert unread_divider(rows) == (2, 2)


def test_your_own_messages_below_do_not_make_a_boundary():
    """An outbound row is never unread, so it is not proof anything was read."""
    rows = [row("new", unread=True), row("you", unread=False, readable=False)]
    assert unread_divider(rows) is None


def test_label_says_which_way_is_new():
    assert "3 new above" in divider_label(3)


# -- through the UI -------------------------------------------------------


async def open_channel(app, pilot):
    await settle(app, pilot)
    app.query_one("#tabs", TabbedContent).active = "ch0"
    await pilot.pause()
    return app.query_one(ChannelPane)


async def test_the_divider_appears_between_new_and_seen(channel_root, tmp_path):
    """Read everything, then let one new notification arrive."""
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        assert pane._divider is None

        write(
            channel_root / "notifications" / "2026-09-05_0700_error_disk-full.md",
            "# [error] Disk full\n",
        )
        app.rescan()
        await settle(app, pilot)

        index, count = pane._divider
        assert count == 1
        assert pane._rows[index].kind == "error"
        labels = [str(app.screen.query_one(OptionList).get_option_at_index(i).prompt)
                  for i in range(app.screen.query_one(OptionList).option_count)]
        assert any("1 new above" in label for label in labels)


async def test_reading_the_new_entry_retires_the_divider(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        # Everything inbound is unread, and the only row below is your own
        # message, which was never unread -- nothing to divide.
        assert pane._divider is None

        state: ReadState = app.state
        key = str(channel_root)
        snapshot = app.snapshots[key]
        state.mark_read(key, snapshot.events[-1])  # read only the oldest
        app.rescan()
        await settle(app, pilot)
        assert pane._divider is not None

        await pilot.press("a")
        await settle(app, pilot)
        assert pane._divider is None


async def test_the_divider_follows_the_active_filter(channel_root, tmp_path):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        await pilot.press("a")
        write(
            channel_root / "notifications" / "2026-09-05_0700_error_disk-full.md",
            "# [error] Disk full\n",
        )
        app.rescan()
        await settle(app, pilot)
        assert pane._divider is not None

        # Search for something the new entry does not say: nothing unread
        # is visible, so there is no boundary to draw.
        await search_for(pilot, "shard")
        await pilot.press("enter")
        await settle(app, pilot)
        assert pane._divider is None
