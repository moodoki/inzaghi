from datetime import datetime, timedelta

import pytest

from conftest import NOW, TZ, write
from inzaghi.channel import Channel, is_channel
from inzaghi.config import scan_root


def test_scan_separates_singletons_from_the_log(channel):
    snap = channel.scan(now=NOW)
    assert set(snap.pinned) == {"HEARTBEAT.md", "STATUS.md", "TASK_OVERVIEW.md"}
    assert [e.kind for e in snap.events] == ["milestone", "ack"]


def test_events_are_newest_first(channel):
    events = channel.scan(now=NOW).events
    assert [e.ts for e in events] == sorted((e.ts for e in events), reverse=True)


def test_heartbeat_gives_a_deadline_and_a_cadence(channel):
    hb = channel.scan(now=NOW).heartbeat
    assert hb.next_by == datetime(2026, 9, 5, 6, 27, 46, tzinfo=TZ)
    assert hb.interval == timedelta(minutes=30)
    assert hb.state == "shard 3 of 8 reindexing on worker-2"


@pytest.mark.parametrize(
    "when, health",
    [
        (NOW, "fresh"),
        (datetime(2026, 9, 5, 6, 40, tzinfo=TZ), "late"),
        (datetime(2026, 9, 5, 8, 0, tzinfo=TZ), "stale"),
    ],
)
def test_health_degrades_past_the_deadline(channel, when, health):
    assert channel.scan(now=when).health(when) == health


def test_nothing_waiting_reads_as_nothing(channel):
    assert channel.scan(now=NOW).waiting is None
    assert channel.scan(now=NOW).attention(NOW) is False


def test_a_real_question_raises_attention(channel, channel_root):
    write(
        channel_root / "notifications" / "STATUS.md",
        "# status\n\n## Waiting on you\nPick a batch size for the next shard.\n",
    )
    snap = channel.scan(now=NOW)
    assert snap.waiting == "Pick a batch size for the next shard."
    assert snap.attention(NOW) is True


def test_a_hard_stop_raises_attention(channel, channel_root):
    write(
        channel_root / "notifications" / "2026-09-05_0100_hard-stop_needs-a-decision.md",
        "# [hard-stop] Which checkpoint?\n",
    )
    assert channel.scan(now=NOW).attention(NOW) is True


def test_sent_message_is_threaded_with_its_ack(channel):
    (thread,) = channel.scan(now=NOW).threads
    assert thread.state == "acked"
    assert thread.ack.kind == "ack"
    # Sent at 12:28 by mtime, picked up at 23:04 by the stamp added on the move.
    assert thread.picked_up == datetime(2026, 9, 4, 23, 4).astimezone()
    assert thread.round_trip == timedelta(hours=10, minutes=36)


def test_unanswered_message_in_inbox_is_in_flight(channel, channel_root):
    write(channel_root / "inbox" / "2026-09-05_0600_pause.md", "PAUSE\n")
    snap = channel.scan(now=NOW)
    assert [t.state for t in snap.threads] == ["in-flight", "acked"]
    assert len(snap.in_flight) == 1


def test_conflict_copies_are_quarantined_not_shown(channel, channel_root):
    write(channel_root / "notifications" / "STATUS (conflicted copy 2026-09-05).md", "# stale\n")
    snap = channel.scan(now=NOW)
    assert [p.name for p in snap.conflicts] == ["STATUS (conflicted copy 2026-09-05).md"]
    assert all("conflicted" not in e.path.name for e in snap.events)


def test_front_matter_overrides_the_filename(channel, channel_root):
    write(
        channel_root / "notifications" / "2026-09-05_0100_note_something.md",
        "---\nkind: error\nts: 2026-09-05T02:30:00+08:00\n---\n# Disk full\n",
    )
    event = channel.scan(now=NOW).events[0]
    assert event.kind == "error"
    assert event.ts == datetime(2026, 9, 5, 2, 30, tzinfo=TZ)


def test_missing_folders_do_not_raise(tmp_path):
    snap = Channel(root=tmp_path / "nope").scan(now=NOW)
    assert snap.events == [] and snap.heartbeat is None


def test_rescan_reuses_cached_documents(channel):
    channel.scan(now=NOW)
    cached = len(channel._cache)
    doc = channel.scan(now=NOW).events[0].doc
    assert len(channel._cache) == cached
    assert channel.scan(now=NOW).events[0].doc is doc


def test_discovery_finds_channels_under_a_root(channel_root, tmp_path):
    (tmp_path / "not_a_channel").mkdir()
    assert is_channel(channel_root)
    assert scan_root(tmp_path) == [channel_root]
