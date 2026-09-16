"""The four-step cycle that moves a channel over ssh.

The order is the whole point, so most of this is about it: retirement has to
land between the receipts arriving and the outbox going up. Run the push
first and a message the session already acted on goes back up for it to act
on again.

The "remote" here is a local directory. rsync treats a path with no colon as
local, so the cycle runs for real -- flags, ordering, deletions and all --
without a host on the other end.
"""

from __future__ import annotations

import fcntl
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import TZ, write
from inzaghi import transport
from inzaghi.channel import Channel, ReadOnlyChannel
from inzaghi.config import ChannelSpec, Config
from inzaghi.protocol import init_channel

PICKED_UP = "2026-09-05_1200_"


@pytest.fixture
def far(tmp_path: Path) -> Path:
    """The session's end: what a remote channel folder looks like."""
    return init_channel(tmp_path / "far" / "northwind").channel.root


@pytest.fixture
def near(tmp_path: Path) -> Path:
    """This end's mirror, deliberately left bare -- the first sync builds it."""
    return tmp_path / "near" / "northwind"


@pytest.fixture
def channel(near: Path, far: Path, tmp_path: Path) -> Channel:
    return Channel(
        root=near,
        name="northwind",
        remote=str(far),
        sync_marker=tmp_path / "state" / "northwind.synced",
        sync_interval=timedelta(seconds=300),
    )


def sent(root: Path, name: str, *, when: datetime | None = None) -> Path:
    """A message sitting in an inbox, with a send time worth preserving."""
    path = write(root / "inbox" / name, "PAUSE\n")
    stamp = (when or datetime(2026, 9, 5, 11, 30, tzinfo=TZ)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


# -- the order --------------------------------------------------------------


def test_retirement_lands_between_the_receipts_and_the_outbox(channel):
    """The regression this file exists for: push before retire is a re-pickup."""
    sent(channel.root, "2026-09-05_1130_pause.md")
    write(channel.done_dir / f"{PICKED_UP}2026-09-05_1130_pause.md", "PAUSE\n")

    seen: list[tuple[list[str], list[str]]] = []

    def run(command):
        inbox = sorted(p.name for p in channel.inbox_dir.glob("*.md"))
        seen.append((list(command), inbox))
        return 0, ""

    transport.sync(channel, run=run)

    # Three legs: notifications down, done/ down, inbox up.
    assert len(seen) == 3
    (_, before_pull), (_, at_receipts), (push, at_push) = seen
    assert before_pull == at_receipts == ["2026-09-05_1130_pause.md"]
    assert at_push == [], "the push saw a message the session had already taken"
    assert push[-1].endswith("/inbox/")


def test_every_leg_preserves_mtimes_and_never_leaves_a_half_file(channel):
    legs: list[list[str]] = []
    transport.sync(channel, run=lambda command: (legs.append(list(command)) or (0, "")))
    for leg in legs:
        assert "-a" in leg, leg  # a send time is an mtime
        assert "--partial" not in leg, leg  # would expose a half file by its final name


def test_only_the_session_owned_subtrees_are_deleted_into(channel):
    legs: list[list[str]] = []
    transport.sync(channel, run=lambda command: (legs.append(list(command)) or (0, "")))
    deleting = [leg for leg in legs if "--delete" in leg]
    assert len(deleting) == 1
    assert deleting[0][-1].endswith("/notifications/")
    for leg in legs:
        if leg[-1].endswith("/inbox/") or "/inbox/done/" in leg[-2]:
            assert "--delete" not in leg, leg


# -- for real, over a local "remote" ---------------------------------------


def test_a_first_sync_builds_the_mirror(channel, far):
    write(far / "notifications" / "HEARTBEAT.md", "- **state:** reindexing\n")
    write(far / "notifications" / "attachments" / "r.pdf", "%PDF\n")

    transport.sync(channel)

    assert (channel.root / "notifications" / "HEARTBEAT.md").read_text() != ""
    assert (channel.root / "notifications" / "attachments" / "r.pdf").is_file()
    assert channel.scan().heartbeat is not None


def test_a_message_reaches_the_session_with_its_send_time_intact(channel, far):
    """The mtime is the only record of when it was written."""
    when = datetime(2026, 9, 5, 11, 30, tzinfo=TZ)
    sent(channel.root, "2026-09-05_1130_pause.md", when=when)

    transport.sync(channel)

    landed = far / "inbox" / "2026-09-05_1130_pause.md"
    assert landed.is_file()
    assert datetime.fromtimestamp(landed.stat().st_mtime, TZ) == when


def test_a_picked_up_message_is_not_delivered_twice(channel, far):
    """The full round trip: send, session consumes, and it must not come back."""
    sent(channel.root, "2026-09-05_1130_pause.md")
    transport.sync(channel)
    assert (far / "inbox" / "2026-09-05_1130_pause.md").is_file()

    # The session does what the contract says: move it, keeping its mtime.
    moved = far / "inbox" / "done" / f"{PICKED_UP}2026-09-05_1130_pause.md"
    (far / "inbox" / "2026-09-05_1130_pause.md").rename(moved)

    transport.sync(channel)

    assert not (channel.root / "inbox" / "2026-09-05_1130_pause.md").exists()
    assert not (far / "inbox" / "2026-09-05_1130_pause.md").exists()
    assert (channel.root / "inbox" / "done" / moved.name).is_file()

    transport.sync(channel)  # and it stays gone
    assert not (far / "inbox" / "2026-09-05_1130_pause.md").exists()


def test_a_message_the_session_has_not_read_survives_the_cycle(channel, far):
    """The other half of the hazard: --delete upward would eat this."""
    sent(channel.root, "2026-09-05_1130_pause.md")
    sent(channel.root, "2026-09-05_1145_rerun.md")
    transport.sync(channel)
    (far / "inbox" / "2026-09-05_1130_pause.md").rename(
        far / "inbox" / "done" / f"{PICKED_UP}2026-09-05_1130_pause.md"
    )

    transport.sync(channel)

    assert (far / "inbox" / "2026-09-05_1145_rerun.md").is_file()
    assert (channel.root / "inbox" / "2026-09-05_1145_rerun.md").is_file()


def test_the_session_keeps_its_own_copy_of_done(channel, far):
    """done/ goes one way only; our mirror of it is never pushed back."""
    write(channel.done_dir / "2026-01-01_0000_ours.md", "local\n")
    transport.sync(channel)
    assert not (far / "inbox" / "done" / "2026-01-01_0000_ours.md").exists()


# -- retiring is a deletion, and is treated like one -----------------------


def test_retirement_matches_the_filename_exactly(channel):
    """Threading may be fuzzy about names. This ends in unlink, so it is not."""
    sent(channel.root, "2026-09-05_1130_pause.md")
    write(channel.done_dir / f"{PICKED_UP}pause.md", "a different message\n")
    write(channel.done_dir / "2026-09-05_1130_pause.md", "no pickup stamp\n")

    assert transport.retire(channel) == []
    assert (channel.root / "inbox" / "2026-09-05_1130_pause.md").is_file()


def test_retirement_ignores_directories_and_dotfiles(channel):
    (channel.inbox_dir / "notes").mkdir(parents=True)
    write(channel.done_dir / f"{PICKED_UP}notes", "")
    write(channel.inbox_dir / ".2026-09-05_1130_pause.md.tmp", "half a file\n")
    write(channel.done_dir / f"{PICKED_UP}.2026-09-05_1130_pause.md.tmp", "")

    assert transport.retire(channel) == []
    assert (channel.inbox_dir / "notes").is_dir()


def test_retirement_reports_what_it_removed(channel):
    for name in ("2026-09-05_1130_pause.md", "2026-09-05_1145_rerun.md"):
        sent(channel.root, name)
        write(channel.done_dir / f"{PICKED_UP}{name}", "PAUSE\n")
    assert transport.retire(channel) == [
        "2026-09-05_1130_pause.md",
        "2026-09-05_1145_rerun.md",
    ]


# -- the marker means a whole cycle got through ----------------------------


def test_the_marker_is_touched_only_when_every_leg_got_through(channel):
    result = transport.sync(channel)
    assert result.marked == channel.sync_marker
    assert channel.sync_marker.is_file()


def test_a_failed_leg_leaves_the_marker_alone(channel):
    """Otherwise the watcher would be told the link is fine when it is not."""
    def broken(command):
        return 23, "rsync: connection unexpectedly closed"

    with pytest.raises(transport.SyncFailed, match="connection unexpectedly closed"):
        transport.sync(channel, run=broken)
    assert not channel.sync_marker.exists()


def test_a_later_leg_failing_still_leaves_the_marker_alone(channel):
    calls = [0]

    def fails_last(command):
        calls[0] += 1
        return (0, "") if calls[0] < 3 else (12, "protocol error")

    with pytest.raises(transport.SyncFailed):
        transport.sync(channel, run=fails_last)
    assert not channel.sync_marker.exists()


def test_a_missing_rsync_is_reported_rather_than_raised_raw(channel, monkeypatch):
    def absent(command, **kwargs):
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(transport.subprocess, "run", absent)
    with pytest.raises(transport.SyncFailed) as raised:
        transport.sync(channel)
    assert "127" in str(raised.value) or "No such file" in str(raised.value)


# -- refusals ---------------------------------------------------------------


def test_the_marker_may_not_live_where_the_pull_rules(channel):
    """notifications/ is owned by the --delete pull, so a marker there is
    deleted every cycle and recreated at the end of the ones that get far
    enough -- reading as fresh forever and never able to report a problem."""
    inside = channel.notifications_dir / ".synced"
    with pytest.raises(ValueError, match="cannot live in notifications"):
        transport.sync(channel, marker=inside)


def test_the_marker_records_a_time_a_client_can_read_back(channel):
    """Written into the file, so no client has to trust a stat for it."""
    from inzaghi.model import Transport

    before = datetime.now().astimezone()
    transport.sync(channel)
    read_back = Transport.read(channel.sync_marker, timedelta(seconds=300))
    assert read_back.synced_at is not None
    assert read_back.synced_at >= before.replace(microsecond=0)
    assert read_back.health(datetime.now().astimezone()) == "fresh"


def test_a_read_only_channel_refuses_to_sync(channel):
    """Pulling writes into the folder, and read-only means untouched."""
    channel.read_only = True
    with pytest.raises(ReadOnlyChannel, match="read-only"):
        transport.sync(channel)


def test_a_channel_with_no_remote_says_so(near):
    with pytest.raises(ValueError, match="no remote"):
        transport.sync(Channel(root=near, name="northwind"))


def test_a_second_cycle_for_the_same_channel_stands_down(channel):
    """A 600 MB attachment outlasts a short cron; two rsyncs must not race."""
    lock = transport._lock_path(channel)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(transport.AlreadyRunning, match="already running"):
            transport.sync(channel)


def test_a_dry_run_touches_nothing(channel, far):
    sent(channel.root, "2026-09-05_1130_pause.md")
    result = transport.sync(channel, dry_run=True)
    assert len(result.commands) == 3
    assert not (far / "inbox" / "2026-09-05_1130_pause.md").exists()
    assert not channel.sync_marker.exists()
    assert result.retired == []


# -- more than one channel --------------------------------------------------


def test_one_unreachable_channel_does_not_stop_the_others(channel, near, tmp_path):
    """A sleeping laptop must not hold up the channels that are answering."""
    broken = Channel(root=tmp_path / "other", name="southwind", remote="")
    outcomes = transport.sync_all([broken, channel])
    assert isinstance(outcomes["southwind"], ValueError)
    assert isinstance(outcomes["northwind"], transport.Result)


def test_the_config_lists_the_channels_worth_syncing(tmp_path):
    config = Config(
        channels=[
            ChannelSpec(path=tmp_path / "a", name="a", remote="box:/chan/a"),
            ChannelSpec(path=tmp_path / "b", name="b"),
        ]
    )
    # Not filtered through is_channel: the first sync is what creates it.
    assert [c.name for c in config.syncable()] == ["a"]
    assert config.syncable()[0].remote == "box:/chan/a"
