"""Telling a broken link apart from a dead session.

From inside the folder the two are identical: nothing new arrives. The marker
is the one piece of evidence that comes from outside it, so every property
here is about which of the two gets blamed.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import TZ, write
from inzaghi.channel import Channel
from inzaghi.config import DEFAULT_SYNC_INTERVAL, Config
from inzaghi.model import Transport

#: The deadline the fixture's heartbeat promises.
DEADLINE = datetime(2026, 9, 5, 6, 27, 46, tzinfo=TZ)
INTERVAL = timedelta(seconds=DEFAULT_SYNC_INTERVAL)


def at(**offset) -> datetime:
    return DEADLINE + timedelta(**offset)


@pytest.fixture
def marker(tmp_path: Path) -> Path:
    """A marker path outside the channel, as a client-driven sync would use."""
    return tmp_path / "state" / "northwind.synced"


def touched(marker: Path, when: datetime) -> Path:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")
    os.utime(marker, (when.timestamp(), when.timestamp()))
    return marker


def watched(root: Path, marker: Path, interval: timedelta | None = INTERVAL) -> Channel:
    return Channel(root=root, name="northwind", sync_marker=marker, sync_interval=interval)


# -- which end gets blamed ------------------------------------------------


def test_a_link_that_is_keeping_up_leaves_the_blame_with_the_session(channel_root, marker):
    """The diagnosis this exists for: the folder is arriving, so silence is real."""
    now = at(minutes=10)
    touched(marker, now - timedelta(seconds=5))
    snapshot = watched(channel_root, marker).scan(now=now)
    assert snapshot.link_health(now) == "fresh"
    assert snapshot.session_health(now) == "late"
    assert snapshot.health(now) == "late"  # not offline; the session is the story


def test_a_stale_link_takes_the_blame_off_the_session(channel_root, marker):
    now = at(minutes=10)
    touched(marker, now - timedelta(hours=1))
    snapshot = watched(channel_root, marker).scan(now=now)
    assert snapshot.session_health(now) == "late"  # unchanged: the file says so
    assert snapshot.health(now) == "offline"  # but it is not evidence any more


def test_a_heartbeat_still_inside_its_window_is_left_alone(channel_root, marker):
    """A link that broke a minute ago has not made a fresh promise false."""
    now = at(minutes=-5)
    touched(marker, now - timedelta(hours=1))
    snapshot = watched(channel_root, marker).scan(now=now)
    assert snapshot.link_health(now) in {"late", "stale"}
    assert snapshot.health(now) == "fresh"


def test_a_channel_with_no_heartbeat_at_all_still_reports_the_link(channel_root, marker):
    (channel_root / "notifications" / "HEARTBEAT.md").unlink()
    now = at(minutes=10)
    touched(marker, now - timedelta(hours=1))
    snapshot = watched(channel_root, marker).scan(now=now)
    assert snapshot.session_health(now) == "unknown"
    assert snapshot.health(now) == "offline"


def test_being_unable_to_see_a_channel_asks_for_someone(channel_root, marker):
    now = at(minutes=10)
    touched(marker, now - timedelta(hours=1))
    snapshot = watched(channel_root, marker).scan(now=now)
    assert snapshot.health(now) == "offline"
    assert snapshot.attention(now, set()) is True


# -- absence of a promise is not a broken one -----------------------------


def test_a_channel_with_no_marker_behaves_exactly_as_before(channel_root):
    """The regression guard: watching nothing must change nothing."""
    now = at(minutes=10)
    plain = Channel(root=channel_root, name="northwind").scan(now=now)
    assert plain.transport is None
    assert plain.link_health(now) == "unknown"
    assert plain.health(now) == plain.session_health(now) == "late"


def test_a_marker_that_has_never_been_touched_is_not_a_failure(channel_root, marker):
    """A sync that has not run yet, or one not wired up to touch it."""
    now = at(minutes=10)
    snapshot = watched(channel_root, marker).scan(now=now)
    assert snapshot.transport is not None and snapshot.transport.synced_at is None
    assert snapshot.link_health(now) == "unknown"
    assert snapshot.health(now) == "late"  # no override without evidence


def test_a_marker_with_no_declared_cadence_yields_no_verdict(channel_root, marker):
    now = at(minutes=10)
    touched(marker, now - timedelta(days=2))
    snapshot = watched(channel_root, marker, interval=None).scan(now=now)
    assert snapshot.transport is not None
    assert (snapshot.transport.age(now) or timedelta()) > timedelta(days=1)
    assert snapshot.link_health(now) == "unknown"
    assert snapshot.health(now) == "late"


@pytest.mark.parametrize(
    "age, health",
    [
        (INTERVAL - timedelta(seconds=1), "fresh"),
        (INTERVAL + timedelta(seconds=30), "fresh"),  # inside the same grace
        (INTERVAL + timedelta(seconds=120), "late"),
        (INTERVAL * 2 + timedelta(seconds=1), "stale"),
    ],
)
def test_the_link_is_judged_against_its_own_cadence(marker, age, health):
    now = at(minutes=10)
    transport = Transport.read(touched(marker, now - age), INTERVAL)
    assert transport.health(now) == health


def test_the_marker_is_re_read_every_scan(channel_root, marker):
    """It moves without the folder moving, so it cannot ride the doc cache."""
    now = at(minutes=10)
    channel = watched(channel_root, marker)
    touched(marker, now - timedelta(hours=1))
    assert channel.scan(now=now).health(now) == "offline"
    touched(marker, now)
    assert channel.scan(now=now).health(now) == "late"


# -- configuration --------------------------------------------------------


def test_the_marker_is_configured_per_channel(tmp_path, monkeypatch):
    config = tmp_path / "inzaghi.toml"
    config.write_text(
        '[[channels]]\n'
        f'path = "{tmp_path / "northwind"}"\n'
        f'sync_marker = "{tmp_path / "northwind.synced"}"\n'
        "sync_interval_seconds = 120\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INZAGHI_CONFIG", str(config))
    (spec,) = Config.load(config).channels
    assert spec.sync_marker == tmp_path / "northwind.synced"
    assert spec.sync_interval_seconds == 120.0


def test_a_channel_entry_without_a_marker_gets_none(tmp_path):
    config = tmp_path / "inzaghi.toml"
    config.write_text(f'[[channels]]\npath = "{tmp_path / "northwind"}"\n', encoding="utf-8")
    (spec,) = Config.load(config).channels
    assert spec.sync_marker is None
    assert spec.sync_interval_seconds == DEFAULT_SYNC_INTERVAL


def test_discovery_hands_the_marker_to_the_channel(tmp_path):
    from inzaghi.protocol import init_channel

    root = init_channel(tmp_path / "northwind").channel.root
    config = tmp_path / "inzaghi.toml"
    config.write_text(
        f'[[channels]]\npath = "{root}"\nsync_marker = "{tmp_path / "m"}"\n'
        "sync_interval_seconds = 90\n",
        encoding="utf-8",
    )
    (channel,) = Config.load(config).discover()
    assert channel.sync_marker == tmp_path / "m"
    assert channel.sync_interval == timedelta(seconds=90)


# -- what a person is told ------------------------------------------------


def test_the_strip_names_the_link_when_it_is_the_problem(channel_root, marker):
    from inzaghi.ui.channel_view import _link_note

    now = at(minutes=10)
    touched(marker, now - timedelta(hours=1))
    snapshot = watched(channel_root, marker).scan(now=now)
    (note,) = _link_note(snapshot, now)
    assert "no sync for" in note


def test_the_strip_stays_quiet_while_the_link_is_fine(channel_root, marker):
    from inzaghi.ui.channel_view import _link_note

    now = at(minutes=10)
    touched(marker, now - timedelta(seconds=5))
    snapshot = watched(channel_root, marker).scan(now=now)
    (note,) = _link_note(snapshot, now)
    assert "no sync" not in note and "⇅" in note


def test_an_unwatched_channel_says_nothing_about_a_link(channel_root):
    from inzaghi.ui.channel_view import _link_note

    now = at(minutes=10)
    snapshot = Channel(root=channel_root).scan(now=now)
    assert _link_note(snapshot, now) == []


def test_the_overview_puts_the_link_where_the_state_would_go(channel_root, marker):
    from inzaghi.ui.overview import _row

    now = at(minutes=10)
    touched(marker, now - timedelta(hours=1))
    channel = watched(channel_root, marker)
    row = _row(channel, channel.scan(now=now), set(), now)
    assert "no sync for" in str(row[-1])
    assert "⇅" in str(row[0])


def test_a_waiting_question_still_outranks_the_link(channel_root, marker):
    from inzaghi.ui.overview import _row

    write(
        channel_root / "notifications" / "STATUS.md",
        "# status\n\n## Waiting on you\nWhich shard should I redo?\n",
    )
    now = at(minutes=10)
    touched(marker, now - timedelta(hours=1))
    channel = watched(channel_root, marker)
    row = _row(channel, channel.scan(now=now), set(), now)
    assert "Which shard" in str(row[-1])


# -- what a person is interrupted for -------------------------------------


def shouts(app, monkeypatch) -> list[tuple[str, str]]:
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app, "_shout", lambda message, severity="information": said.append((message, severity))
    )
    return said


def app_for(channel: Channel, marker: Path, **alerts):
    from inzaghi.config import Alerts, ChannelSpec, Config
    from inzaghi.ui.app import InzaghiApp

    config = Config(
        channels=[
            ChannelSpec(path=channel.root, name=channel.name, sync_marker=marker)
        ],
        alerts=Alerts(**alerts),
    )
    return InzaghiApp(config, channels=[channel])


def test_a_link_going_down_is_reported_as_a_link_going_down(channel_root, marker, monkeypatch):
    """Naming the heartbeat here would send someone to look at a healthy session."""
    channel = watched(channel_root, marker)
    app = app_for(channel, marker)
    said = shouts(app, monkeypatch)

    fresh = at(minutes=-5)
    touched(marker, fresh)
    app._alert(channel.key, channel.scan(now=fresh), fresh)  # establishes "fresh"
    assert said == []

    later = at(minutes=30)
    app._alert(channel.key, channel.scan(now=later), later)
    (message, severity) = said[-1]
    assert "synced" in message and "not current" in message
    assert "heartbeat" not in message
    assert severity == "warning"


def test_the_heartbeat_alert_does_not_fire_for_a_link_failure(channel_root, marker, monkeypatch):
    """Both alerts on: only the one that names the real culprit should speak."""
    channel = watched(channel_root, marker)
    app = app_for(channel, marker, heartbeat_late=True, sync_late=True)
    said = shouts(app, monkeypatch)

    fresh = at(minutes=-5)
    touched(marker, fresh)
    app._alert(channel.key, channel.scan(now=fresh), fresh)

    later = at(minutes=30)
    app._alert(channel.key, channel.scan(now=later), later)
    assert len(said) == 1
    assert "heartbeat" not in said[0][0]


def test_a_session_going_quiet_over_a_healthy_link_still_names_the_heartbeat(
    channel_root, marker, monkeypatch
):
    channel = watched(channel_root, marker)
    app = app_for(channel, marker, heartbeat_late=True)
    said = shouts(app, monkeypatch)

    fresh = at(minutes=-5)
    touched(marker, fresh)
    app._alert(channel.key, channel.scan(now=fresh), fresh)

    later = at(minutes=30)
    touched(marker, later)  # the link is fine; the session is not
    app._alert(channel.key, channel.scan(now=later), later)
    assert [m for m, _ in said] == [f"{channel.name}: no heartbeat since its deadline"]


def test_a_link_already_down_when_inzaghi_opened_is_backlog(channel_root, marker, monkeypatch):
    """Same rule as every other alert: only a transition is news."""
    channel = watched(channel_root, marker)
    app = app_for(channel, marker)
    said = shouts(app, monkeypatch)

    now = at(minutes=30)
    touched(marker, now - timedelta(hours=2))
    app._alert(channel.key, channel.scan(now=now), now)
    app._alert(channel.key, channel.scan(now=now), now)
    assert said == []
