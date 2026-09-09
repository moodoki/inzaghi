"""The objects a channel folder is read into."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from . import parse

Health = Literal["fresh", "late", "stale", "unknown"]
Direction = Literal["in", "out"]
#: Whether a delivered file has arrived yet, or was refused on sight.
Arrival = Literal["here", "syncing", "refused"]
#: What showing one to a person means: render it in the reader ourselves,
#: hand it to the desktop, or only point a file manager at the folder it
#: sits in.
Disposition = Literal["read", "view", "reveal"]

# How far past its own deadline a session must drift before "late" becomes
# "probably dead".  Two missed windows: one can be a slow job, two is a pattern.
_STALE_FACTOR = 2

#: Kinds that ask for a human the moment they appear.
LOUD_KINDS = frozenset({"hard-stop", "error"})

#: Default slack allowed past a promised update before calling it late. The
#: heartbeat has to cross a sync client to reach us, so a deadline that has just
#: passed usually means the file is still in flight, not that anything is wrong.
DEFAULT_GRACE = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class Doc:
    """A whole-file document, parsed as far as it will go."""

    path: Path
    text: str
    meta: dict[str, str]
    body: str
    mtime: float
    size: int

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def title(self) -> str:
        return parse.first_heading(self.body) or self.path.stem.replace("_", " ").title()

    @classmethod
    def load(cls, path: Path) -> "Doc":
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = parse.split_front_matter(text)
        return cls(path=path, text=text, meta=meta, body=body, mtime=stat.st_mtime, size=stat.st_size)


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file a notification delivered, and whether it is here yet.

    Resolved fresh on every scan rather than cached with the document that
    names it: the prose does not change when the payload finally lands.
    """

    #: As the notification wrote it, relative to the attachments folder.
    name: str
    #: Where it belongs in the channel.  Meaningless when ``refused``.
    path: Path
    #: The session's own description of the file, if it gave one.
    note: str = ""
    arrival: Arrival = "syncing"
    disposition: Disposition = "reveal"
    #: Bytes, once there are any to count.
    size: int | None = None
    #: Last-modified time, once there is a file to ask.  Carried so that a
    #: payload rewritten while it is being read is noticed by the scan that
    #: everything else here is noticed by: the tuple simply stops comparing
    #: equal, the same way a size filling in does.
    mtime: float | None = None
    #: ``(ctime, inode)``: the part of a file's identity that its size and its
    #: modification time miss.  A sync client swapping new contents in touches
    #: both of these even where the modification date it reports does not move,
    #: and a rewritten payload is often exactly as long as the one before it.
    #: Compared, never shown -- the same reason ``channel._doc`` fingerprints
    #: what it caches.
    fingerprint: tuple[float, int] | None = None
    #: Why it was refused, in the words shown to the person reading.
    problem: str = ""

    @property
    def openable(self) -> bool:
        """Only a file that is actually here can be shown at all."""
        return self.arrival == "here"

    @property
    def readable(self) -> bool:
        """Whether showing it means rendering it in the reader ourselves."""
        return self.arrival == "here" and self.disposition == "read"


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Liveness: when the session last spoke and when it promised to speak next."""

    doc: Doc
    updated: datetime | None
    next_by: datetime | None
    state: str | None

    @classmethod
    def from_doc(cls, doc: Doc) -> "Heartbeat":
        bullets = parse.parse_kv_bullets(doc.body)
        # ``ts`` counts as the update time. It is one of the four front-matter
        # keys the contract advertises, and on a file that is overwritten at
        # every wakeup "when this was written" is the same fact as "when this
        # was last updated" -- a session that filled it in was following the
        # contract, and was being read as though it had never said.
        updated = _first_ts(
            doc.meta.get("updated"), doc.meta.get("ts"), bullets.get("updated")
        )
        next_by = _first_ts(
            doc.meta.get("next_by"),
            *(v for k, v in bullets.items() if "next update" in k or k == "next"),
        )
        state = doc.meta.get("state") or bullets.get("state")
        return cls(doc=doc, updated=updated, next_by=next_by, state=state)

    @property
    def interval(self) -> timedelta | None:
        """The cadence the session set for itself, if it declared both ends."""
        if self.updated and self.next_by and self.next_by > self.updated:
            return self.next_by - self.updated
        return None

    def overdue_by(self, now: datetime) -> timedelta | None:
        if not self.next_by:
            return None
        late = now - self.next_by
        return late if late > timedelta(0) else None

    def health(self, now: datetime, grace: timedelta = DEFAULT_GRACE) -> Health:
        """Liveness, allowing ``grace`` for the update to make it across the sync.

        ``overdue_by`` stays truthful -- the countdown should say what the clock
        says. Only the judgement of whether that is a problem is softened.
        """
        if not self.next_by:
            return "unknown"
        late = now - self.next_by
        if late <= grace:
            return "fresh"
        interval = self.interval
        if interval and late > interval * _STALE_FACTOR:
            return "stale"
        return "late"


@dataclass(frozen=True, slots=True)
class Status:
    """The overwritten run-state file, plus whether it is asking for you."""

    doc: Doc
    updated: datetime | None
    waiting: str | None

    #: Things a session writes under "Waiting on you" that mean "nothing".
    NOTHING = frozenset({"", "-", "none", "none.", "nothing", "nothing.", "n/a", "na"})

    @classmethod
    def says_nothing(cls, section: str) -> bool:
        """Whether a "Waiting on you" section is answering "nothing".

        The first sentence decides, not the whole section. A session with
        nothing to ask writes "Nothing." and then, as often as not, a
        paragraph about what is running instead -- and the flag has to go
        quiet for that, or it stands until the next wakeup rewrites the file
        and teaches you to ignore it.

        Only a sentence that is *nothing but* the word counts. "Nothing is
        blocked except the licence decision" keeps its flag, because there the
        word is the subject of a question rather than the answer to one. That
        asymmetry is deliberate: an unflagged question waits until somebody
        happens to read the channel, which is the expensive way to be wrong.
        """
        return parse.first_sentence(section).lower() in cls.NOTHING

    @classmethod
    def from_doc(cls, doc: Doc) -> "Status":
        # ``ts`` for the same reason as on a heartbeat: this file is rewritten
        # whole at every wakeup, so the time it carries is the time it holds.
        updated = _first_ts(doc.meta.get("updated"), doc.meta.get("ts")) or parse.parse_timestamp(
            doc.body[:400]
        )
        section = parse.find_section(doc.body, r"waiting on you|needs? you|blocked on you")
        waiting = None
        if section is not None:
            stripped = section.strip()
            if not cls.says_nothing(stripped):
                waiting = stripped
        if doc.meta.get("needs_reply", "").lower() in {"true", "yes", "1"} and not waiting:
            waiting = section or "(flagged by the session)"
        return cls(doc=doc, updated=updated, waiting=waiting)

    @property
    def headline(self) -> str:
        """First substantive line, for the one-row overview."""
        for raw in self.doc.body.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("*", "_")) and line.endswith(("*", "_")):
                continue  # an italic metadata line, not the state
            return line.lstrip("-* ").strip()
        return ""


@dataclass(frozen=True, slots=True)
class Event:
    """One immutable entry in the log, in either direction."""

    doc: Doc
    direction: Direction
    kind: str
    slug: str
    ts: datetime
    #: For an ack: the message it answers, as a comparison key.
    ref: str | None = None
    #: Outbound only: still sitting in inbox/, not yet moved to inbox/done/.
    in_flight: bool = False

    @property
    def path(self) -> Path:
        return self.doc.path

    @property
    def title(self) -> str:
        heading = parse.first_heading(self.doc.body)
        if heading:
            return heading
        first = next((l.strip() for l in self.doc.body.splitlines() if l.strip()), "")
        return first[:120] or self.slug.replace("-", " ")

    @property
    def key(self) -> str:
        return parse.normalise_ref(self.doc.path.name)


@dataclass(frozen=True, slots=True)
class Thread:
    """A message you sent, together with the session's receipt for it."""

    sent: Event
    ack: Event | None = None
    #: When the session moved the message to inbox/done/, from the stamp it
    #: prefixed onto the filename.  Absent while the message is still in flight.
    picked_up: datetime | None = None

    @property
    def round_trip(self) -> timedelta | None:
        """How long the session took to answer.

        ``None`` when the two timestamps disagree about their order -- the send
        time comes from an mtime that a sync client may have rewritten, and a
        negative duration is noise, not information.
        """
        if self.ack:
            elapsed = self.ack.ts - self.sent.ts
            if elapsed >= timedelta(0):
                return elapsed
        return None

    @property
    def state(self) -> Literal["in-flight", "picked-up", "acked"]:
        if self.ack:
            return "acked"
        return "in-flight" if self.sent.in_flight else "picked-up"


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything one scan of a channel folder found."""

    root: Path
    name: str
    scanned_at: datetime
    heartbeat: Heartbeat | None = None
    status: Status | None = None
    pinned: dict[str, Doc] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)  # inbound, newest first
    threads: list[Thread] = field(default_factory=list)  # outbound, newest first
    conflicts: list[Path] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    #: Files each notification delivers, keyed by the notification's own path.
    #: Only notifications carry these: a file nobody points at is not a
    #: delivery, and nothing outside ``notifications/`` may name one.
    attachments: dict[Path, tuple[Attachment, ...]] = field(default_factory=dict)
    #: Slack allowed past a promised heartbeat before it counts as late.
    grace: timedelta = DEFAULT_GRACE

    @property
    def waiting(self) -> str | None:
        return self.status.waiting if self.status else None

    @property
    def in_flight(self) -> list[Thread]:
        return [t for t in self.threads if t.state != "acked"]

    def health(self, now: datetime | None = None) -> Health:
        if not self.heartbeat:
            return "unknown"
        return self.heartbeat.health(now or self.scanned_at, self.grace)

    def attention(self, now: datetime | None = None, unread: set[str] | None = None) -> bool:
        """Does this channel want a human right now?

        A hard-stop or an error is a thing that *happened*, so it asks for
        someone only until they have read it. Trouble that is still going on
        says so on its own -- through ``waiting``, or by the session missing
        its heartbeat -- and neither of those depends on having been read.

        ``unread`` is the set of paths not yet read. ``None`` means no read
        state was supplied, and then an unseen event is the safer assumption.
        """
        loud = [event for event in self.events if event.kind in LOUD_KINDS]
        if unread is not None:
            loud = [event for event in loud if str(event.path) in unread]
        return bool(self.waiting or self.health(now) in {"late", "stale"} or loud)


def _first_ts(*candidates: str | None) -> datetime | None:
    for candidate in candidates:
        if candidate:
            parsed = parse.parse_timestamp(candidate)
            if parsed:
                return parsed
    return None
