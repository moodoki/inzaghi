from conftest import NOW, write
from inzaghi.state import ReadState


def test_everything_is_unread_at_first(channel, tmp_path):
    state = ReadState.load(tmp_path / "read.json")
    snap = channel.scan(now=NOW)
    assert len(state.unread(channel.key, snap)) == len(snap.events)


def test_marking_read_sticks_across_a_reload(channel, tmp_path):
    path = tmp_path / "read.json"
    state = ReadState.load(path)
    snap = channel.scan(now=NOW)
    state.mark_all_read(channel.key, snap)
    state.save()
    assert ReadState.load(path).unread(channel.key, snap) == []


def test_a_rewritten_file_becomes_unread_again(channel, channel_root, tmp_path):
    state = ReadState.load(tmp_path / "read.json")
    state.mark_all_read(channel.key, channel.scan(now=NOW))
    write(
        channel_root / "notifications" / "2026-09-04_2325_milestone_shard-2-reindexed.md",
        "# [milestone] Shard 2 reindexed\n\nRewritten with more detail.\n",
    )
    channel._cache.clear()
    assert len(state.unread(channel.key, channel.scan(now=NOW))) == 1


def test_receipts_for_deleted_files_are_dropped(channel, channel_root, tmp_path):
    state = ReadState.load(tmp_path / "read.json")
    state.mark_all_read(channel.key, channel.scan(now=NOW))
    (channel_root / "notifications" / "2026-09-04_2325_milestone_shard-2-reindexed.md").unlink()
    channel._cache.clear()
    state.forget_missing(channel.key, channel.scan(now=NOW))
    assert len(state.seen[channel.key]) == 1


def test_a_corrupt_state_file_is_not_fatal(tmp_path):
    path = tmp_path / "read.json"
    path.write_text("{not json")
    assert ReadState.load(path).seen == {}
