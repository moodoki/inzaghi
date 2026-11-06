"""STATUS.md says what is on the other end: harness, provider, model."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Static, TabbedContent

from conftest import write
from inzaghi import parse
from inzaghi.model import Doc, Status
from inzaghi.protocol import CHANNEL_README
from inzaghi.ui.channel_view import ChannelPane
from test_ui import make_app, settle


def status(text: str) -> Status:
    meta, body = parse.split_front_matter(text)
    return Status.from_doc(Doc(Path("STATUS.md"), text, meta, body, 0.0, len(text)))


def test_front_matter_names_the_session():
    s = status("---\nharness: claude-code\nprovider: azure\nmodel: claude-opus-5-5\n---\n# s\n")
    assert (s.harness, s.provider, s.model) == ("claude-code", "azure", "claude-opus-5-5")
    assert s.session == "Claude Code · Azure · claude-opus-5-5"


def test_bullets_say_the_same_thing():
    s = status("# s\n\n- **harness:** opencode\n- **provider:** local\n- **model:** `qwen3-coder`\n")
    assert s.session == "opencode · local · qwen3-coder"


def test_front_matter_wins_over_a_bullet():
    s = status("---\nmodel: a\n---\n- **model:** b\n")
    assert s.model == "a"


def test_a_channel_with_no_agent_says_so():
    assert status("---\nharness: none\n---\n").session == "no agent"


def test_names_nobody_listed_are_shown_as_written():
    s = status("---\nharness: my-agent\nprovider: lab-gpu-box\nmodel: x-7b\n---\n")
    assert s.session == "my-agent · lab-gpu-box · x-7b"


def test_a_status_that_declares_nothing_shows_nothing():
    s = status("# s\n\n## Waiting on you\nNothing.\n")
    assert (s.harness, s.provider, s.model, s.session) == (None, None, None, "")


def test_the_contract_asks_for_all_three():
    """A key the contract advertises has to be read, and one that is read has
    to be advertised: the session cannot fill in what it was never told."""
    for key in ("harness:", "provider:", "model:"):
        assert key in CHANNEL_README


async def test_the_strip_shows_what_the_session_is(channel_root):
    write(
        channel_root / "notifications" / "STATUS.md",
        "---\nharness: claude-code\nprovider: anthropic\nmodel: claude-opus-5-5\n---\n"
        "# northwind\n\n## Waiting on you\nNothing.\n",
    )
    app = make_app(channel_root)
    async with app.run_test(size=(220, 50)) as pilot:
        await settle(app, pilot)
        app.query_one("#tabs", TabbedContent).active = "ch0"
        await pilot.pause()
        await settle(app, pilot)
        strip = app.query_one(ChannelPane).query_one("#strip", Static)
        assert "Claude Code · Anthropic · claude-opus-5-5" in strip.visual.plain
