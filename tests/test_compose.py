import errno
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


def test_nothing_half_written_is_ever_visible_in_the_inbox(channel, monkeypatch):
    """Reported from a live channel: atomic is not the same as invisible.

    The rename is atomic, but the temporary being renamed *from* is an entry
    like any other, and a session woken by the create event lists the folder
    at exactly that moment. So the listing is taken at the one instant it
    could go wrong -- while the rename is happening.
    """
    seen: list[list[str]] = []
    real_replace = compose.os.replace

    def watched(src, dst):
        seen.append(sorted(p.name for p in channel.inbox_dir.iterdir()))
        return real_replace(src, dst)

    monkeypatch.setattr(compose.os, "replace", watched)
    path = compose.send(channel, "PAUSE", now=NOW)

    assert seen, "the message was never renamed into place"
    assert seen[0] == ["done"], f"a temporary was visible in inbox/: {seen[0]}"
    assert sorted(p.name for p in channel.inbox_dir.iterdir()) == sorted(["done", path.name])


def test_the_staging_file_is_cleaned_up_when_the_write_fails(channel, monkeypatch):
    def refuse(src, dst):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(compose.os, "replace", refuse)
    with pytest.raises(OSError):
        compose.send(channel, "STOP", now=NOW)

    assert list(channel.inbox_dir.glob("*")) == [channel.inbox_dir / "done"]
    assert list(channel.root.glob(".inzaghi-*")) == []


def test_a_staging_directory_on_another_filesystem_falls_back(channel, monkeypatch):
    """EXDEV is not worth failing a send over: the contract's reading rule
    covers a temporary beside the target, which is where this lands."""
    calls: list[tuple[str, str]] = []
    real_replace = compose.os.replace

    def cross_device(src, dst):
        calls.append((str(src), str(dst)))
        if Path(src).parent == channel.root:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(compose.os, "replace", cross_device)
    path = compose.send(channel, "RESUME", now=NOW)

    assert path.read_text() == "RESUME\n"
    assert len(calls) == 2, "it did not try again beside the target"
    assert list(channel.root.glob(".inzaghi-*")) == []


def test_a_delivery_in_progress_is_not_read_as_a_message(channel):
    """The other half of the rule, in the implementation that states it."""
    (channel.inbox_dir / ".inzaghi-x9syq66e.partial").write_text("PAU")
    (channel.inbox_dir / ".2026-09-10_1403_pause.md.~7f1e135d").write_text("")
    (channel.inbox_dir / "2026-09-10_1403_pause.md").write_text("PAUSE\n")

    # The fixture already holds one message, picked up and moved to done/.
    in_flight = [t.sent.path.name for t in channel.scan(now=NOW).threads if t.state == "in-flight"]
    assert in_flight == ["2026-09-10_1403_pause.md"]
