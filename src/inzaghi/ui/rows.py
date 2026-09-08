"""Turning a snapshot into timeline rows.

Kept free of Textual so the ordering and labelling rules can be tested without
a terminal.  Row labels are Rich markup; every piece of text that came out of a
channel is escaped, because titles genuinely look like ``[milestone] ...`` and
would otherwise be parsed as markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from rich.markup import escape

from .. import fmt
from ..model import Attachment, Doc, Event, Snapshot, Thread

#: Marker and colour per event kind.  Anything unrecognised stays neutral.
KIND_STYLE: dict[str, tuple[str, str]] = {
    "milestone": ("◆", "green"),
    "phase-summary": ("▣", "cyan"),
    "ack": ("✓", "dim"),
    "hard-stop": ("■", "bold red"),
    "error": ("✗", "red"),
    "note": ("·", "dim"),
    "message": ("↑", "bright_blue"),
}
PINNED_ORDER = ("STATUS.md", "HEARTBEAT.md", "TASK_OVERVIEW.md")

HEALTH_STYLE = {
    "fresh": ("●", "green"),
    "late": ("◍", "yellow"),
    "stale": ("○", "bold red"),
    "unknown": ("·", "dim"),
}

_KIND_PREFIX_RE = re.compile(r"^\[[^\]]{1,24}\]\s*")

#: Marker per attachment state.  A refusal is the session's mistake and has to
#: be visible as one; a file still crossing the sync is nobody's.
ARRIVAL_STYLE: dict[str, tuple[str, str]] = {
    "here": ("⧉", ""),
    "syncing": ("⟳", "dim"),
    "refused": ("⚠", "yellow"),
}

#: Width of the timestamp column: enough for "Sep 04 22:10".
STAMP_WIDTH = 12


@dataclass(frozen=True, slots=True)
class Row:
    """One selectable line in a channel's timeline."""

    key: str
    doc: Doc
    kind: str
    label: str
    ts: datetime | None = None
    pinned: bool = False
    unread: bool = False
    #: True only for rows that can ever *be* unread -- inbound log entries.
    #: Your own messages and the pinned panels never can, so they are not
    #: evidence that anything below the divider has been read.
    readable: bool = False
    #: Lowercased haystack for search: the title and the whole document.
    text: str = ""
    #: Files this entry delivered, in the order it named them.
    attachments: tuple[Attachment, ...] = ()


def stamp(ts: datetime | None, now: datetime) -> str:
    """Right-aligned timestamp, so the columns after it line up."""
    return f"{fmt.clock(ts, now):>{STAMP_WIDTH}}"


def clean_title(text: str) -> str:
    """Drop the leading ``[kind]`` marker; the kind has its own column."""
    return _KIND_PREFIX_RE.sub("", text.strip())


def build_rows(snapshot: Snapshot, unread: set[str], now: datetime | None = None) -> list[Row]:
    """Pinned panels first, then everything else newest-first."""
    now = now or snapshot.scanned_at
    delivered = snapshot.attachments
    rows = [
        _pinned_row(name, doc, delivered.get(doc.path, ()), now)
        for name, doc in _pinned_docs(snapshot)
    ]

    entries: list[tuple[datetime, Row]] = []
    for event in snapshot.events:
        entries.append((event.ts, _event_row(event, delivered.get(event.path, ()), unread, now)))
    for thread in snapshot.threads:
        entries.append((thread.sent.ts, _thread_row(thread, now)))
    entries.sort(key=lambda pair: pair[0], reverse=True)
    rows.extend(row for _, row in entries)
    return rows


def _pinned_docs(snapshot: Snapshot) -> list[tuple[str, Doc]]:
    ordered = [(name, snapshot.pinned[name]) for name in PINNED_ORDER if name in snapshot.pinned]
    rest = sorted(
        (name, doc)
        for name, doc in snapshot.pinned.items()
        if name not in PINNED_ORDER and name != "README.md"
    )
    if "README.md" in snapshot.pinned:  # the contract, useful but never urgent
        rest.append(("README.md", snapshot.pinned["README.md"]))
    return ordered + rest


def _pinned_row(
    name: str, doc: Doc, attachments: tuple[Attachment, ...], now: datetime
) -> Row:
    updated = datetime.fromtimestamp(doc.mtime).astimezone()
    title = name.removesuffix(".md").replace("_", " ").lower()
    return Row(
        key=f"pin:{name}",
        doc=doc,
        kind="pinned",
        text=f"{title}\n{doc.body}".lower(),
        pinned=True,
        ts=updated,
        attachments=attachments,
        label=(
            f"{'':>{STAMP_WIDTH - 2}}[b]▣ {escape(title):<14}[/]"
            f" [dim]{fmt.ago(updated, now)}[/]{clip_marker(attachments)}"
        ),
    )


def _event_row(
    event: Event, attachments: tuple[Attachment, ...], unread: set[str], now: datetime
) -> Row:
    mark, colour = KIND_STYLE.get(event.kind, ("·", "white"))
    is_unread = str(event.path) in unread
    title = escape(clean_title(event.title))[:200]
    style = "b" if is_unread else "dim" if event.kind == "ack" else ""
    open_tag, close_tag = (f"[{style}]", "[/]") if style else ("", "")
    return Row(
        key=str(event.path),
        doc=event.doc,
        kind=event.kind,
        ts=event.ts,
        unread=is_unread,
        readable=True,
        text=f"{event.title}\n{event.doc.body}".lower(),
        attachments=attachments,
        label=(
            f"[dim]{stamp(event.ts, now)}[/] [{colour}]{mark}[/] "
            f"[{colour}]{escape(event.kind):<13}[/] {open_tag}{title}{close_tag}"
            f"{clip_marker(attachments)}"
        ),
    )


def _thread_row(thread: Thread, now: datetime) -> Row:
    sent = thread.sent
    title = escape(clean_title(sent.title))[:200]
    if thread.state == "acked":
        trip = thread.round_trip
        note = f"acked +{fmt.duration(trip, precise=True)}" if trip else "acked"
        colour = "dim"
    elif thread.state == "picked-up":
        note = f"picked up {fmt.ago(thread.picked_up, now)}"
        colour = "bright_blue"
    else:
        note = "in flight"
        colour = "bold bright_blue"
    return Row(
        key=str(sent.path),
        doc=sent.doc,
        kind="message",
        ts=sent.ts,
        text=f"{sent.title}\n{sent.doc.body}".lower(),
        label=(
            f"[dim]{stamp(sent.ts, now)}[/] [{colour}]↑[/] "
            f"[{colour}]{'you':<13}[/] {title} [dim]· {note}[/]"
        ),
    )


def clip_marker(attachments: tuple[Attachment, ...]) -> str:
    """The ``⧉2`` on a timeline row that delivered files, or nothing.

    Counts what was announced rather than what has landed: the point of the
    marker is that this entry came with files, which is true before they
    arrive and stays true if one of them was refused.
    """
    if not attachments:
        return ""
    return f" [dim]⧉{len(attachments)}[/]"


def attachment_label(attachment: Attachment) -> str:
    """One line in the attachment strip: what it is, then how big or how late."""
    mark, colour = ARRIVAL_STYLE[attachment.arrival]
    head = f"[{colour}]{mark}[/] " if colour else f"{mark} "
    name = escape(attachment.name)
    if attachment.arrival == "here":
        detail = fmt.size(attachment.size)
        # Where enter would put it, so the line says what pressing it does.
        if attachment.disposition == "reveal":
            detail += " · in folder"
        elif attachment.disposition == "read":
            detail += " · in reader"
    elif attachment.arrival == "refused":
        detail = escape(attachment.problem)
    else:
        detail = "waiting on sync"
    line = f"{head}{name} [dim]{detail}[/]"
    if attachment.note:
        line += f" [dim]· {escape(attachment.note)}[/]"
    return line


def health_badge(snapshot: Snapshot, now: datetime) -> str:
    """The one-glance liveness marker used in both the overview and a tab."""
    mark, colour = HEALTH_STYLE[snapshot.health(now)]
    return f"[{colour}]{mark}[/]"


ALL = "all"


@dataclass(frozen=True, slots=True)
class Filter:
    """What the timeline is currently showing.

    A kind and a search term compose: picking ``milestone`` and typing "gate"
    asks for milestones mentioning gate, which is the question someone actually
    has when a log runs to hundreds of entries.
    """

    kind: str = ALL
    query: str = ""

    @property
    def active(self) -> bool:
        return self.kind != ALL or bool(self.query.strip())

    def matches(self, row: Row) -> bool:
        if self.kind != ALL and row.kind != self.kind:
            return False
        needle = self.query.strip().lower()
        return not needle or needle in row.text

    def apply(self, rows: list[Row]) -> list[Row]:
        return [row for row in rows if self.matches(row)]

    def with_kind(self, kind: str) -> "Filter":
        return Filter(kind=kind, query=self.query)

    def with_query(self, query: str) -> "Filter":
        return Filter(kind=self.kind, query=query)


def kind_counts(rows: list[Row]) -> dict[str, int]:
    """How many rows of each kind, newest-first order preserved for the bar."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.kind] = counts.get(row.kind, 0) + 1
    return counts


def kind_cycle(rows: list[Row]) -> list[str]:
    """``all`` followed by the kinds actually present, in a stable order."""
    counts = kind_counts(rows)
    ordered = [kind for kind in KIND_STYLE if kind in counts]
    ordered += sorted(k for k in counts if k not in KIND_STYLE and k != "pinned")
    if "pinned" in counts:
        ordered.append("pinned")
    return [ALL, *ordered]


def filter_bar(rows: list[Row], current: Filter) -> str:
    """The one-line legend of kinds, doubling as the filter's own display.

    ``rows`` should already be narrowed by the search but not by the kind, so
    the counts read as a histogram of what the search found rather than of the
    whole log.
    """
    counts = kind_counts(rows)
    parts = []
    for kind in kind_cycle(rows):
        count = len(rows) if kind == ALL else counts.get(kind, 0)
        label = f"{escape(kind)} {count}"
        parts.append(f"[reverse b] {label} [/]" if kind == current.kind else f"[dim]{label}[/]")
    line = "  ".join(parts)
    if current.query.strip():
        line += f"   [b]/{escape(current.query)}[/]"
    return line


def unread_divider(rows: list[Row]) -> tuple[int, int] | None:
    """Where to draw the "new since you last looked" line, and how many are new.

    Returns the index the divider sits *after*, so everything above it is new.
    The list runs newest-first, and unread entries are usually -- but not
    always -- contiguous at the top: a rewritten file becomes unread again in
    its own chronological place. Anchoring to the *last* unread row keeps the
    line honest in that case, at the cost of it sitting further down.

    ``None`` when there is nothing to divide: no unread rows, or nothing
    beneath them that could have been read in the first place -- your own
    messages and the pinned panels do not count.
    """
    positions = [index for index, row in enumerate(rows) if row.unread]
    if not positions:
        return None
    last = positions[-1]
    if not any(row.readable for row in rows[last + 1 :]):
        return None
    return last, len(positions)


def divider_label(count: int, width: int = 4) -> str:
    """``──── 3 new above ────`` -- "above" because the newest is at the top."""
    rule = "─" * width
    return f"[dim]{rule}[/] [b]{count} new above[/] [dim]{rule}[/]"
