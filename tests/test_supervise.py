"""Poking a session that has stopped reading its inbox.

The supervisor exists because every in-session mechanism is started by the
session, and so cannot recover once a turn ends with nothing armed. It runs
outside them all, and the one thing it must never do is type into a session
that is asking its user a question.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from conftest import write
from inzaghi import supervise as sup
from inzaghi.channel import Channel
from inzaghi.config import ChannelSpec, Config

NOW = 1_800_000_000.0


@pytest.fixture
def supervised(channel_root):
    """The fixture channel, configured with a tmux pane to poke."""
    config = Config(
        channels=[ChannelSpec(path=channel_root, name="northwind", tmux="win:1.0")]
    )
    return config, Channel(root=channel_root, name="northwind")


def put(root: Path, name: str, age: float = 0.0, text: str = "do the thing\n") -> Path:
    path = write(root / "inbox" / name, text)
    stamp = NOW - age
    import os

    os.utime(path, (stamp, stamp))
    return path


class Recorder:
    """Stands in for the shell, so no test touches a real tmux."""

    def __init__(self, capture: str = "$ ", code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.capture = capture
        self.code = code

    def __call__(self, command):
        self.calls.append(list(command))
        if command[:2] == ["tmux", "capture-pane"]:
            return self.code, self.capture
        return self.code, ""


# -- what counts as a message ---------------------------------------------


def test_only_dotless_markdown_counts(channel_root):
    channel = Channel(root=channel_root)
    put(channel_root, "2026-09-23_1400_real.md")
    put(channel_root, ".2026-09-23_1400_partial.md.~7f1e135d")
    put(channel_root, ".inzaghi-x9syq66e.partial")
    put(channel_root, "notes.txt")
    assert [p.name for p in sup.messages(channel)] == ["2026-09-23_1400_real.md"]


def test_the_done_folder_is_not_a_message(channel_root):
    """It is a directory, and it is full of messages already dealt with."""
    channel = Channel(root=channel_root)
    assert sup.messages(channel) == []


def test_an_unreadable_inbox_is_not_evidence_of_anything(channel_root, monkeypatch):
    channel = Channel(root=channel_root)
    monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(OSError("gone")))
    assert sup.messages(channel) == []


def test_look_reports_the_count_and_the_oldest(channel_root):
    channel = Channel(root=channel_root)
    put(channel_root, "a.md", age=600)
    put(channel_root, "b.md", age=120)
    waiting = sup.look(channel)
    assert waiting.count == 2
    assert waiting.age(NOW) == pytest.approx(600, abs=2)


def test_a_clear_inbox_looks_like_nothing(channel_root):
    assert sup.look(Channel(root=channel_root)) is None


# -- when to step in ------------------------------------------------------


def waiting_for(channel_root, age: float) -> sup.Waiting:
    put(channel_root, "m.md", age=age)
    return sup.look(Channel(root=channel_root, name="northwind"))


def test_a_fresh_message_is_left_to_the_session(channel_root):
    """The session's own watcher should have it; poking is noise."""
    why = sup.due(waiting_for(channel_root, 30), None, NOW, after=180, every=900)
    assert "grace" in why


def test_an_old_message_with_nobody_watching_is_due(channel_root):
    assert sup.due(waiting_for(channel_root, 600), None, NOW, after=180, every=900) == ""


def test_a_channel_poked_recently_is_left_alone(channel_root):
    why = sup.due(waiting_for(channel_root, 6000), NOW - 60, NOW, after=180, every=900)
    assert "interval" in why


def test_the_interval_expires(channel_root):
    assert sup.due(waiting_for(channel_root, 6000), NOW - 1000, NOW, after=180, every=900) == ""


# -- the note it hands over -----------------------------------------------


def test_the_note_says_what_and_how_old(channel_root):
    put(channel_root, "2026-09-23_1400_fix-the-thing.md", age=7200)
    text = sup.note(sup.look(Channel(root=channel_root, name="northwind")), NOW)
    assert "northwind" in text and "1 unread message" in text
    assert "2h old" in text
    assert "2026-09-23_1400_fix-the-thing.md" in text
    assert "inbox/done/" in text


def test_the_note_does_not_list_a_hundred_names(channel_root):
    for n in range(5):
        put(channel_root, f"m{n}.md", age=7200)
    text = sup.note(sup.look(Channel(root=channel_root, name="northwind")), NOW)
    assert "5 unread messages" in text and "and 2 more" in text


# -- the thing it must never do -------------------------------------------


ASKING_PANES = [
    "Do you want to proceed?\n ❯ 1. Yes\n   2. No",
    "Auto mode classifier requires confirmation for this command.",
    "Allow this tool call? [y/N]",
    "Press Enter to continue",
]


@pytest.mark.parametrize("pane", ASKING_PANES)
def test_a_pane_asking_its_user_something_is_never_typed_into(pane):  # noqa: D401
    """A poke is keystrokes, and Enter after them answers whatever is asked.

    That decision belongs to the person the question was put to. This is the
    property the whole design rests on: the supervisor may interrupt work, and
    may never answer for anybody.
    """
    assert "not typing into it" in sup.read_pane("win:1.0", run=Recorder(pane)).refusal


def test_an_ordinary_prompt_is_fine():
    assert sup.read_pane("win:1.0", run=Recorder("❯ \n")).refusal == ""


def test_a_pane_that_cannot_be_read_is_refused():
    """If we cannot see what we are typing into, we do not type."""
    assert "cannot read pane" in sup.read_pane("win:1.0", run=Recorder("", code=1)).refusal


def test_the_message_is_typed_literally_then_submitted():
    """``send-keys`` reads its arguments as key names without ``-l``.

    A note containing the word Enter, or a bracketed token, would otherwise be
    pressed rather than typed.
    """
    recorder = Recorder()
    assert sup.tmux_poke("win:1.0", "press Enter [ok]", run=recorder) == ""
    assert recorder.calls == [
        ["tmux", "send-keys", "-t", "win:1.0", "-l", "--", "press Enter [ok]"],
        ["tmux", "send-keys", "-t", "win:1.0", "Enter"],
    ]


# -- a whole sweep --------------------------------------------------------


def test_a_channel_with_no_way_in_is_reported_not_skipped(channel_root, tmp_path):
    """Supervised and quiet must never look the same from outside."""
    config = Config(channels=[ChannelSpec(path=channel_root, name="northwind")])
    put(channel_root, "m.md", age=6000)
    (report,) = sup.sweep(config, now=NOW, pokes=sup.Pokes(tmp_path / "p.json"))
    assert report.action == "unsupervised"


def test_a_due_channel_is_poked_and_remembered(supervised, tmp_path):
    config, channel = supervised
    put(channel.root, "m.md", age=6000)
    pokes = sup.Pokes(tmp_path / "p.json")
    recorder = Recorder()

    (report,) = sup.sweep(config, now=NOW, pokes=pokes, run=recorder)

    assert report.action == "poked (mail)"
    assert ["tmux", "send-keys", "-t", "win:1.0", "Enter"] in recorder.calls
    assert pokes.at[channel.key] == NOW


def test_a_refused_pane_is_not_recorded_as_poked(supervised, tmp_path):
    """So the next sweep tries again once the question has been answered."""
    config, channel = supervised
    put(channel.root, "m.md", age=6000)
    pokes = sup.Pokes(tmp_path / "p.json")

    (report,) = sup.sweep(
        config, now=NOW, pokes=pokes, run=Recorder("Do you want to proceed?")
    )

    assert report.action == "refused"
    assert channel.key not in pokes.at


def test_a_quiet_channel_is_not_reported_at_all(supervised, tmp_path):
    config, _ = supervised
    assert sup.sweep(config, now=NOW, pokes=sup.Pokes(tmp_path / "p.json")) == []


def test_dry_run_types_nothing(supervised, tmp_path):
    """It still reads the pane -- a dry run that cannot see one cannot report
    that the poke would have been refused."""
    config, channel = supervised
    put(channel.root, "m.md", age=6000)
    recorder = Recorder()
    (report,) = sup.sweep(
        config, now=NOW, pokes=sup.Pokes(tmp_path / "p.json"), dry_run=True, run=recorder
    )
    assert report.action.startswith("would poke")
    assert all(call[:2] != ["tmux", "send-keys"] for call in recorder.calls)


def test_a_nudge_command_is_argv_with_substitutions(channel_root, tmp_path):
    """No shell: the config is the user's, but a message is a session's."""
    config = Config(
        channels=[
            ChannelSpec(
                path=channel_root,
                name="northwind",
                nudge=("notify", "--to", "{name}", "--count", "{count}", "--say", "{message}"),
            )
        ]
    )
    put(channel_root, "m.md", age=6000)
    recorder = Recorder()
    (report,) = sup.sweep(config, now=NOW, pokes=sup.Pokes(tmp_path / "p.json"), run=recorder)
    assert report.action == "poked (mail)"
    sent = recorder.calls[0]
    assert sent[:5] == ["notify", "--to", "northwind", "--count", "1"]
    assert "northwind" in sent[-1]


def test_a_failing_nudge_is_reported_and_not_remembered(channel_root, tmp_path):
    config = Config(
        channels=[ChannelSpec(path=channel_root, name="northwind", nudge=("false",))]
    )
    put(channel_root, "m.md", age=6000)
    pokes = sup.Pokes(tmp_path / "p.json")
    (report,) = sup.sweep(config, now=NOW, pokes=pokes, run=Recorder(code=1))
    assert report.action == "failed"
    assert pokes.at == {}


def test_only_the_named_channels_are_swept(supervised, tmp_path):
    config, channel = supervised
    put(channel.root, "m.md", age=6000)
    assert sup.sweep(config, names=["elsewhere"], now=NOW, pokes=sup.Pokes(tmp_path / "p.json")) == []


# -- remembering ----------------------------------------------------------


def test_pokes_round_trip(tmp_path):
    pokes = sup.Pokes(tmp_path / "p.json")
    pokes.record("a", NOW)
    pokes.save()
    assert sup.Pokes.load(tmp_path / "p.json").at == {"a": NOW}


def test_a_state_file_that_will_not_parse_starts_again(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{ not json", encoding="utf-8")
    assert sup.Pokes.load(path).at == {}


def test_junk_values_are_dropped_rather_than_crashing(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"at": {"a": "soon", "b": 1.0, "c": True}}), encoding="utf-8")
    assert sup.Pokes.load(path).at == {"b": 1.0}


def test_saving_nothing_writes_nothing(tmp_path):
    path = tmp_path / "p.json"
    sup.Pokes(path).save()
    assert not path.exists()


# -- staying up -----------------------------------------------------------


def test_a_sweep_that_raises_does_not_end_the_watch(supervised, monkeypatch):
    """It is meant to be left alone for weeks; one bad pass is not a reason to stop."""
    config, _ = supervised
    seen: list[sup.Report] = []
    passes = iter([1, 2])

    monkeypatch.setattr(sup, "sweep", _boom)
    monkeypatch.setattr(sup.time, "sleep", lambda _: None)
    sup.watch(config, on_report=seen.append, stop=lambda: next(passes, None) is None)

    assert [r.action for r in seen] == ["error", "error"]


def _boom(*args, **kwargs):
    raise RuntimeError("the volume said no")


# -- a session that stopped at a usage limit ------------------------------

LIMIT = "Claude usage limit reached. Your limit will reset at 3pm."


def test_a_stalled_session_is_roused_even_with_an_empty_inbox(supervised, tmp_path):
    """The case the watcher cannot cover: nothing has arrived and nothing will
    happen again until something asks."""
    config, channel = supervised
    pokes = sup.Pokes(tmp_path / "p.json")

    (report,) = sup.sweep(config, now=NOW, pokes=pokes, run=Recorder(LIMIT))

    assert report.action == "poked (stall)"
    assert pokes.at[channel.key] == NOW


def test_rousing_says_nothing_about_what_to_do(supervised):
    """There is nothing here we know it should be working on."""
    text = sup.rousing("northwind")
    assert "usage limit" in text and "northwind" in text


def test_a_stalled_session_is_not_roused_twice_in_the_interval(supervised, tmp_path):
    config, channel = supervised
    pokes = sup.Pokes(tmp_path / "p.json")
    pokes.record(channel.key, NOW - 60)

    (report,) = sup.sweep(config, now=NOW, pokes=pokes, run=Recorder(LIMIT))

    assert report.action == "held"


def test_a_stalled_session_is_roused_again_once_the_interval_passes(supervised, tmp_path):
    """Because the poke before the reset was a wasted turn, and the one after
    it is what starts the session again."""
    config, channel = supervised
    pokes = sup.Pokes(tmp_path / "p.json")
    pokes.record(channel.key, NOW - 1000)

    (report,) = sup.sweep(config, now=NOW, pokes=pokes, run=Recorder(LIMIT))

    assert report.action == "poked (stall)"


def test_a_working_session_with_an_empty_inbox_is_left_alone(supervised, tmp_path):
    config, _ = supervised
    assert sup.sweep(config, now=NOW, pokes=sup.Pokes(tmp_path / "p.json"),
                     run=Recorder("● Running the tests\n")) == []


def test_mail_outranks_a_stall(supervised, tmp_path):
    """Both are true at once when a limit lands on a full inbox; the mail is
    the more useful thing to be told about."""
    config, channel = supervised
    put(channel.root, "m.md", age=6000)
    (report,) = sup.sweep(config, now=NOW, pokes=sup.Pokes(tmp_path / "p.json"),
                          run=Recorder(LIMIT))
    assert report.action == "poked (mail)"
    assert "unread" in report.detail


def test_a_stalled_pane_that_is_also_asking_something_is_still_refused(supervised, tmp_path):
    """The refusal is absolute: a limit notice does not license an Enter."""
    config, channel = supervised
    pokes = sup.Pokes(tmp_path / "p.json")
    pane = LIMIT + "\nDo you want to proceed?\n ❯ 1. Yes"

    (report,) = sup.sweep(config, now=NOW, pokes=pokes, run=Recorder(pane))

    assert report.action == "refused"
    assert channel.key not in pokes.at


def test_a_channel_with_no_pane_is_judged_on_its_inbox_alone(channel_root, tmp_path):
    """A nudge command is opaque to us, so there is nothing to read."""
    config = Config(
        channels=[ChannelSpec(path=channel_root, name="northwind", nudge=("true",))]
    )
    assert sup.sweep(config, now=NOW, pokes=sup.Pokes(tmp_path / "p.json")) == []
