"""Reading a channel must not be interrupted by the poll.

The timeline is rebuilt from a folder that another machine is writing, on a
two-second timer. Every property here is about that rebuild staying invisible
to someone in the middle of reading something.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from textual.widgets import Markdown, OptionList, TabbedContent

from conftest import NOW, write
from inzaghi.ui.channel_view import ChannelPane
from test_ui import make_app, settle


async def open_channel(app, pilot):
    await settle(app, pilot)
    app.query_one("#tabs", TabbedContent).active = "ch0"
    await pilot.pause()
    await pilot.press("a")  # stabilise unread, so only time is left changing
    await settle(app, pilot)
    return app.query_one(ChannelPane)


def select(app, pane, key: str):
    timeline = app.screen.query_one(OptionList)
    timeline.highlighted = timeline.get_option_index(key)
    return timeline


def count_renders(pane, monkeypatch) -> list[int]:
    calls = [0]
    original = pane._render_rows

    def counted():
        calls[0] += 1
        original()

    monkeypatch.setattr(pane, "_render_rows", counted)
    return calls


@pytest.fixture
def reading(channel_root):
    """An app with an old entry selected, as if someone were reading it."""
    return make_app(channel_root)


async def test_the_cursor_does_not_move_as_relative_times_tick(reading, monkeypatch):
    """The regression: labels say "4m ago", so they change while rows do not."""
    app = reading
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        snapshot = app.snapshots[pane.channel.key]
        oldest = str(snapshot.events[-1].path)
        timeline = select(app, pane, oldest)
        await pilot.pause()
        index, selected = timeline.highlighted, pane._selected
        renders = count_renders(pane, monkeypatch)

        for minutes in (1, 5, 30, 90):
            pane.update(snapshot, set(), NOW + timedelta(minutes=minutes))
            await pilot.pause()

        assert pane._selected == selected == oldest
        assert timeline.highlighted == index
        assert renders[0] == 0, "the list was rebuilt for a cosmetic change"


async def test_labels_still_refresh_while_the_cursor_stays(reading):
    """Not rebuilding must not mean not updating."""
    app = reading
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        snapshot = app.snapshots[pane.channel.key]
        timeline = app.screen.query_one(OptionList)
        prompts = lambda: [
            str(timeline.get_option_at_index(i).prompt) for i in range(timeline.option_count)
        ]
        before_labels, before_prompts = list(pane._labels), prompts()
        cursor = timeline.highlighted

        pane.update(snapshot, set(), NOW + timedelta(hours=3))
        await pilot.pause()

        assert pane._labels != before_labels, "the ages did not change"
        assert prompts() != before_prompts, "the rows were not rewritten in place"
        assert timeline.highlighted == cursor


async def test_a_new_entry_arriving_does_not_steal_the_cursor(reading, channel_root):
    app = reading
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        oldest = str(app.snapshots[pane.channel.key].events[-1].path)
        select(app, pane, oldest)
        await pilot.pause()

        write(
            channel_root / "notifications" / "2026-09-05_0700_error_disk-full.md",
            "# [error] Disk full\n",
        )
        app.rescan()
        await settle(app, pilot)

        assert pane._selected == oldest
        timeline = app.screen.query_one(OptionList)
        assert timeline.highlighted == timeline.get_option_index(oldest)


async def test_the_reader_keeps_its_place_when_the_document_is_rewritten(reading, channel_root):
    """A status file refreshed mid-read must not jump back to the top."""
    app = reading
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        target = channel_root / "notifications" / "2026-09-04_2325_milestone_shard-2-reindexed.md"
        select(app, pane, str(target))
        await pilot.pause()

        scrolls = []
        reader = app.screen.query_one("#reader")
        original = reader.scroll_home
        reader.scroll_home = lambda **kw: scrolls.append(1)

        write(target, "# [milestone] Shard 2 reindexed\n\nNow with more detail.\n")
        app.rescan()
        await settle(app, pilot)

        assert scrolls == [], "the reader was scrolled home for the same document"
        assert "more detail" in str(app.screen.query_one("#doc", Markdown)._markdown)
        reader.scroll_home = original


async def test_moving_to_another_entry_does_scroll_to_its_top(reading):
    app = reading
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        snapshot = app.snapshots[pane.channel.key]
        select(app, pane, str(snapshot.events[-1].path))
        await pilot.pause()

        scrolls = []
        reader = app.screen.query_one("#reader")
        reader.scroll_home = lambda **kw: scrolls.append(1)
        select(app, pane, str(snapshot.events[0].path))
        await pilot.pause()

        assert scrolls, "a different document should start at its top"
