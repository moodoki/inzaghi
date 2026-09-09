"""Deleting the duplicates a sync client leaves behind."""

from __future__ import annotations

import pytest
from textual.widgets import TabbedContent

from conftest import NOW, write
from inzaghi.channel import Channel, ReadOnlyChannel, remove_conflicts
from inzaghi.ui.channel_view import ChannelPane
from inzaghi.ui.modals import ConfirmScreen
from test_ui import make_app, settle

CONFLICT = "STATUS (conflicted copy 2026-09-05).md"


@pytest.fixture
def conflicted(channel_root):
    write(channel_root / "notifications" / CONFLICT, "# stale copy\n")
    write(channel_root / "inbox" / "pause (conflicted copy 2026-09-05).md", "PAUSE\n")
    return channel_root


def test_conflicts_are_found_but_never_shown_as_events(conflicted):
    snapshot = Channel(root=conflicted).scan(now=NOW)
    assert sorted(p.name for p in snapshot.conflicts) == [
        "STATUS (conflicted copy 2026-09-05).md",
        "pause (conflicted copy 2026-09-05).md",
    ]
    assert all("conflicted" not in event.path.name for event in snapshot.events)


def test_removing_deletes_only_the_conflicts(conflicted):
    channel = Channel(root=conflicted)
    snapshot = channel.scan(now=NOW)
    removed, problems = remove_conflicts(channel, snapshot.conflicts)

    assert len(removed) == 2 and problems == []
    assert (conflicted / "notifications" / "STATUS.md").exists()
    assert channel.scan(now=NOW).conflicts == []


def test_a_read_only_channel_refuses_deletion(conflicted):
    """Read-only means untouched, not merely unwritten-to."""
    channel = Channel(root=conflicted, read_only=True)
    conflicts = Channel(root=conflicted).scan(now=NOW).conflicts
    with pytest.raises(ReadOnlyChannel):
        remove_conflicts(channel, conflicts)
    assert all(path.exists() for path in conflicts)


def test_a_path_that_is_not_a_conflict_is_refused(conflicted):
    """The snapshot may be stale; the name is re-checked at deletion time."""
    channel = Channel(root=conflicted)
    real = conflicted / "notifications" / "STATUS.md"
    removed, problems = remove_conflicts(channel, [real])
    assert removed == [] and "not a conflict copy" in problems[0]
    assert real.exists()


def test_a_conflict_outside_the_channel_is_refused(conflicted, tmp_path):
    outside = write(tmp_path / "elsewhere" / CONFLICT, "x\n")
    removed, problems = remove_conflicts(Channel(root=conflicted), [outside])
    assert removed == [] and problems
    assert outside.exists()


def test_a_file_that_vanished_first_is_reported_not_raised(conflicted):
    channel = Channel(root=conflicted)
    conflicts = channel.scan(now=NOW).conflicts
    for path in conflicts:
        path.unlink()
    removed, problems = remove_conflicts(channel, conflicts)
    assert removed == [] and len(problems) == 2


# -- through the UI -------------------------------------------------------


async def open_channel(app, pilot):
    await settle(app, pilot)
    app.query_one("#tabs", TabbedContent).active = "ch0"
    await pilot.pause()
    return app.query_one(ChannelPane)


async def test_the_key_is_hidden_when_there_is_nothing_to_clean(channel_root):
    """False, not None: Textual hides on False and merely dims on None."""
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await open_channel(app, pilot)
        assert app.check_action("clean_conflicts", ()) is False


async def test_the_key_appears_once_a_conflict_shows_up(conflicted):
    app = make_app(conflicted)
    async with app.run_test() as pilot:
        await open_channel(app, pilot)
        assert app.check_action("clean_conflicts", ()) is True


async def test_the_key_stays_hidden_on_a_read_only_channel(conflicted):
    app = make_app(conflicted, read_only=True)
    async with app.run_test() as pilot:
        await open_channel(app, pilot)
        assert app.check_action("clean_conflicts", ()) is False


async def test_cleaning_asks_first_and_cancelling_keeps_the_files(conflicted):
    app = make_app(conflicted)
    async with app.run_test() as pilot:
        await open_channel(app, pilot)
        conflicts = app.snapshots[str(conflicted)].conflicts
        await pilot.press("K")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        await settle(app, pilot)
        assert all(path.exists() for path in conflicts)


async def test_confirming_deletes_them_and_the_warning_clears(conflicted):
    app = make_app(conflicted)
    async with app.run_test() as pilot:
        await open_channel(app, pilot)
        conflicts = list(app.snapshots[str(conflicted)].conflicts)
        await pilot.press("K")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert not any(path.exists() for path in conflicts)
        assert app.snapshots[str(conflicted)].conflicts == []
        assert app.check_action("clean_conflicts", ()) is False
