"""Writing messages into a channel's inbox.

Two rules govern every write here.  Writes are atomic -- the file appears in
``inbox/`` complete or not at all -- because a sync client watching the folder
will happily upload a half-written instruction, and a truncated ``STOP`` is a
worse outcome than no ``STOP``.  And a channel marked ``read_only`` refuses
them outright, which is how a live session's folder is protected from a
development build.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import parse
from .channel import Channel, ReadOnlyChannel

__all__ = ["QUICK_ACTIONS", "QUICK_BY_KEYWORD", "QuickAction", "ReadOnlyChannel", "send", "send_quick"]


@dataclass(frozen=True, slots=True)
class QuickAction:
    """One of the keywords the contract says a session recognises on sight."""

    key: str
    keyword: str
    label: str
    description: str
    #: Ends or suspends an unattended run; the UI must confirm before sending.
    destructive: bool = False


QUICK_ACTIONS: tuple[QuickAction, ...] = (
    QuickAction("s", "STATUS", "Status", "Write a fresh STATUS.md now."),
    QuickAction("p", "PAUSE", "Pause", "Finish the current step, start no new jobs.", destructive=True),
    QuickAction("u", "RESUME", "Resume", "Resume normal work."),
    QuickAction("x", "STOP", "Stop", "Finish the current step, write a summary, end the loop.", destructive=True),
)

QUICK_BY_KEYWORD = {action.keyword: action for action in QUICK_ACTIONS}


def send(channel: Channel, text: str, *, slug: str | None = None, now: datetime | None = None) -> Path:
    """Write ``text`` into the channel's inbox and return the resulting path."""
    if channel.read_only:
        raise ReadOnlyChannel(f"{channel.name} is configured read-only; refusing to write")
    body = text.strip()
    if not body:
        raise ValueError("refusing to send an empty message")

    inbox = channel.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    target = _unique(inbox, filename(body, slug=slug, now=now))
    _atomic_write(target, body + "\n")
    return target


def send_quick(channel: Channel, action: QuickAction, note: str = "", **kwargs) -> Path:
    """Send one of the recognised keywords, optionally with a line of context."""
    body = action.keyword if not note.strip() else f"{action.keyword}\n\n{note.strip()}"
    return send(channel, body, slug=action.keyword.lower(), **kwargs)


def filename(body: str, *, slug: str | None = None, now: datetime | None = None) -> str:
    """``YYYY-MM-DD_HHMM_<slug>.md``, the name shape the contract suggests."""
    now = now or datetime.now()
    first_line = next((l.strip() for l in body.splitlines() if l.strip()), "message")
    return f"{now:%Y-%m-%d_%H%M}_{slug or parse.slugify(first_line)}.md"


def _unique(directory: Path, name: str) -> Path:
    """A path that does not exist yet, so two sends in a minute cannot collide."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, 100):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"cannot find a free name for {name} in {directory}")


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename into place.

    The rename is atomic on the same filesystem, so a reader -- the session or
    the sync client -- never sees a partial message.
    """
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".inzaghi-", suffix=".partial")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp is 0600; the session reading this folder may well be another
        # user on another machine, so widen to the usual umask-respecting mode.
        os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _umask() -> int:
    """Read the process umask without leaving it changed."""
    current = os.umask(0)
    os.umask(current)
    return current
