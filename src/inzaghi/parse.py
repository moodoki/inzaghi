"""Parsers for the channel file format.

Everything a channel writes is human-readable Markdown; nothing here may assume
more structure than the README contract promises.  Each parser degrades to
``None`` rather than raising, so a session that drifts from the format makes one
widget go quiet instead of taking the whole app down.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

# Kinds named by the v1 contract.  Unknown kinds are kept verbatim rather than
# dropped, so a session can invent one without a Inzaghi release.
KNOWN_KINDS = frozenset(
    {"milestone", "phase-summary", "ack", "hard-stop", "error", "note", "status"}
)

# Singletons are overwritten in place: they are live panels, not log entries.
SINGLETON_NAMES = frozenset({"STATUS.md", "HEARTBEAT.md", "TASK_OVERVIEW.md", "README.md"})

_EVENT_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{4})_(?P<kind>[^_]+)_(?P<slug>.+?)\.(?P<ext>md|txt|markdown)$"
)
_STAMPED_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[_T](?P<time>\d{4})_(?P<rest>.+)$"
)
_KV_BULLET_RE = re.compile(r"^[-*]\s+\*\*(?P<key>[^*]+?):?\*\*[:\s]\s*(?P<value>.*)$")
_UPDATED_RE = re.compile(r"^\s*[*_]{1,2}updated\s+(?P<ts>[^*_]+?)[*_]{1,2}\s*$", re.I | re.M)
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")

# Sync clients drop copies beside the file they could not merge.  They are never
# events; surfacing them as new mail would be worse than useless.
_CONFLICT_RE = re.compile(
    r"conflicted copy|\.sync-conflict-|~syncthing~|\(case conflict", re.I
)


# Inline links and images.  ``[label](target)``, plus the bracketed target form
# Markdown uses for a filename with spaces in it -- which delivered files have.
_LINK_RE = re.compile(
    r"(?P<image>!)?\[(?P<label>[^\]\n]*)\]"
    r"\(\s*(?:<(?P<bracketed>[^>\n]*)>|(?P<bare>[^)\s]+))"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
# Fenced blocks and inline spans quote commands and file listings, so a link
# inside one is an example, not a reference.
_FENCE_RE = re.compile(r"^(?P<fence>```+|~~~+)[^\n]*\n.*?(?:^(?P=fence)[ \t]*$|\Z)", re.M | re.S)
_SPAN_RE = re.compile(r"`[^`\n]*`")


def is_conflict_copy(name: str) -> bool:
    """True for a sync client's leftover duplicate of a real file."""
    return bool(_CONFLICT_RE.search(name))


def slugify(text: str, *, max_len: int = 48) -> str:
    """Filename-safe slug: lowercase ASCII words joined by hyphens."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "message"


def normalise_ref(name: str) -> str:
    """Collapse a filename to a comparison key.

    An ack cites the message it answers by name, but the citation reaches us
    through slugified filenames and prose titles, so ``2026-09-04-heartbeat.md``
    and ``re-2026-09-04-heartbeat-md`` have to land on the same key.
    """
    name = re.sub(r"\.(md|txt|markdown)$", "", name.strip(), flags=re.I)
    # A slugified citation carries the extension as a trailing word:
    # "re: 2026-09-04-heartbeat.md" reaches us as "re-2026-09-04-heartbeat-md".
    name = re.sub(r"[-_](md|txt|markdown)$", "", name, flags=re.I)
    name = re.sub(r"^re[-_: ]+", "", name, flags=re.I)
    stamped = _STAMPED_RE.match(name)
    if stamped:
        name = stamped.group("rest")
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass(frozen=True, slots=True)
class FileName:
    """What a notification's filename alone tells us."""

    ts: datetime | None
    kind: str | None
    slug: str


def parse_event_filename(name: str) -> FileName:
    """Split ``YYYY-MM-DD_HHMM_<kind>_<slug>.md``.

    Falls back through a stamped-but-kindless name to the bare stem, so a file
    that does not follow the convention still appears with a sane label.
    """
    match = _EVENT_RE.match(name)
    if match:
        return FileName(
            ts=_stamp_to_dt(match["date"], match["time"]),
            kind=match["kind"].lower(),
            slug=match["slug"],
        )
    stem = re.sub(r"\.(md|txt|markdown)$", "", name, flags=re.I)
    stamped = _STAMPED_RE.match(stem)
    if stamped:
        return FileName(
            ts=_stamp_to_dt(stamped["date"], stamped["time"]),
            kind=None,
            slug=stamped["rest"],
        )
    return FileName(ts=None, kind=None, slug=stem)


def _stamp_to_dt(date: str, time: str) -> datetime | None:
    try:
        return datetime.strptime(f"{date} {time}", "%Y-%m-%d %H%M").astimezone()
    except ValueError:
        return None


def parse_timestamp(text: str) -> datetime | None:
    """First ISO-8601 timestamp in ``text``, localised if it carries no zone."""
    match = _ISO_RE.search(text)
    if not match:
        return None
    raw = match.group(0).replace(" ", "T").replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", raw):  # +0800 -> +08:00
        raw = f"{raw[:-2]}:{raw[-2:]}"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Peel an optional flat ``---`` YAML header off the top of a document.

    Deliberately flat scalars only: the header is an optional hint layer over
    prose, and anything richer belongs in the prose.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in {"---", "..."}:
            meta: dict[str, str] = {}
            for entry in lines[1:index]:
                key, sep, value = entry.partition(":")
                if sep and key.strip():
                    meta[key.strip().lower()] = value.strip().strip("'\"")
            return meta, "".join(lines[index + 1 :])
        if not line.strip() or ":" in line:
            continue
        break  # not a header after all; leave the document untouched
    return {}, text


def first_heading(text: str) -> str | None:
    """The document's ``# title``, stripped of any ``[kind]`` prefix marker."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or None
        if line:
            return None
    return None


def first_sentence(text: str) -> str:
    """The opening sentence of ``text``, without its markup or its bullet.

    A sentence ends at ``.``, ``!``, ``?`` or a line break, whichever comes
    first, so a one-word answer followed by a paragraph of explanation can be
    read apart from the explanation.  What comes back is stripped of the things
    prose wraps a word in -- a bullet, bold, italics -- because a session
    writing ``- **Nothing.**`` is saying the same word as one writing
    ``Nothing``.
    """
    head = text.strip().lstrip("-*+ \t")
    for index, char in enumerate(head):
        if char in ".!?\n":
            head = head[:index]
            break
    return head.strip().strip("*_`").strip()


@dataclass(frozen=True, slots=True)
class Link:
    """One inline link or image, exactly as the prose wrote it."""

    label: str
    target: str
    image: bool = False


def markdown_links(text: str) -> list[Link]:
    """Every inline link and image in ``text``, code excluded.

    The target is returned undecoded and unresolved: whether it names anything,
    and whether that thing is allowed, are not questions about prose.
    """
    stripped = _SPAN_RE.sub(" ", _FENCE_RE.sub("\n", text))
    found: list[Link] = []
    for match in _LINK_RE.finditer(stripped):
        target = (match["bracketed"] or match["bare"] or "").strip()
        if target:
            found.append(
                Link(label=match["label"].strip(), target=target, image=bool(match["image"]))
            )
    return found


def parse_kv_bullets(text: str) -> dict[str, str]:
    """Collect ``- **key:** value`` bullets into a mapping."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = _KV_BULLET_RE.match(line.strip())
        if match:
            out[match["key"].strip().lower()] = match["value"].strip()
    return out


def iter_sections(text: str) -> Iterator[tuple[str, str]]:
    """Yield ``(heading, body)`` for each ``##``-or-deeper section."""
    heading: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("##"):
            if heading is not None:
                yield heading, "\n".join(buffer).strip()
            heading = line.lstrip("#").strip()
            buffer = []
        elif heading is not None:
            buffer.append(line)
    if heading is not None:
        yield heading, "\n".join(buffer).strip()


def find_section(text: str, pattern: str) -> str | None:
    """Body of the first section whose heading matches ``pattern``."""
    regex = re.compile(pattern, re.I)
    for heading, body in iter_sections(text):
        if regex.search(heading):
            return body
    return None
