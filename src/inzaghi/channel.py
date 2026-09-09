"""Reading a channel folder into a :class:`~inzaghi.model.Snapshot`.

A channel is one folder shared with one unattended session:

    <root>/
      README.md            the contract
      notifications/       session -> you   (singletons + an append-only log)
        attachments/       files a notification delivers, never read on their own
      inbox/               you -> session   (moved to inbox/done/ once read)

Scanning is deliberately dumb and repeatable: no watcher state, no incremental
diffing beyond a content cache.  The folder is the only source of truth, and it
is edited by another machine over a sync client, so a full re-read is the only
honest way to answer "what is there now".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import NamedTuple

from collections.abc import Iterable

from . import attach, parse
from .model import (
    DEFAULT_GRACE,
    Attachment,
    Doc,
    Event,
    Heartbeat,
    Snapshot,
    Status,
    Thread,
)

NOTIFICATIONS = "notifications"
ATTACHMENTS = attach.ATTACHMENTS
INBOX = "inbox"
DONE = "done"

#: How long a cached parse is trusted before the file is read again whatever
#: ``stat`` says about it.  Nothing on a local disk needs this; a synced folder
#: does.  See :meth:`Channel._doc`.
CACHE_SECONDS = 60.0

_TEXT_SUFFIXES = frozenset({".md", ".txt", ".markdown"})
_ACK_REF_RE = re.compile(r"\bre[:\-]\s*(?P<ref>.+?)\s*$", re.I)


class ReadOnlyChannel(RuntimeError):
    """Raised when a channel marked read-only would be modified."""


def is_channel(path: Path) -> bool:
    """A folder is a channel when it has both halves of the conversation."""
    return (path / NOTIFICATIONS).is_dir() and (path / INBOX).is_dir()


def absence_is_real(path: Path) -> bool:
    """Whether a channel's disappearance can be believed.

    A folder that is simply gone looks identical to one on an unmounted volume,
    and identical again to one a sync client is halfway through rewriting. Two
    conditions have to hold before we act on an absence: the parent directory
    must be readable, so we know we are actually looking at the right place,
    and the channel's own directory must be gone. A directory that still exists
    but no longer looks like a channel is treated as mid-sync, not deleted.
    """
    return path.parent.is_dir() and not path.exists()


class _Cached(NamedTuple):
    """A parse, with what the file looked like and when we last believed it."""

    #: mtime, size, ctime and inode, as of the read.
    fingerprint: tuple[float, int, float, int]
    #: ``monotonic()`` at the read, so that a clock the sync client adjusts
    #: cannot make a parse look newer than it is.
    read_at: float
    doc: Doc


@dataclass
class Channel:
    """One folder, plus how we are allowed to treat it."""

    root: Path
    name: str = ""
    #: Refuse every write.  Set for channels a live session owns while we are
    #: still developing against them.
    read_only: bool = False
    #: Slack allowed past a promised heartbeat before it counts as late.
    grace: timedelta = DEFAULT_GRACE
    _cache: dict[Path, _Cached] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        self.name = self.name or self.root.name

    @property
    def key(self) -> str:
        return str(self.root)

    @property
    def notifications_dir(self) -> Path:
        return self.root / NOTIFICATIONS

    @property
    def attachments_dir(self) -> Path:
        return self.notifications_dir / ATTACHMENTS

    @property
    def inbox_dir(self) -> Path:
        return self.root / INBOX

    @property
    def done_dir(self) -> Path:
        return self.inbox_dir / DONE

    def _doc(self, path: Path, *, trust_stat: bool = True) -> Doc | None:
        """Load a document, reusing the cached parse when it has not changed.

        "Has not changed" is a question for the filesystem, and a File Provider
        mount -- iCloud, Dropbox, Nextcloud on macOS -- answers it about the
        placeholder standing in for the file rather than about the file. The
        contents live on a server until something opens them; until then
        ``stat`` reports whatever the provider last wrote into the placeholder,
        which is not necessarily what the session has written since. A cache
        that believes it will stop reading the file, and a file nobody reads is
        a file the provider is never asked to fetch: the staleness holds until
        the process restarts. Three things follow, and they are this method.

        ``trust_stat=False`` reads every time, and the scan passes it for the
        files that are overwritten in place. Those are the only ones a stale
        stat can hide -- a new notification is a new path, and a new path is
        always read -- and they are the ones whose freshness is the entire
        point of the panels above the log. Opening them is also what prompts
        the provider to fetch them, so the files that most need to be current
        are the ones we keep asking for.

        The fingerprint carries ``st_ctime`` and the inode beside mtime and
        size. A provider swapping a file's contents underneath us touches the
        inode, and marks the metadata, even when the modification date it
        reports stays where it was; and a heartbeat rewritten with a new
        timestamp is almost always the same length as the one before it, so
        size vouches for nothing on its own.

        And no parse is trusted for longer than ``CACHE_SECONDS``, so that a
        stat which has stopped moving altogether costs one stale minute rather
        than the rest of the session.
        """
        try:
            stat = path.stat()
        except OSError:
            self._cache.pop(path, None)
            return None
        fingerprint = (stat.st_mtime, stat.st_size, stat.st_ctime, stat.st_ino)
        cached = self._cache.get(path)
        if (
            trust_stat
            and cached is not None
            and cached.fingerprint == fingerprint
            and monotonic() - cached.read_at < CACHE_SECONDS
        ):
            return cached.doc
        try:
            doc = Doc.load(path)
        except OSError:
            return None
        # A file that turned out to be unchanged keeps the document it already
        # had. Re-reading is about not trusting the stat, not about handing the
        # widgets a new object every poll: the panes compare what they are
        # showing against what the scan found, and an identical parse under a
        # new identity is a redraw nobody asked for.
        if cached is not None and cached.doc == doc:
            doc = cached.doc
        self._cache[path] = _Cached(fingerprint, monotonic(), doc)
        return doc

    def scan(self, now: datetime | None = None) -> Snapshot:
        now = now or datetime.now().astimezone()
        pinned: dict[str, Doc] = {}
        events: list[Event] = []
        conflicts: list[Path] = []
        problems: list[str] = []

        for path in _text_files(self.notifications_dir, problems):
            if parse.is_conflict_copy(path.name):
                conflicts.append(path)
                continue
            # Asked before the load rather than after it: whether a file is
            # rewritten in place is exactly the question of whether its cached
            # parse may be trusted.
            singleton = _is_singleton(path.name)
            doc = self._doc(path, trust_stat=not singleton)
            if doc is None:
                continue
            if singleton:
                pinned[path.name] = doc
                continue
            events.append(_inbound_event(doc))

        readme = self._doc(self.root / "README.md", trust_stat=False)
        if readme is not None:
            pinned.setdefault("README.md", readme)

        # Resolved here rather than inside ``_doc``, which caches: a
        # notification does not change when the file it announced finally
        # finishes syncing, so the answer cannot be cached with the parse.
        # Notifications only -- an attachment is something the session
        # delivers, and the folder it delivers into is its own.
        attachments: dict[Path, tuple[Attachment, ...]] = {}
        for doc in (*pinned.values(), *(event.doc for event in events)):
            found = attach.resolve(self.attachments_dir, doc)
            if found:
                attachments[doc.path] = found

        outbound = self._scan_outbound(conflicts, problems)
        events.sort(key=lambda e: e.ts, reverse=True)
        outbound.sort(key=lambda e: e.ts, reverse=True)

        return Snapshot(
            root=self.root,
            name=self.name,
            scanned_at=now,
            heartbeat=Heartbeat.from_doc(pinned["HEARTBEAT.md"]) if "HEARTBEAT.md" in pinned else None,
            status=Status.from_doc(pinned["STATUS.md"]) if "STATUS.md" in pinned else None,
            pinned=pinned,
            events=events,
            threads=_weave(outbound, events),
            conflicts=conflicts,
            problems=problems,
            attachments=attachments,
            grace=self.grace,
        )

    def _scan_outbound(self, conflicts: list[Path], problems: list[str]) -> list[Event]:
        out: list[Event] = []
        for in_flight, directory in ((True, self.inbox_dir), (False, self.done_dir)):
            for path in _text_files(directory, problems):
                if parse.is_conflict_copy(path.name):
                    conflicts.append(path)
                    continue
                doc = self._doc(path)
                if doc is not None:
                    out.append(_outbound_event(doc, in_flight=in_flight))
        return out


def _text_files(directory: Path, problems: list[str]) -> list[Path]:
    try:
        return sorted(
            p
            for p in directory.iterdir()
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in _TEXT_SUFFIXES
        )
    except FileNotFoundError:
        return []
    except OSError as exc:  # a sync client can yank the mount mid-scan
        problems.append(f"{directory}: {exc}")
        return []


def _is_singleton(name: str) -> bool:
    """Overwritten-in-place files: the named ones, plus any SHOUTING stem."""
    if name in parse.SINGLETON_NAMES:
        return True
    stem = Path(name).stem
    return stem.upper() == stem and not stem[:1].isdigit()


def _inbound_event(doc: Doc) -> Event:
    parsed = parse.parse_event_filename(doc.path.name)
    kind = (doc.meta.get("kind") or parsed.kind or "note").lower()
    ts = (
        parse.parse_timestamp(doc.meta.get("ts", ""))
        or parsed.ts
        or parse.parse_timestamp(doc.body[:400])
        or datetime.fromtimestamp(doc.mtime).astimezone()
    )
    return Event(doc=doc, direction="in", kind=kind, slug=parsed.slug, ts=ts, ref=_ack_ref(doc, kind))


def _outbound_event(doc: Doc, *, in_flight: bool) -> Event:
    """Build the "you said" side of a thread.

    The send time is the file's mtime, not its name: a message keeps its
    original mtime when the session moves it to ``done/``, but the session
    prefixes the *pickup* time onto the filename, which would otherwise make
    every delivered message look instantaneous.
    """
    parsed = parse.parse_event_filename(doc.path.name)
    ts = parse.parse_timestamp(doc.meta.get("ts", "")) or datetime.fromtimestamp(doc.mtime).astimezone()
    return Event(
        doc=doc,
        direction="out",
        kind=(doc.meta.get("kind") or "message").lower(),
        slug=parsed.slug,
        ts=ts,
        in_flight=in_flight,
    )


def _ack_ref(doc: Doc, kind: str) -> str | None:
    """Which outbound message this event answers, as a comparison key."""
    if kind != "ack":
        return None
    explicit = doc.meta.get("re") or doc.meta.get("ref")
    if explicit:
        return parse.normalise_ref(explicit)
    heading = parse.first_heading(doc.body) or ""
    heading = re.sub(r"^\[[^\]]+\]\s*", "", heading)  # drop the "[ack]" marker
    match = _ACK_REF_RE.search(heading)
    if match:
        return parse.normalise_ref(match["ref"])
    return parse.normalise_ref(parse.parse_event_filename(doc.path.name).slug)


def _weave(outbound: list[Event], inbound: list[Event]) -> list[Thread]:
    """Pair each sent message with the ack that cites it."""
    acks = {e.ref: e for e in reversed(inbound) if e.kind == "ack" and e.ref}
    threads: list[Thread] = []
    for sent in outbound:
        key = sent.key
        stamped = parse.parse_event_filename(sent.doc.path.name)
        threads.append(
            Thread(
                sent=sent,
                ack=acks.get(key),
                picked_up=None if sent.in_flight else stamped.ts,
            )
        )
    return threads


def remove_conflicts(channel: Channel, paths: Iterable[Path]) -> tuple[list[Path], list[str]]:
    """Delete a sync client's leftover duplicates. Returns (removed, problems).

    The only deletion Inzaghi performs, so it re-checks every path against the
    conflict pattern and the channel's own directories at the moment of
    unlinking rather than trusting the snapshot it was handed -- that snapshot
    may be seconds old, and the folder is being written by someone else.
    """
    if channel.read_only:
        raise ReadOnlyChannel(f"{channel.name} is configured read-only; refusing to delete")

    removed: list[Path] = []
    problems: list[str] = []
    for path in paths:
        if not _is_conflict_in(channel, path):
            problems.append(f"{path.name}: not a conflict copy in this channel")
            continue
        try:
            path.unlink()
        except OSError as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        channel._cache.pop(path, None)
        removed.append(path)
    return removed, problems


def _is_conflict_in(channel: Channel, path: Path) -> bool:
    """A real file, named like a conflict copy, inside this channel's folders."""
    return (
        parse.is_conflict_copy(path.name)
        and path.is_file()
        and path.parent in {channel.notifications_dir, channel.inbox_dir, channel.done_dir}
    )
