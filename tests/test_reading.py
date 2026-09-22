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
    timeline = app.screen.query_one("#timeline", OptionList)
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
    """An app with an old entry selected, as if someone were reading it.

    With the timers turned right down: every test here drives the refresh
    itself, on a clock it chose, and counts the rebuilds that follow. A poll
    landing between the call and the assertion is a rebuild nobody asked for
    and the count is then off by one, which is a flake rather than a finding.
    """
    app = make_app(channel_root)
    app.config.poll_seconds = 3600.0
    app.config.discover_seconds = 3600.0
    return app


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
        timeline = app.screen.query_one("#timeline", OptionList)
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
        timeline = app.screen.query_one("#timeline", OptionList)
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


# -- not rebuilding what did not change -----------------------------------


def count_builds(pane, monkeypatch) -> list[int]:
    """Count the rebuilds of the row list, without changing what it returns."""
    calls: list[int] = []
    import inzaghi.ui.channel_view as view

    original = view.build_rows

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(view, "build_rows", counted)
    return calls


async def test_a_poll_that_found_nothing_does_not_rebuild_the_rows(reading, monkeypatch):
    """The documents are the same objects the cache handed back and the labels
    still read the same, so there is nothing for a rebuild to produce."""
    app = reading
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        snapshot = app.snapshots[pane.channel.key]
        unread = {str(e.path) for e in app.state.unread(pane.channel.key, snapshot)}
        builds = count_builds(pane, monkeypatch)

        for second in range(1, 6):
            pane.update(snapshot, set(unread), pane._built_at + timedelta(seconds=second))
            await pilot.pause()

        assert builds == [], f"rebuilt {len(builds)} times with nothing to show"


async def test_a_label_coming_due_rebuilds_them(reading, monkeypatch):
    app = reading
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        snapshot = app.snapshots[pane.channel.key]
        unread = {str(e.path) for e in app.state.unread(pane.channel.key, snapshot)}
        due = pane._labels_at
        builds = count_builds(pane, monkeypatch)

        pane.update(snapshot, set(unread), due - timedelta(milliseconds=1))
        await pilot.pause()
        assert builds == [], "rebuilt before any label was due"

        pane.update(snapshot, set(unread), due)
        await pilot.pause()
        assert len(builds) == 1, "the label came due and the rows were not rebuilt"


async def test_anything_the_scan_changed_rebuilds_them(reading, channel_root, monkeypatch):
    """Each of the things a row is built from, one at a time."""
    app = reading
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        key = pane.channel.key
        builds = count_builds(pane, monkeypatch)
        soon = pane._built_at + timedelta(seconds=1)

        # A new entry.
        write(
            channel_root / "notifications" / "2026-09-05_0530_milestone_new-one.md",
            "# [milestone] A new one\n",
        )
        app.rescan()
        await settle(app, pilot)
        assert len(builds) >= 1, "a new notification did not rebuild"

        # A row read.
        snapshot = app.snapshots[key]
        before = len(builds)
        pane.update(snapshot, set(), soon)
        await pilot.pause()
        assert len(builds) == before + 1, "an emptied unread set did not rebuild"


async def test_the_reader_is_still_refreshed_on_a_skipped_poll(reading, monkeypatch):
    """The convention the skip must not break: what the selected entry holds
    can change while its row does not -- a rewritten status file, an
    attachment that has landed, and the open file's own re-read timer."""
    app = reading
    async with app.run_test() as pilot:
        pane = await open_channel(app, pilot)
        snapshot = app.snapshots[pane.channel.key]
        unread = {str(e.path) for e in app.state.unread(pane.channel.key, snapshot)}
        shown: list[str] = []
        monkeypatch.setattr(
            type(pane), "_show", lambda self, row: shown.append(row.key)
        )

        pane.update(snapshot, set(unread), pane._built_at + timedelta(seconds=1))
        await pilot.pause()
        assert shown, "the reader was skipped along with the rows"
