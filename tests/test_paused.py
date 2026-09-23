"""A session that goes quiet on purpose, and says so.

Three silences look identical in a folder: a dead session, a wedged one, and
one waiting on a usage limit it cannot do anything about. Only the session
knows which, so only the session can say -- and having said it, it is trusted
exactly as far as the moment it named.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from textual.widgets import DataTable, Static, TabbedContent

from conftest import TZ, write
from inzaghi.channel import Channel
from inzaghi.ui.channel_view import ChannelPane
from inzaghi.ui.rows import HEALTH_STYLE
from test_ui import make_app, settle

#: The fixture heartbeat is due at 06:27:46 and the fixture clock is 06:00.
DEADLINE = datetime(2026, 9, 5, 6, 27, 46, tzinfo=TZ)
BACK = datetime(2026, 9, 5, 9, 0, tzinfo=TZ)


def at(**offset) -> datetime:
    return DEADLINE + timedelta(**offset)


def pause(root, *, until: str, front_matter: bool = False) -> None:
    """Rewrite the fixture heartbeat as a session about to go quiet would."""
    body = """
# northwind session heartbeat

- **updated:** 2026-09-05T05:57:46+08:00
- **next update expected by:** 2026-09-05T06:27:46+08:00 (+30 min)
- **state:** usage limit reached
"""
    if front_matter:
        body = f"---\npaused_until: {until}\n---\n{body}"
    else:
        body += f"- **paused until:** {until}\n"
    write(root / "notifications" / "HEARTBEAT.md", body)


# -- what the file says ---------------------------------------------------


@pytest.mark.parametrize("front_matter", [False, True])
def test_a_pause_is_read_from_the_bullet_or_the_front_matter(channel_root, front_matter):
    pause(channel_root, until="2026-09-05T09:00:00+08:00", front_matter=front_matter)
    heartbeat = Channel(root=channel_root).scan(now=at(hours=1)).heartbeat
    assert heartbeat.paused_until == BACK


def test_a_channel_that_never_pauses_says_nothing(channel):
    assert channel.scan(now=at(minutes=-1)).heartbeat.paused_until is None


# -- what it is worth -----------------------------------------------------


def test_a_paused_session_is_paused_and_not_late(channel_root):
    pause(channel_root, until="2026-09-05T09:00:00+08:00")
    channel = Channel(root=channel_root)
    when = at(hours=1)  # half an hour past the deadline it promised
    assert channel.scan(now=when).health(when) == "paused"


def test_a_pause_that_has_run_out_is_worth_nothing(channel_root):
    """Said 09:00 and was not back: exactly as late as one that said nothing."""
    pause(channel_root, until="2026-09-05T09:00:00+08:00")
    channel = Channel(root=channel_root)
    when = BACK + timedelta(minutes=1)
    assert channel.scan(now=when).health(when) == "stale"


def test_a_paused_channel_does_not_ask_for_anyone(channel_root):
    pause(channel_root, until="2026-09-05T09:00:00+08:00")
    when = at(hours=1)
    snapshot = Channel(root=channel_root).scan(now=when)
    assert snapshot.attention(when, unread=set()) is False


def test_a_pause_outlives_a_link_that_stopped_arriving(channel_root, tmp_path):
    """A statement about 09:00 is not made less true by a sync that stopped."""
    pause(channel_root, until="2026-09-05T09:00:00+08:00")
    marker = tmp_path / "state" / "northwind.synced"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")
    stopped = (DEADLINE - timedelta(hours=1)).timestamp()
    os.utime(marker, (stopped, stopped))
    channel = Channel(
        root=channel_root, sync_marker=marker, sync_interval=timedelta(minutes=5)
    )
    when = at(hours=1)
    snapshot = channel.scan(now=when)
    assert snapshot.link_health(when) == "stale"
    assert snapshot.health(when) == "paused"

    after = BACK + timedelta(minutes=1)
    assert channel.scan(now=after).health(after) == "offline"


def test_the_paused_mark_is_its_own(channel_root):
    """Not the mark for a channel nobody can judge: this one has an answer."""
    assert HEALTH_STYLE["paused"] not in (
        HEALTH_STYLE["unknown"],
        HEALTH_STYLE["late"],
        HEALTH_STYLE["stale"],
    )


# -- what the screen says -------------------------------------------------


async def test_the_strip_says_when_the_session_is_back(channel_root, monkeypatch):
    pause(channel_root, until="2026-09-05T09:00:00+08:00")
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        pane = app.query_one(ChannelPane)
        pane.update_strip(at(hours=1))
        await pilot.pause()

        line = pane.query_one("#strip", Static).visual.plain
        assert "paused, back 09:00" in line
        assert "overdue" not in line


async def test_the_overview_says_it_too(channel_root):
    pause(channel_root, until="2026-09-05T09:00:00+08:00")
    app = make_app(channel_root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        table = app.query_one(DataTable)
        app.query_one("OverviewPane").update(
            app.channels, app.snapshots, app._unread_paths(), at(hours=1)
        )
        await pilot.pause()
        row = [str(cell) for cell in table.get_row_at(0)]
        assert HEALTH_STYLE["paused"][0] in row[0]
        assert "back 09:00" in row[3]


# -- what the contract says -----------------------------------------------


def test_the_contract_and_the_skill_both_explain_a_pause():
    from inzaghi import skill
    from inzaghi.protocol import CHANNEL_README

    assert "paused until:" in CHANNEL_README
    assert "paused_until" in CHANNEL_README
    text = (skill.SOURCE / "SKILL.md").read_text(encoding="utf-8")
    assert "paused until:" in text


def test_the_contract_says_who_should_write_the_heartbeat():
    from inzaghi import skill
    from inzaghi.protocol import CHANNEL_README

    assert "not the agent" in CHANNEL_README
    text = (skill.SOURCE / "SKILL.md").read_text(encoding="utf-8")
    assert "reports on your turn" in text
