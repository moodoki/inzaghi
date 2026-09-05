from datetime import datetime
from pathlib import Path

import pytest

from conftest import NOW
from inzaghi import compose
from inzaghi.channel import Channel
from inzaghi.compose import QUICK_BY_KEYWORD, ReadOnlyChannel


def test_send_writes_a_stamped_slugged_file(channel):
    path = compose.send(channel, "Pause the GPU jobs, please", now=datetime(2026, 9, 5, 11, 30))
    assert path.name == "2026-09-05_1130_pause-the-gpu-jobs-please.md"
    assert path.read_text() == "Pause the GPU jobs, please\n"
    assert path.parent == channel.inbox_dir


def test_sent_message_shows_up_as_in_flight(channel):
    compose.send(channel, "please rerun seed 3", now=NOW)
    assert [t.state for t in channel.scan(now=NOW).threads][0] == "in-flight"


def test_quick_action_sends_the_bare_keyword(channel):
    path = compose.send_quick(channel, QUICK_BY_KEYWORD["STATUS"], now=NOW)
    assert path.read_text() == "STATUS\n"
    assert path.name.endswith("_status.md")


def test_quick_action_can_carry_a_note(channel):
    path = compose.send_quick(channel, QUICK_BY_KEYWORD["PAUSE"], "before the 3am job", now=NOW)
    assert path.read_text() == "PAUSE\n\nbefore the 3am job\n"


def test_read_only_channel_refuses_to_be_written_to(channel_root):
    channel = Channel(root=channel_root, read_only=True)
    with pytest.raises(ReadOnlyChannel):
        compose.send(channel, "STOP")
    assert list(channel.inbox_dir.glob("*.md")) == []


def test_empty_message_is_refused(channel):
    with pytest.raises(ValueError):
        compose.send(channel, "   \n\n")


def test_two_sends_in_the_same_minute_do_not_collide(channel):
    first = compose.send(channel, "same text", now=NOW)
    second = compose.send(channel, "same text", now=NOW)
    assert first != second
    assert second.stem.endswith("-2")


def test_no_partial_file_is_left_behind(channel):
    compose.send(channel, "hello", now=NOW)
    assert [p.name for p in channel.inbox_dir.iterdir() if p.name.startswith(".")] == []


def test_a_failed_write_leaves_the_inbox_clean(channel, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("volume went away")

    monkeypatch.setattr(compose.os, "replace", boom)
    with pytest.raises(OSError):
        compose.send(channel, "hello", now=NOW)
    assert list(channel.inbox_dir.iterdir()) == [channel.done_dir]
