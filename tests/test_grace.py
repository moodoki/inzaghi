"""Slack allowed past a promised heartbeat before it counts as late.

The heartbeat crosses a sync client to reach us, so a deadline that has only
just passed usually means the file is still in flight.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from conftest import TZ, write
from inzaghi.channel import Channel
from inzaghi.config import DEFAULT_HEARTBEAT_GRACE, Config
from inzaghi.model import DEFAULT_GRACE

DEADLINE = datetime(2026, 9, 5, 6, 27, 46, tzinfo=TZ)


def at(**offset) -> datetime:
    return DEADLINE + timedelta(**offset)


@pytest.mark.parametrize(
    "when, health",
    [
        (at(minutes=-1), "fresh"),
        (at(seconds=0), "fresh"),
        (at(seconds=30), "fresh"),   # inside the default grace
        (at(seconds=59), "fresh"),
        (at(seconds=61), "late"),    # outside it
        (at(minutes=90), "stale"),
    ],
)
def test_the_default_grace_is_sixty_seconds(channel, when, health):
    assert channel.scan(now=when).health(when) == health


def test_the_default_is_sixty_seconds_everywhere():
    assert DEFAULT_GRACE == timedelta(seconds=60)
    assert DEFAULT_HEARTBEAT_GRACE == 60.0


def test_a_longer_grace_holds_off_longer(channel_root):
    channel = Channel(root=channel_root, grace=timedelta(minutes=5))
    assert channel.scan(now=at(minutes=4)).health(at(minutes=4)) == "fresh"
    assert channel.scan(now=at(minutes=6)).health(at(minutes=6)) == "late"


def test_zero_grace_restores_the_strict_deadline(channel_root):
    channel = Channel(root=channel_root, grace=timedelta(0))
    assert channel.scan(now=at(seconds=1)).health(at(seconds=1)) == "late"


def test_grace_does_not_delay_the_stale_verdict_meaningfully(channel_root):
    """A session two intervals overdue is stale regardless of a small grace."""
    channel = Channel(root=channel_root, grace=timedelta(seconds=60))
    when = at(minutes=61)  # the fixture promises every 30 min
    assert channel.scan(now=when).health(when) == "stale"


def test_the_countdown_stays_truthful_inside_the_grace(channel):
    """Only the judgement is softened; the clock is still reported as it is."""
    when = at(seconds=30)
    snapshot = channel.scan(now=when)
    assert snapshot.health(when) == "fresh"
    assert snapshot.heartbeat.overdue_by(when) == timedelta(seconds=30)


def test_nothing_is_waiting_so_grace_keeps_it_off_the_attention_list(channel, channel_root):
    when = at(seconds=30)
    assert channel.scan(now=when).attention(when) is False
    assert channel.scan(now=at(seconds=90)).attention(at(seconds=90)) is True


# -- configuration --------------------------------------------------------


def test_the_grace_is_configurable(tmp_path, channel_root):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f'heartbeat_grace_seconds = 300\n\n[[channels]]\npath = "{channel_root}"\n'
    )
    config = Config.load(config_file)
    assert config.heartbeat_grace_seconds == 300.0
    (channel,) = config.discover()
    assert channel.grace == timedelta(seconds=300)
    assert channel.scan(now=at(minutes=4)).health(at(minutes=4)) == "fresh"


def test_an_unset_grace_falls_back_to_the_default(tmp_path, channel_root):
    config_file = tmp_path / "config.toml"
    config_file.write_text(f'[[channels]]\npath = "{channel_root}"\n')
    (channel,) = Config.load(config_file).discover()
    assert channel.grace == DEFAULT_GRACE


def test_the_channel_hands_its_grace_to_the_snapshot(channel_root):
    channel = Channel(root=channel_root, grace=timedelta(seconds=7))
    assert channel.scan(now=DEADLINE).grace == timedelta(seconds=7)


# -- alerting -------------------------------------------------------------


async def test_no_late_alert_is_raised_inside_the_grace(channel_root, monkeypatch):
    """The point of the whole thing: no alarm while the update is in flight."""
    from inzaghi.config import Alerts, ChannelSpec
    from inzaghi.ui.app import InzaghiApp
    from test_ui import settle

    config = Config(
        channels=[ChannelSpec(path=channel_root, name="northwind")],
        alerts=Alerts(heartbeat_late=True),
    )
    app = InzaghiApp(config)
    shouted: list[str] = []
    monkeypatch.setattr(InzaghiApp, "_shout", lambda self, msg, severity="information": shouted.append(msg))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        snapshot = app.snapshots[str(channel_root)]
        shouted.clear()  # the opening scan is backlog, not news

        app._known_health[str(channel_root)] = "fresh"
        app._alert(str(channel_root), snapshot, at(seconds=30))
        assert shouted == []

        app._alert(str(channel_root), snapshot, at(seconds=90))
        assert len(shouted) == 1 and "heartbeat" in shouted[0]


async def test_a_channel_already_late_on_startup_does_not_pop_an_alert(channel_root, monkeypatch):
    """Backlog is shown in the overview, not announced."""
    from inzaghi.config import Alerts, ChannelSpec
    from inzaghi.ui.app import InzaghiApp
    from test_ui import settle

    shouted: list[str] = []
    monkeypatch.setattr(
        InzaghiApp, "_shout", lambda self, msg, severity="information": shouted.append(msg)
    )
    app = InzaghiApp(
        Config(
            channels=[ChannelSpec(path=channel_root, name="northwind")],
            alerts=Alerts(heartbeat_late=True),
        )
    )
    async with app.run_test() as pilot:
        await settle(app, pilot)  # the fixture's deadline is long past
        assert app.snapshots[str(channel_root)].health() == "stale"
        assert shouted == []
