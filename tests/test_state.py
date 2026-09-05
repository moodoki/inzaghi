from conftest import NOW, write
from inzaghi.config import ChannelSpec, Config, RootSpec
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


def test_a_notification_missing_from_one_scan_keeps_its_receipt(channel, channel_root, tmp_path):
    """A synced folder is allowed to be behind; absence is not evidence."""
    state = ReadState.load(tmp_path / "read.json")
    state.mark_all_read(channel.key, channel.scan(now=NOW))
    before = len(state.seen[channel.key])
    (channel_root / "notifications" / "2026-09-04_2325_milestone_shard-2-reindexed.md").unlink()
    channel._cache.clear()
    state.unread(channel.key, channel.scan(now=NOW))
    assert len(state.seen[channel.key]) == before


def test_forgetting_a_channel_drops_all_of_its_receipts(channel, tmp_path):
    state = ReadState.load(tmp_path / "read.json")
    state.mark_all_read(channel.key, channel.scan(now=NOW))
    state.forget(channel.key)
    assert channel.key not in state.seen


def test_forgetting_a_channel_that_was_never_seen_is_harmless(tmp_path):
    state = ReadState.load(tmp_path / "read.json")
    state.forget("/nowhere")
    assert state.seen == {} and state._dirty is False


# -- which channels a config still watches --------------------------------


def test_a_channel_under_a_root_is_watched_even_when_it_is_not_there(tmp_path):
    """The volume being unmounted is exactly when the receipts matter most."""
    config = Config(roots=[RootSpec(path=tmp_path / "sync", depth=1)])
    assert config.watches(tmp_path / "sync" / "northwind")


def test_a_channel_deeper_than_the_root_scans_is_not_watched(tmp_path):
    config = Config(roots=[RootSpec(path=tmp_path / "sync", depth=1)])
    assert not config.watches(tmp_path / "sync" / "nested" / "northwind")
    assert Config(roots=[RootSpec(path=tmp_path / "sync", depth=2)]).watches(
        tmp_path / "sync" / "nested" / "northwind"
    )


def test_an_explicit_channel_is_watched_and_a_stranger_is_not(tmp_path):
    config = Config(channels=[ChannelSpec(path=tmp_path / "northwind")])
    assert config.watches(tmp_path / "northwind")
    assert not config.watches(tmp_path / "southwind")


def test_a_corrupt_state_file_is_not_fatal(tmp_path):
    path = tmp_path / "read.json"
    path.write_text("{not json")
    assert ReadState.load(path).seen == {}
