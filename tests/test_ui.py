"""UI tests, driven headlessly through Textual's pilot."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from shutil import rmtree
from unittest import mock

from textual.widgets import DataTable, Input, OptionList, Static, TabbedContent, TextArea

from conftest import write

from inzaghi import channel as channel_module
from inzaghi import compose as composer_module
from inzaghi.config import ChannelSpec, Config, RootSpec
from inzaghi.protocol import init_channel
from inzaghi.ui.app import OVERVIEW_ID, InzaghiApp
from inzaghi.ui.channel_view import ChannelPane
from inzaghi.ui.overview import (
    PIGEON,
    PIGEON_BLINK,
    PIGEON_HEIGHT,
    PIGEON_WIDTH,
    OverviewPane,
)
from inzaghi.ui.composer import Composer
from inzaghi.ui.modals import ConfirmScreen


def make_app(root: Path, *, read_only: bool = False) -> InzaghiApp:
    config = Config(channels=[ChannelSpec(path=root, name=root.name, read_only=read_only)])
    return InzaghiApp(config)


async def settle(app, pilot):
    """Let the scan worker finish and its results reach the widgets."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_a_tab_and_a_row_per_channel(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.query_one(DataTable).row_count == 1
        assert len(app.query(ChannelPane)) == 1
        assert app.snapshots[str(channel_root)].events


async def test_selecting_a_row_opens_that_channel(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.query_one("#tabs", TabbedContent).active == OVERVIEW_ID
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#tabs", TabbedContent).active == "ch0"


async def test_timeline_pins_the_live_panels_above_the_log(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        pane = app.query_one(ChannelPane)
        assert [row.key for row in pane._rows[:3]] == [
            "pin:STATUS.md",
            "pin:HEARTBEAT.md",
            "pin:TASK_OVERVIEW.md",
        ]


async def test_reading_a_row_clears_it_from_unread(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        key = str(channel_root)
        assert len(app.state.unread(key, app.snapshots[key])) == 2
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        timeline = app.query_one("#timeline", OptionList)
        timeline.highlighted = timeline.get_option_index(
            str(app.snapshots[key].events[0].path)
        )
        await pilot.pause()
        assert len(app.state.unread(key, app.snapshots[key])) == 1


async def test_mark_all_read_empties_the_badge(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await pilot.pause()
        key = str(channel_root)
        assert app.state.unread(key, app.snapshots[key]) == []


async def test_compose_writes_a_message_into_the_inbox(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        composer = app.screen.query_one(Composer)
        assert composer.display is True
        composer.query_one(TextArea).text = "drop to batch 8"
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        sent = list((channel_root / "inbox").glob("*.md"))
        assert [p.read_text() for p in sent] == ["drop to batch 8\n"]


async def test_stop_asks_first_and_cancelling_sends_nothing(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        await settle(app, pilot)
        assert list((channel_root / "inbox").glob("*.md")) == []


async def test_confirming_stop_sends_the_keyword(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        (sent,) = list((channel_root / "inbox").glob("*.md"))
        assert sent.read_text() == "STOP\n"


async def test_a_read_only_channel_cannot_be_written_from_the_ui(channel_root):
    """The guard that keeps a live session's folder safe from a dev build."""
    app = make_app(channel_root, read_only=True)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        for key in ("c", "s", "x"):
            await pilot.press(key)
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmScreen)
            assert app.screen.query_one(Composer).display is False
        await settle(app, pilot)
        assert list((channel_root / "inbox").glob("*.md")) == []


# -- the overview summary -------------------------------------------------


def summary(app) -> str:
    return str(app.query_one("#overview-summary", Static).content)


async def test_reading_an_error_clears_it_from_the_overview_count(channel_root):
    """The count answers "does anything want me", not "did anything ever go wrong"."""
    # The app runs on the wall clock, so give the channel a live heartbeat --
    # otherwise it is overdue and asks for you on that account alone.
    now = datetime.now().astimezone()
    write(
        channel_root / "notifications" / "HEARTBEAT.md",
        f"# heartbeat\n\n- **updated:** {now.isoformat()}\n"
        f"- **next update expected by:** {(now + timedelta(hours=1)).isoformat()}\n",
    )
    write(
        channel_root / "notifications" / "2026-09-05_0100_error_index-writer-crashed.md",
        "# [error] Index writer crashed on shard 4\n",
    )
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert "1 need you" in summary(app)

        await pilot.press("a")  # mark everything read
        await settle(app, pilot)

        assert "1 need you" not in summary(app)
        assert "none waiting" in summary(app)


# -- pruning read receipts ------------------------------------------------


def seed_receipts(tmp_path: Path, *channel_keys: str) -> Path:
    """A read.json already holding a receipt for each of ``channel_keys``."""
    path = tmp_path / "state" / "read.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = {key: {f"{key}/notifications/read.md": "1:1"} for key in channel_keys}
    path.write_text(json.dumps({"version": 1, "seen": seen}), encoding="utf-8")
    return path


async def test_receipts_for_channels_the_config_ignores_are_forgotten(channel_root, tmp_path):
    stranger = "/somewhere/else/entirely"
    seed_receipts(tmp_path, str(channel_root), stranger)
    app = root_app(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert str(channel_root) in app.state.seen
        assert stranger not in app.state.seen
    assert stranger not in json.loads((tmp_path / "state" / "read.json").read_text())["seen"]


async def test_an_unmounted_channel_keeps_its_receipts(channel_root, tmp_path):
    """Nothing under a watched root is forgotten for merely not being there."""
    absent = str(tmp_path / "southwind")          # configured for, not on disk
    seed_receipts(tmp_path, absent)
    app = root_app(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await rediscover(app, pilot)
        assert absent in app.state.seen


async def test_a_config_that_names_nothing_forgets_nothing(channel_root, tmp_path):
    """An empty config is one being written, not a decision to forget."""
    seed_receipts(tmp_path, str(channel_root), "/somewhere/else")
    app = InzaghiApp(Config())
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert set(app.state.seen) == {str(channel_root), "/somewhere/else"}


async def test_a_deleted_channel_loses_its_receipts(channel_root, tmp_path):
    """Gone from a volume we could see at the time -- that absence is believable."""
    seed_receipts(tmp_path, str(channel_root))
    app = root_app(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        init_channel(tmp_path / "southwind")
        await rediscover(app, pilot)
        app.state.seen[str(tmp_path / "southwind")] = {"whatever.md": "1:1"}

        rmtree(tmp_path / "southwind")
        await rediscover(app, pilot)

        assert str(tmp_path / "southwind") not in app.state.seen
        assert str(channel_root) in app.state.seen  # the survivor is untouched


# -- nothing slow on the UI thread ----------------------------------------
#
# The volume behind a channel belongs to a sync client, and it answers when it
# feels like it. Every call that can wait on it has to happen in a worker, or
# the whole app stops redrawing until it returns.


async def test_believing_an_absence_never_blocks_the_ui(channel_root, tmp_path):
    """The stat that decides a channel is gone asks the volume that went quiet."""
    app = root_app(tmp_path)
    callers: list[int] = []

    def watched(path):
        callers.append(threading.get_ident())
        return False

    async with app.run_test() as pilot:
        await settle(app, pilot)
        ui_thread = threading.get_ident()
        with mock.patch("inzaghi.ui.app.absence_is_real", watched):
            # A wedged volume looks exactly like this: discovery finds nothing.
            with mock.patch.object(Config, "discover", lambda self: []):
                await rediscover(app, pilot)

    assert callers, "the absence was never checked"
    assert ui_thread not in callers


async def test_sending_never_blocks_the_ui(channel_root):
    """compose.send writes into the synced folder and fsyncs it."""
    app = make_app(channel_root)
    callers: list[int] = []
    real_send = composer_module.send

    def watched(*args, **kwargs):
        callers.append(threading.get_ident())
        return real_send(*args, **kwargs)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        ui_thread = threading.get_ident()
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        with mock.patch("inzaghi.ui.app.composer.send", watched):
            await pilot.press("c")
            await pilot.pause()
            app.screen.query_one(Composer).query_one(TextArea).text = "rerun shard 4"
            await pilot.press("ctrl+s")
            await settle(app, pilot)

    assert callers, "nothing was sent"
    assert ui_thread not in callers
    (sent,) = list((channel_root / "inbox").glob("*.md"))
    assert sent.read_text() == "rerun shard 4\n"


async def test_cleaning_conflicts_never_blocks_the_ui(channel_root):
    """Deleting a sync client's leftovers is I/O on that client's own volume."""
    conflict = channel_root / "notifications" / "STATUS (conflicted copy 2026-09-05).md"
    conflict.write_text("stale\n", encoding="utf-8")
    app = make_app(channel_root)
    callers: list[int] = []
    real_remove = channel_module.remove_conflicts

    def watched(*args, **kwargs):
        callers.append(threading.get_ident())
        return real_remove(*args, **kwargs)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        ui_thread = threading.get_ident()
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        with mock.patch("inzaghi.ui.app.remove_conflicts", watched):
            await pilot.press("k")
            await pilot.pause()
            await pilot.press("y")
            await settle(app, pilot)

    assert callers, "nothing was cleaned"
    assert ui_thread not in callers
    assert not conflict.exists()


# -- discovery while running ---------------------------------------------


def root_app(root: Path) -> InzaghiApp:
    """An app that scans ``root`` for channels, the way the real config does."""
    return InzaghiApp(Config(roots=[RootSpec(path=root, depth=1)]))


async def rediscover(app, pilot):
    app.rediscover()
    await settle(app, pilot)
    await settle(app, pilot)


async def test_a_new_channel_appears_without_a_restart(channel_root, tmp_path):
    app = root_app(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert len(app.query(ChannelPane)) == 1

        init_channel(tmp_path / "southwind")
        await rediscover(app, pilot)

        assert {c.name for c in app.channels} == {"northwind", "southwind"}
        assert len(app.query(ChannelPane)) == 2
        assert app.query_one(DataTable).row_count == 2


async def test_a_deleted_channel_loses_its_tab(channel_root, tmp_path):
    app = root_app(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        init_channel(tmp_path / "southwind")
        await rediscover(app, pilot)

        rmtree(tmp_path / "southwind")
        await rediscover(app, pilot)

        assert [c.name for c in app.channels] == ["northwind"]
        assert len(app.query(ChannelPane)) == 1


async def test_an_unmounted_root_does_not_take_the_tabs_with_it(channel_root, tmp_path):
    """Discovery cannot see the difference; the missing root is the tell."""
    app = root_app(tmp_path / "volume")
    (tmp_path / "volume").mkdir()
    (channel_root).rename(tmp_path / "volume" / "northwind")
    async with app.run_test() as pilot:
        await rediscover(app, pilot)
        assert len(app.query(ChannelPane)) == 1

        (tmp_path / "volume").rename(tmp_path / "volume-gone")  # the disk goes away
        await rediscover(app, pilot)

        assert [c.name for c in app.channels] == ["northwind"]
        assert len(app.query(ChannelPane)) == 1


async def test_refresh_binding_picks_up_a_new_channel(channel_root, tmp_path):
    app = root_app(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        init_channel(tmp_path / "southwind")
        await pilot.press("r")
        await settle(app, pilot)
        await settle(app, pilot)
        assert len(app.query(ChannelPane)) == 2


async def test_flipping_read_only_in_the_config_takes_effect(channel_root, tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f'[[channels]]\npath = "{channel_root}"\nname = "northwind"\nread_only = true\n'
    )
    monkeypatch.setenv("INZAGHI_CONFIG", str(config_file))
    app = InzaghiApp(Config.load(config_file))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.channels[0].read_only is True

        config_file.write_text(
            f'[[channels]]\npath = "{channel_root}"\nname = "northwind"\nread_only = false\n'
        )
        await rediscover(app, pilot)
        assert app.channels[0].read_only is False


async def test_a_broken_config_is_ignored_rather_than_emptying_the_app(channel_root, tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(f'[[channels]]\npath = "{channel_root}"\n')
    app = InzaghiApp(Config.load(config_file))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        config_file.write_text("[[channels]\npath = broken")
        await rediscover(app, pilot)
        assert len(app.query(ChannelPane)) == 1


def test_the_cli_reports_the_name_it_was_invoked_as(monkeypatch):
    """Both `inzaghi` and the `inz` alias should quote themselves in usage."""
    from inzaghi import cli

    monkeypatch.setattr(cli.sys, "argv", ["inz", "ls"])
    assert cli._prog() == "inz"
    monkeypatch.setattr(cli.sys, "argv", ["/usr/local/bin/inzaghi"])
    assert cli._prog() == "inzaghi"


def test_module_invocation_falls_back_to_the_full_name(monkeypatch):
    from inzaghi import cli

    monkeypatch.setattr(cli.sys, "argv", ["__main__.py"])
    assert cli._prog() == "inzaghi"


# -- a config with no roots at all ---------------------------------------


def channels_only_app(*roots_of_channels: Path) -> InzaghiApp:
    """The config shape someone writes when they just list their projects."""
    return InzaghiApp(
        Config(channels=[ChannelSpec(path=p, name=p.name) for p in roots_of_channels])
    )


async def test_channels_work_with_no_roots_configured(channel_root, tmp_path):
    second = init_channel(tmp_path / "southwind").channel.root
    app = channels_only_app(channel_root, second)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert {c.name for c in app.channels} == {"northwind", "southwind"}
        assert app.query_one(DataTable).row_count == 2


async def test_an_explicit_channel_survives_its_volume_going_away(channel_root, tmp_path):
    """The unmount guard must cover listed channels, not only scanned roots."""
    volume = tmp_path / "volume"
    volume.mkdir()
    second = init_channel(volume / "southwind").channel.root
    app = channels_only_app(channel_root, second)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert len(app.query(ChannelPane)) == 2

        volume.rename(tmp_path / "volume-gone")  # the disk holding one of them
        await rediscover(app, pilot)

        assert {c.name for c in app.channels} == {"northwind", "southwind"}


async def test_a_genuinely_deleted_explicit_channel_is_dropped(channel_root, tmp_path):
    second = init_channel(tmp_path / "southwind").channel.root
    app = channels_only_app(channel_root, second)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        rmtree(second)
        await rediscover(app, pilot)
        assert [c.name for c in app.channels] == ["northwind"]


async def test_a_half_synced_channel_keeps_its_tab(channel_root, tmp_path):
    """Present but incomplete reads as mid-sync, not as deleted."""
    second = init_channel(tmp_path / "southwind").channel.root
    app = channels_only_app(channel_root, second)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        rmtree(second / "notifications")
        await rediscover(app, pilot)
        assert len(app.channels) == 2


# -- the inline composer --------------------------------------------------


async def test_the_composer_leaves_the_reader_on_screen(channel_root):
    """The point of it being inline: nothing is covered while you write."""
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, type(app.screen))  # no modal was pushed
        assert app.screen.query_one(Composer).display is True
        assert app.screen.query_one("#reader").display is True
        assert app.screen.query_one("#timeline", OptionList).display is True


async def test_a_draft_survives_going_back_to_read_something(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        composer = app.screen.query_one(Composer)
        composer.query_one(TextArea).text = "half a thought"

        await pilot.press("escape")  # back to the list
        await pilot.pause()
        assert app.screen.query_one("#timeline", OptionList).has_focus
        assert composer.display is True and composer.text == "half a thought"

        await pilot.press("down", "down")  # read something else
        await pilot.press("c")
        await pilot.pause()
        assert composer.query_one(TextArea).has_focus
        assert composer.text == "half a thought"


async def test_discard_empties_the_draft(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        composer = app.screen.query_one(Composer)
        composer.query_one(TextArea).text = "never mind"
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert composer.display is False and composer.text == ""


async def test_sending_closes_the_composer_and_clears_it(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        composer = app.screen.query_one(Composer)
        composer.query_one(TextArea).text = "please rerun shard 4"
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        assert composer.display is False and composer.text == ""
        (sent,) = list((channel_root / "inbox").glob("*.md"))
        assert sent.read_text() == "please rerun shard 4\n"


async def test_an_empty_draft_sends_nothing(channel_root):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        assert list((channel_root / "inbox").glob("*.md")) == []
        assert app.screen.query_one(Composer).display is True


async def test_a_failed_send_keeps_the_draft_to_retry(channel_root, monkeypatch):
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        composer = app.screen.query_one(Composer)
        composer.query_one(TextArea).text = "worth keeping"

        def boom(*args, **kwargs):
            raise OSError("volume went away")

        monkeypatch.setattr("inzaghi.ui.app.composer.send", boom)
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        assert composer.display is True and composer.text == "worth keeping"


# -- the bird -------------------------------------------------------------


async def test_the_pigeon_appears_when_there_is_room_to_spare(channel_root):
    app = make_app(channel_root)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert app.screen.query_one("#pigeon-dock").display is True


async def test_the_pigeon_stands_down_in_a_short_terminal(channel_root):
    app = make_app(channel_root)
    async with app.run_test(size=(120, 18)) as pilot:
        await settle(app, pilot)
        assert app.screen.query_one("#pigeon-dock").display is False


def test_the_blink_is_the_same_bird_with_its_eye_shut():
    """Blinking must not move anything: _fit_pigeon sizes the dock from PIGEON."""
    assert PIGEON_BLINK != PIGEON, "the eye pattern no longer matches the drawing"
    assert len(PIGEON_BLINK.splitlines()) == PIGEON_HEIGHT
    assert max(len(line) for line in PIGEON_BLINK.splitlines()) == PIGEON_WIDTH


async def test_the_pigeon_blinks_and_opens_its_eye_again(channel_root):
    app = make_app(channel_root)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        overview = app.query_one(OverviewPane)
        eye = app.screen.query_one("#pigeon", Static)

        overview._blink()
        await pilot.pause()
        assert str(eye.content) == PIGEON_BLINK

        overview._open_eye()
        await pilot.pause()
        assert str(eye.content) == PIGEON


async def test_a_bird_nobody_can_see_does_not_blink(channel_root):
    """Decoration should not cost a redraw off screen."""
    app = make_app(channel_root)
    async with app.run_test(size=(120, 18)) as pilot:
        await settle(app, pilot)
        assert app.screen.query_one("#pigeon-dock").display is False

        app.query_one(OverviewPane)._blink()
        await pilot.pause()
        assert str(app.screen.query_one("#pigeon", Static).content) == PIGEON


def test_a_pane_that_has_not_composed_yet_survives_a_refresh(channel_root):
    """Both refreshes fire on timers, and mounting a dozen panes is not instant.

    The regression: a config naming enough channels leaves a pane created but
    still empty when the one-second tick reaches it, and the query for a child
    that does not exist yet took the whole app down.
    """
    channel = channel_module.Channel(root=channel_root)
    snapshot = channel.scan()
    now = snapshot.scanned_at

    pane = ChannelPane(channel)
    pane.update(snapshot, set(), now)
    pane.update_strip(now)
    assert pane.snapshot is snapshot  # kept, to be written out once mounted

    OverviewPane().update([channel], {channel.key: snapshot}, {}, now)


async def test_the_first_tick_can_beat_the_tabs_onto_the_screen(channel_root, monkeypatch):
    """The regression, one level up: the tick fired before any tab existed.

    Driven by making compose() produce nothing, which is what a slow mount
    looks like from the timer's point of view.
    """
    app = make_app(channel_root)
    monkeypatch.setattr(InzaghiApp, "compose", lambda self: iter(()))
    async with app.run_test() as pilot:
        app._tick()  # would have taken the app down
        app._refresh_widgets(datetime.now().astimezone())
        await pilot.pause()


async def test_the_pigeon_never_costs_a_channel_a_row(tmp_path):
    """Decoration yields to data: enough channels and the bird goes away."""
    from inzaghi.protocol import init_channel

    roots = [init_channel(tmp_path / f"chan{i:02d}").channel.root for i in range(14)]
    app = channels_only_app(*roots)
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(app, pilot)
        assert app.query_one(DataTable).row_count == 14
        assert app.screen.query_one("#pigeon-dock").display is False


# -- moving between channels ---------------------------------------------


def two_channels(tmp_path) -> InzaghiApp:
    from inzaghi.protocol import init_channel

    roots = [init_channel(tmp_path / name).channel.root for name in ("alpha", "beta")]
    return channels_only_app(*roots)


def active(app) -> str:
    return app.query_one("#tabs", TabbedContent).active


async def test_the_arrows_step_through_the_tabs(tmp_path):
    app = two_channels(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert active(app) == OVERVIEW_ID

        await pilot.press("right")
        assert active(app) == "ch0"
        await pilot.press("right")
        assert active(app) == "ch1"
        await pilot.press("right")
        assert active(app) == OVERVIEW_ID, "the end wraps, the way ] does"
        await pilot.press("left")
        assert active(app) == "ch1"


async def test_the_arrows_work_wherever_the_keyboard_is(tmp_path):
    """Two of the widgets that hold focus bind these keys themselves: the
    overview table, to a column move its row cursor does not make, and the
    reader, to a sideways scroll a vertical-only pane does not have. Both hand
    them back -- Textual bubbles a key whose action would do nothing. Guarded
    because the day the reader gains a horizontal scrollbar it stops."""
    app = two_channels(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.focused is app.query_one(DataTable)
        await pilot.press("right")
        assert active(app) == "ch0"

        reader = app.screen.query_one("#reader")
        reader.focus()
        await pilot.pause()
        assert app.focused is reader
        await pilot.press("right")
        assert active(app) == "ch1"


async def test_the_arrows_stay_out_of_a_draft(tmp_path):
    """In a draft they are a cursor. Nobody expects the channel to change
    under a half-typed message."""
    app = two_channels(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("right")
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("p", "a", "u", "s", "e", "left", "left", "right")
        assert active(app) == "ch0"
        assert app.screen.query_one(TextArea).text == "pause"


async def test_the_arrows_stay_out_of_the_search_box(tmp_path):
    app = two_channels(tmp_path)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("right")
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("s", "h", "a", "r", "d", "left", "right")
        assert active(app) == "ch0"
        assert app.screen.query_one("#search", Input).value == "shard"


async def test_the_arrows_move_between_a_dialogs_buttons(channel_root):
    """Not the tabs behind it: this dialog is asking whether to stop a run."""
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("right")
        await pilot.press("x")  # STOP, which asks first
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)

        await pilot.press("right")
        await pilot.pause()
        assert app.focused.id == "no"
        await pilot.press("left")
        await pilot.pause()
        assert app.focused.id == "yes"
        assert active(app) == "ch0", "the tab moved behind the dialog"

        await pilot.press("escape")
        await settle(app, pilot)
        assert list((channel_root / "inbox").glob("*.md")) == []
