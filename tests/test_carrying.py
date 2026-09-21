"""Files a message carries into a channel's inbox.

The rule is the same at both ends -- a file nobody points at is not a delivery
-- but the work is on this side. A path that means something on this machine
means nothing on the one that reads it, so what a draft points at is copied
into ``inbox/attachments/`` and the draft's own reference is rewritten to
point at the copy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from inzaghi import compose, transport
from inzaghi.channel import Channel
from inzaghi.protocol import init_channel


@pytest.fixture
def channel(tmp_path) -> Channel:
    return init_channel(tmp_path / "northwind", "northwind").channel


@pytest.fixture
def shot(tmp_path) -> Path:
    path = tmp_path / "desk" / "shot.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x89PNG" + b"0" * 64)
    return path


def carried(channel: Channel) -> list[str]:
    folder = channel.inbox_dir / compose.ATTACHMENTS
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


# -- what counts as pointing at a file ------------------------------------


def test_a_path_on_a_line_of_its_own_is_a_delivery(shot):
    found = compose.attaching(f"Have a look:\n\n{shot}\n")
    assert [item.source for item in found] == [shot]


def test_a_link_to_a_local_file_is_a_delivery(shot):
    found = compose.attaching(f"Have a look at [the screenshot]({shot}).")
    assert [(item.source, item.label) for item in found] == [(shot, "the screenshot")]


def test_a_path_in_the_middle_of_a_sentence_is_prose(shot):
    assert compose.attaching(f"The trace is in {shot} if you want it.") == []


def test_a_path_that_names_nothing_here_is_prose():
    """A message may perfectly well discuss a file on the session's machine."""
    assert compose.attaching("Rerun /opt/run/sweep.sh with --fast.\n") == []


def test_a_path_inside_a_code_fence_is_being_quoted(shot):
    assert compose.attaching(f"```\n{shot}\n```\n") == []


def test_the_same_file_twice_is_carried_once(shot):
    found = compose.attaching(f"{shot}\n\n[again]({shot})\n")
    assert len(found) == 1


def test_a_folder_is_refused_with_a_reason(tmp_path):
    (tmp_path / "logs").mkdir()
    [item] = compose.attaching(f"{tmp_path / 'logs'}\n")
    assert not item.sendable
    assert "folder" in item.problem


def test_a_file_over_the_ceiling_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(compose, "MAX_ATTACHMENT_BYTES", 1024)
    big = tmp_path / "core.dump"
    big.write_bytes(b"0" * 2048)
    [item] = compose.attaching(f"{big}\n")
    assert not item.sendable
    assert "limit" in item.problem


def test_the_ceiling_is_twenty_five_megabytes():
    assert compose.MAX_ATTACHMENT_BYTES == 25 * 1024 * 1024


# -- copying and rewriting ------------------------------------------------


def test_sending_copies_the_file_and_rewrites_the_link(channel, shot):
    path = compose.send(channel, f"Have a look at [the screenshot]({shot}).")
    assert carried(channel) == ["shot.png"]
    assert "[the screenshot](attachments/shot.png)" in path.read_text()
    assert str(shot) not in path.read_text()


def test_a_bare_path_becomes_a_link_naming_the_file(channel, shot):
    path = compose.send(channel, f"Have a look:\n\n{shot}\n")
    assert "[shot.png](attachments/shot.png)" in path.read_text()


def test_the_copy_is_the_bytes_that_were_there(channel, shot):
    compose.send(channel, f"{shot}\n")
    copy = channel.inbox_dir / compose.ATTACHMENTS / "shot.png"
    assert copy.read_bytes() == shot.read_bytes()


def test_a_second_file_of_the_same_name_does_not_overwrite_the_first(channel, shot, tmp_path):
    compose.send(channel, f"{shot}\n")
    other = tmp_path / "elsewhere" / "shot.png"
    other.parent.mkdir()
    other.write_bytes(b"different")
    path = compose.send(channel, f"{other}\n")
    assert carried(channel) == ["shot-2.png", "shot.png"]
    assert "attachments/shot-2.png" in path.read_text()


def test_a_refused_file_refuses_the_whole_send(channel, tmp_path):
    (tmp_path / "logs").mkdir()
    with pytest.raises(ValueError, match="folder"):
        compose.send(channel, f"Apply these:\n\n{tmp_path / 'logs'}\n")
    assert list(channel.inbox_dir.glob("*.md")) == []
    assert carried(channel) == []


def test_a_message_that_could_not_be_written_leaves_no_payload(channel, shot, monkeypatch):
    """A payload nobody points at is invisible, and still being synced."""
    monkeypatch.setattr(compose, "_atomic_write", _boom)
    with pytest.raises(OSError):
        compose.send(channel, f"{shot}\n")
    assert carried(channel) == []


def _boom(*args, **kwargs):
    raise OSError("the volume said no")


def test_a_message_with_nothing_to_carry_makes_no_folder(channel):
    compose.send(channel, "STATUS")
    assert not (channel.inbox_dir / compose.ATTACHMENTS).exists() or carried(channel) == []


def test_a_read_only_channel_carries_nothing(tmp_path, shot):
    channel = init_channel(tmp_path / "ro", "ro").channel
    channel.read_only = True
    with pytest.raises(compose.ReadOnlyChannel):
        compose.send(channel, f"{shot}\n")
    assert carried(channel) == []


# -- and afterwards -------------------------------------------------------


def test_a_retired_message_takes_its_payload_with_it(channel, shot):
    path = compose.send(channel, f"Have a look:\n\n{shot}\n")
    (channel.done_dir).mkdir(parents=True, exist_ok=True)
    (channel.done_dir / f"2026-09-21_1100_{path.name}").write_text("picked up")

    assert transport.retire(channel) == [path.name]
    assert carried(channel) == []


def test_a_payload_another_message_still_names_is_left_alone(channel, shot):
    first = compose.send(channel, f"{shot}\n")
    name = carried(channel)[0]
    later = channel.inbox_dir / "2026-09-21_1200_again.md"
    later.write_text(f"and again: [{name}](attachments/{name})\n")

    (channel.done_dir / f"2026-09-21_1100_{first.name}").write_text("picked up")
    assert transport.retire(channel) == [first.name]
    assert carried(channel) == [name]


def test_retiring_nothing_deletes_nothing(channel, shot):
    compose.send(channel, f"{shot}\n")
    assert transport.retire(channel) == []
    assert carried(channel) == ["shot.png"]


# -- the contract -------------------------------------------------------


def test_the_contract_and_the_skill_both_describe_the_folder():
    from inzaghi import skill
    from inzaghi.protocol import CHANNEL_README

    assert "inbox/attachments/" in CHANNEL_README
    assert "wait briefly" in CHANNEL_README
    text = (skill.SOURCE / "SKILL.md").read_text(encoding="utf-8")
    assert "inbox/attachments/" in text
    assert "wait briefly" in text.lower()


def test_init_makes_the_folder(tmp_path):
    result = init_channel(tmp_path / "fresh", "fresh")
    assert (result.channel.inbox_dir / compose.ATTACHMENTS).is_dir()


# -- through the composer -------------------------------------------------


async def test_a_draft_with_a_path_in_it_sends_the_file(channel_root, shot):
    from textual.widgets import TabbedContent, TextArea

    from inzaghi.ui.composer import Composer
    from test_ui import make_app, settle

    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        app.screen.query_one(Composer).query_one(TextArea).text = f"look at this:\n\n{shot}\n"
        await pilot.press("ctrl+s")
        await settle(app, pilot)

    (sent,) = list((channel_root / "inbox").glob("*.md"))
    assert "[shot.png](attachments/shot.png)" in sent.read_text()
    assert (channel_root / "inbox" / "attachments" / "shot.png").read_bytes() == shot.read_bytes()


async def test_the_head_line_says_what_the_draft_is_carrying(channel_root, shot):
    from textual.widgets import Static, TabbedContent, TextArea

    from inzaghi.ui.composer import Composer
    from test_ui import make_app, settle

    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        composer = app.screen.query_one(Composer)
        composer.query_one(TextArea).text = f"look at this:\n\n{shot}\n"
        await pilot.pause(0.5)
        await settle(app, pilot)

        assert "carrying 1 file" in composer.query_one("#composer-head", Static).visual.plain


async def test_the_head_line_says_why_a_file_cannot_go(channel_root, tmp_path):
    from textual.widgets import Static, TabbedContent, TextArea

    from inzaghi.ui.composer import Composer
    from test_ui import make_app, settle

    (tmp_path / "logs").mkdir()
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        composer = app.screen.query_one(Composer)
        composer.query_one(TextArea).text = f"{tmp_path / 'logs'}\n"
        await pilot.pause(0.5)
        await settle(app, pilot)

        assert "folder" in composer.query_one("#composer-head", Static).visual.plain


async def test_the_attach_key_writes_the_path_into_the_draft(channel_root, shot):
    from textual.widgets import Input, TabbedContent, TextArea

    from inzaghi.ui.composer import Composer
    from inzaghi.ui.modals import PromptScreen
    from test_ui import make_app, settle

    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert isinstance(app.screen, PromptScreen)

        app.screen.query_one(Input).value = str(shot)
        await pilot.press("enter")
        await pilot.pause()

        text = app.screen.query_one(Composer).query_one(TextArea).text
        assert f"[shot.png]({shot})" in text
