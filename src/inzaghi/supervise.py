"""Poking a session that has stopped reading its inbox.

Every mechanism a session can build to notice a message is started by the
session, and so cannot recover it once a turn ends with nothing armed. Three
channels here lost mail that way in one night: a watcher exited on a quiet
timeout meaning to be re-armed, nothing re-armed it, and the messages that
arrived four hours later landed in a folder no process was watching.

So the watching belongs in two places that fail independently. The session's
own watcher stays armed for the life of the session and gives it low-latency
notice on turns it is already taking. This runs *outside* every session, sees
a folder that has held a message too long, and pokes whoever owns it.

It is deliberately dumb. It does not read a message, decide what it means, or
touch a channel in any way: a supervisor that wrote into the folder it watches
would be one more thing racing the session. It notices, it pokes, it records
that it poked, and everything else is the session's job.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .channel import Channel
from .config import Config, state_dir
from .model import Doc, Heartbeat

#: How every line this sends begins, so that none of it can be read back as
#: evidence. A poke lands in the pane it was typed into and stays there, so a
#: pattern matched against the whole pane matches our own words on the next
#: pass -- which is a supervisor detecting itself, once every interval, for as
#: long as the scrollback holds. It sent 105 pokes that way before this line
#: existed. Nothing we wrote is ever evidence of anything.
SIGNATURE = "Inzaghi supervisor:"

#: How often a resident sweep looks. Cheap: one listing per channel.
DEFAULT_INTERVAL = 30.0

#: What a pane looks like when something is being asked of a human. Poking it
#: would type into that question, and an Enter after it would answer one --
#: a permission prompt is the user's to answer and never ours, so a pane that
#: might be showing one is left alone and said to be left alone.
ASKING = re.compile(
    r"(do you want to proceed|requires confirmation|allow this|"
    r"\[y/n\]|\(y/n\)|press enter to continue|❯\s*1\.\s)",
    re.IGNORECASE,
)

#: And what one looks like when the model has stopped being available to it.
#: A usage limit does not kill anything -- the process is fine, the watchers
#: are fine, the folder is fine -- it just means nothing will happen until the
#: limit resets, and nothing tells the session when it has. So a stalled
#: session is poked on its own cadence whether or not it has mail waiting:
#: that poke is what restarts it, and before the reset it costs one wasted
#: turn that ends the way the last one did.
STALLED = re.compile(
    r"(usage limit|rate limit|limit reached|limit will reset|"
    r"out of (?:credits|quota)|resets? at)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Waiting:
    """What one channel is holding, and since when."""

    channel: Channel
    paths: tuple[Path, ...]
    oldest: float  #: epoch seconds of the oldest message's mtime

    @property
    def count(self) -> int:
        return len(self.paths)

    def age(self, now: float) -> float:
        return max(0.0, now - self.oldest)


@dataclass(frozen=True, slots=True)
class Pane:
    """What one look at a session's pane said about it."""

    #: Why it must not be typed into, or "" when it may be.
    refusal: str = ""
    #: Whether it says the words a session waiting out a usage limit says.
    stalled: bool = False
    #: What it said, with our own words removed -- kept so that two looks can
    #: be compared without reading either of them again.
    text: str = ""


@dataclass(frozen=True, slots=True)
class Report:
    """One channel's outcome from one sweep, for the log and for the tests."""

    channel: str
    waiting: int
    #: What happened: "poked", or why not.
    action: str
    detail: str = ""


def messages(channel: Channel) -> list[Path]:
    """The unread messages in a channel's inbox, by the contract's own rule.

    ``*.md`` entries that do not begin with a dot, and nothing else: a sync
    client leaves ``.partial`` and ``~hex`` temporaries in that folder, and a
    supervisor that counted one would poke a session about a file that is not
    a message yet.
    """
    try:
        entries = sorted(channel.inbox_dir.iterdir())
    except OSError:
        return []  # an unreadable inbox is not evidence of anything
    found = []
    for path in entries:
        if path.name.startswith(".") or path.suffix.lower() != ".md":
            continue
        try:
            if path.is_file() and not path.is_symlink():
                found.append(path)
        except OSError:
            continue
    return found


def look(channel: Channel) -> Waiting | None:
    """What ``channel`` is holding, or ``None`` if its inbox is clear."""
    paths = messages(channel)
    if not paths:
        return None
    stamps = []
    for path in paths:
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue  # it went while we were looking; the others still count
    # The minimum over the files themselves, never clamped against the clock:
    # a folder crossing a sync client carries the mtimes of the machine that
    # wrote it, and one a little ahead of us is not a message from the future,
    # it is a message.
    oldest = min(stamps) if stamps else time.time()
    return Waiting(channel=channel, paths=tuple(paths), oldest=oldest)


@dataclass
class Pokes:
    """When each channel was last poked. Local state, never in a channel."""

    path: Path
    at: dict[str, float] = field(default_factory=dict)
    #: Which channels looked stalled on the last pass. A stall is poked on the
    #: edge -- the pass where it first appears -- and not again while it lasts.
    #: A session that answered has answered the first one; one that is wedged
    #: will not answer the twentieth either, and twenty pokes cost twenty
    #: wake-ups doing nothing but reading the same sentence.
    stalled: dict[str, bool] = field(default_factory=dict)
    _dirty: bool = field(default=False, repr=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "Pokes":
        path = path or (state_dir() / "supervise.json")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path=path)
        at = {str(k): float(v) for k, v in raw.get("at", {}).items() if _number(v)}
        stalled = {str(k): bool(v) for k, v in raw.get("stalled", {}).items()}
        return cls(path=path, at=at, stalled=stalled)

    def record(self, key: str, when: float) -> None:
        self.at[key] = when
        self._dirty = True

    def saw_stall(self, key: str, stalled: bool) -> bool:
        """Note what the pane looks like now; True if this is a rising edge."""
        was = self.stalled.get(key, False)
        if was != stalled:
            self.stalled[key] = stalled
            self._dirty = True
        return stalled and not was

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "at": self.at, "stalled": self.stalled}, indent=1, sort_keys=True
        )
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = False


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def due(waiting: Waiting, last: float | None, now: float, after: float, every: float) -> str:
    """``""`` when this channel should be poked, or the reason it should not."""
    age = waiting.age(now)
    if age < after:
        return f"waiting {int(age)}s, under the {int(after)}s grace"
    if last is not None and now - last < every:
        return f"poked {int(now - last)}s ago, under the {int(every)}s interval"
    return ""


# -- poking ---------------------------------------------------------------


def overdue(channel: Channel, now: float, grace: float) -> bool:
    """Whether the channel has missed the deadline it set for itself.

    This is the test, and the pane is only the reason. Every heartbeat
    publishes ``next update expected by`` for exactly this question, so a
    session declares its own cadence -- thirty minutes while it idles holding
    a decision, five mid-build, a hundred and fifty through a long rehearsal
    -- and no threshold of ours has to serve all three. Reading silence off a
    terminal instead was wrong three times in one evening against sessions
    that were minutes inside a window they had published.

    No heartbeat, or one that promises nothing, is not evidence of a stall:
    absence of a promise is not a broken one.
    """
    try:
        doc = Doc.load(channel.notifications_dir / "HEARTBEAT.md")
    except OSError:
        return False
    beat = Heartbeat.from_doc(doc)
    if beat.next_by is None:
        return False
    return datetime.fromtimestamp(now).astimezone() > beat.next_by + timedelta(seconds=grace)


def _anywhere(text: str) -> re.Pattern[str]:
    """``text``, matchable however a terminal broke it across lines.

    Character by character with whitespace allowed between each, because a
    pane wraps at the screen edge with no regard for words: the break falls
    mid-word as readily as between two, and a line-based or word-based
    comparison misses exactly the cases that matter.
    """
    return re.compile(r"\s*".join(re.escape(c) for c in "".join(text.split())))


def theirs(pane: str, spoken: Iterable[str] = ()) -> str:
    """``pane`` with anything we said taken out, which is all that may be read.

    ``spoken`` is what we might have said to *this* channel. A message only
    partly on screen leaves a fragment this cannot match, which is the second
    reason the messages themselves avoid every word being looked for -- the
    first being that a message describing a stall would report one.
    """
    for text in spoken:
        pane = _anywhere(text).sub(" ", pane)
    # Whatever follows a signature is a fragment of ours that the wrapping or
    # the scrollback cut in half, and nothing of the session's is in it.
    if SIGNATURE in pane:
        pane = pane.split(SIGNATURE)[0]
    return pane


def rousing(name: str) -> str:
    """The line a stalled session is given, when it has no mail waiting.

    Deliberately not an instruction: there is nothing here we know it should
    be doing. It is a tap on the shoulder, and what to do next is in its own
    context and its own channel.

    And deliberately free of every word ``STALLED`` looks for. The message
    lands in the pane it is typed into and stays there, so a message that
    described the thing it was reporting would be read back as another report
    of it. Filtering our own text out of the capture is the belt; this is the
    braces, and it is the half that cannot come loose -- a message that never
    contained the evidence cannot manufacture any.
    """
    return (
        f"{SIGNATURE} nothing has moved in {name}'s session for a while, so "
        "this is a tap on the shoulder. Check for yourself: if you were paused "
        "and can run again, carry on where you left off and say so in the "
        "heartbeat's state: line. If you were not, this was a false positive — "
        "say that instead, and please tell the Inzaghi session so, because the "
        "detection is worth fixing and your channel's own record is not the "
        "place to write something that did not happen."
    )


def note(waiting: Waiting, now: float) -> str:
    """The line a poked session is given. One sentence, then what to do."""
    count = waiting.count
    noun = "message" if count == 1 else "messages"
    age = waiting.age(now)
    since = f"{int(age // 3600)}h" if age >= 3600 else f"{int(age // 60)}m"
    names = ", ".join(path.name for path in waiting.paths[:3])
    if count > 3:
        names += f", and {count - 3} more"
    return (
        f"Inzaghi supervisor: {count} unread {noun} in {waiting.channel.name}'s "
        f"inbox, oldest {since} old ({names}). Read them, act, write an ack, "
        f"and move each to inbox/done/ with the pickup time prefixed."
    )


def _run(command: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout + done.stderr).strip()


def read_pane(target: str, spoken: Iterable[str] = (), run=_run) -> Pane:
    """Look at a session's pane once, and answer both questions about it.

    Whether it may be typed into: a poke is keystrokes, and the last line of a
    pane decides what they mean. Typing into a session that is asking its user
    a question -- a permission prompt, a confirmation, a menu -- puts our text
    where that answer goes, and the Enter behind it submits one. That decision
    belongs to the person the question was put to and is never ours to make
    for them, so a pane that might be showing one is skipped, and said to be
    skipped. A pane that cannot be read at all is skipped too: if we cannot
    see what we are typing into, we do not type.

    And whether it is stalled: a session waiting out a usage limit is not
    broken and nothing it started has died, but nothing will happen in it
    again until something asks. That is what the poke is for.
    """
    code, out = run(["tmux", "capture-pane", "-p", "-t", target, "-S", "-12"])
    if code != 0:
        return Pane(refusal=f"cannot read pane {target}: {out or 'tmux failed'}")
    out = theirs(out, spoken)
    # Both are read, and neither shadows the other: a session can perfectly
    # well be waiting out a limit *and* holding a question, and the refusal
    # has to win while the stall is still what gets reported.
    return Pane(
        refusal=(
            f"pane {target} is asking its user something; not typing into it"
            if ASKING.search(out)
            else ""
        ),
        stalled=bool(STALLED.search(out)),
        text=out,
    )


def tmux_poke(target: str, text: str, run=_run) -> str:
    """Type ``text`` into a tmux pane and submit it. ``""`` on success.

    Two calls, and the literal flag on the first: ``send-keys`` reads its
    arguments as key names, so a message containing the word "Enter" or a
    bracketed token would otherwise be pressed rather than typed.
    """
    code, out = run(["tmux", "send-keys", "-t", target, "-l", "--", text])
    if code != 0:
        return f"tmux send-keys failed: {out or code}"
    code, out = run(["tmux", "send-keys", "-t", target, "Enter"])
    if code != 0:
        return f"tmux Enter failed: {out or code}"
    return ""


def _substituted(argv: list[str], waiting: Waiting, text: str) -> list[str]:
    fields = {
        "name": waiting.channel.name,
        "path": str(waiting.channel.root),
        "count": str(waiting.count),
        "message": text,
    }
    out = []
    for arg in argv:
        for key, value in fields.items():
            arg = arg.replace("{" + key + "}", value)
        out.append(arg)
    return out


# -- a sweep --------------------------------------------------------------


def sweep(
    config: Config,
    *,
    names: list[str] | None = None,
    now: float | None = None,
    pokes: Pokes | None = None,
    dry_run: bool = False,
    run=_run,
) -> list[Report]:
    """Look at every supervised channel once, and poke the ones that need it.

    Returns one report per channel that is holding anything, whether or not it
    was poked. A channel with an empty inbox is not reported: the quiet case
    is the common one and a supervisor that narrates it is a log nobody reads.
    """
    now = time.time() if now is None else now
    pokes = Pokes.load() if pokes is None else pokes
    after = config.nudge_after_seconds
    every = config.nudge_every_seconds
    wanted = set(names or [])

    reports: list[Report] = []
    for channel in config.discover():
        if wanted and channel.name not in wanted:
            continue
        spec = _spec_for(config, channel)
        target = (spec.tmux if spec else "") or ""
        argv = list(spec.nudge) if spec and spec.nudge else []
        waiting = look(channel)
        count = waiting.count if waiting else 0

        if waiting is not None and not target and not argv:
            reports.append(
                Report(channel.name, count, "unsupervised",
                       "no tmux target or nudge command in the config")
            )
            continue
        if waiting is None and not target:
            continue  # nothing waiting, and no pane to tell whether it stalled

        # The pane answers two questions and is read once for both. Only a
        # tmux-configured channel has one; a ``nudge`` command is opaque to us
        # and its channel is judged on its inbox alone.
        # What we might have said here, so that none of it is read back.
        spoken = (rousing(channel.name), note(waiting, now)) if waiting else (rousing(channel.name),)
        pane = read_pane(target, spoken, run=run) if target else Pane()

        # Recorded every pass, for every channel with a pane, so that a stall
        # which has cleared re-arms the edge for the next one.
        # Two independent things have to hold before a session is called
        # stalled. Its own heartbeat has to be past the deadline it published
        # -- that is the test, and it is the one the contract already answers
        # -- and its pane has to say why. The words alone were never enough:
        # they were ours once, and after that they were sessions describing a
        # limit they had survived.
        late = pane.stalled and overdue(channel, now, config.heartbeat_grace_seconds)
        edge = pokes.saw_stall(channel.key, late) if target else False

        if waiting is not None:
            why = due(waiting, pokes.at.get(channel.key), now, after, every)
            reason, text = ("mail", note(waiting, now))
        elif edge:
            # The pass on which the stall first appeared, and only that one.
            # A session that is coming back answers the first poke; one that is
            # wedged will not answer any, so repeating costs wake-ups and buys
            # nothing. The interval still applies, so a stall arriving straight
            # after a mail poke waits its turn.
            last = pokes.at.get(channel.key)
            why = "" if last is None or now - last >= every else (
                f"poked {int(now - last)}s ago, under the {int(every)}s interval"
            )
            reason, text = ("stall", rousing(channel.name))
        else:
            continue

        if why:
            reports.append(Report(channel.name, count, "held", why))
            continue
        if dry_run:
            reports.append(Report(channel.name, count, f"would poke ({reason})", text))
            continue
        if target:
            if pane.refusal:
                reports.append(Report(channel.name, count, "refused", pane.refusal))
                continue
            failure = tmux_poke(target, text, run=run)
        else:
            code, out = run(_substituted(argv, waiting, text))
            failure = "" if code == 0 else f"nudge command failed: {out or code}"
        if failure:
            reports.append(Report(channel.name, count, "failed", failure))
            continue
        pokes.record(channel.key, now)
        reports.append(Report(channel.name, count, f"poked ({reason})", text))
    pokes.save()
    return reports


def _spec_for(config: Config, channel: Channel):
    """The explicit config entry for ``channel``, if it has one.

    A channel found by scanning a root has no entry and therefore no way to be
    poked -- which is reported rather than passed over, because "supervised"
    and "has an empty inbox" must never look the same from outside.
    """
    for spec in config.channels:
        if spec.path.expanduser() == channel.root:
            return spec
    return None


def watch(
    config: Config,
    *,
    names: list[str] | None = None,
    interval: float = DEFAULT_INTERVAL,
    on_report=None,
    stop=None,
) -> None:
    """Sweep every ``interval`` seconds until told to stop.

    The config is re-read each pass, so a channel added while this is running
    is supervised without a restart -- the same promise the viewer's discovery
    makes, and for the same reason: this is meant to be left alone for weeks.
    """
    pokes = Pokes.load()
    while not (stop and stop()):
        try:
            current = config.reload()
        except Exception:  # noqa: BLE001 -- a bad edit must not end the watch
            current = config
        try:
            for report in sweep(current, names=names, pokes=pokes):
                if on_report:
                    on_report(report)
        except Exception as exc:  # noqa: BLE001 -- nor must one bad sweep
            if on_report:
                on_report(Report("-", 0, "error", str(exc)))
        time.sleep(interval)


def stamp(when: float | None = None) -> str:
    return datetime.fromtimestamp(when or time.time()).astimezone().isoformat(timespec="seconds")
