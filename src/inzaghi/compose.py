"""Writing messages into a channel's inbox.

Two rules govern every write here.  Writes are atomic -- the file appears in
``inbox/`` complete or not at all -- because a sync client watching the folder
will happily upload a half-written instruction, and a truncated ``STOP`` is a
worse outcome than no ``STOP``.  And a channel marked ``read_only`` refuses
them outright, which is how a live session's folder is protected from a
development build.

Atomic is not the same as invisible, and the difference showed up in a live
channel.  A rename is atomic, but the temporary being renamed *from* is a
directory entry like any other, and a session woken by the create event lists
``inbox/`` at exactly the moment it exists.  So the staging file is written
beside the channel rather than inside the folder being watched -- same
filesystem, so the rename is still atomic, but nothing half-written is ever
listed as an instruction.  The contract carries the other half of that rule,
because a sync client leaves temporaries of its own here and no amount of care
on this side stops it.

A message may carry files, the way a notification does coming the other way.
The rule is the same at both ends -- a file nobody points at is not a delivery
-- but the work is on this side: a path that means something on this machine
means nothing on the one that reads it, so the file is *copied* into
``inbox/attachments/`` and the draft's own reference is rewritten to point at
the copy. A session cannot tell a path that was never going to resolve from a
payload that has not synced yet, and it should not have to.
"""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import parse
from .channel import Channel, ReadOnlyChannel

__all__ = [
    "ATTACHMENTS",
    "MAX_ATTACHMENT_BYTES",
    "Attaching",
    "QUICK_ACTIONS",
    "QUICK_BY_KEYWORD",
    "QuickAction",
    "ReadOnlyChannel",
    "attaching",
    "send",
    "send_quick",
]

#: The folder inside ``inbox/``, named to match the one notifications use.
ATTACHMENTS = "attachments"

#: What a message may carry. A screenshot of what went wrong, a log excerpt,
#: a config to apply: those are the point, and the whole inbound attachment
#: corpus across four live channels is 31 MB. The folder belongs to a sync
#: client that has to carry whatever goes in, and a payload still uploading
#: when the session next wakes is a payload that was not delivered.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


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

    carried = attaching(body)
    refused = [item for item in carried if not item.sendable]
    if refused:
        raise ValueError(
            "; ".join(f"{item.source.name} {item.problem}" for item in refused)
        )

    inbox = channel.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    # The payload goes first, the same way round the contract asks a session
    # to write one: a message naming a file that is not there yet is a message
    # that arrives early, and nothing is lost by being early.
    copied: list[Path] = []
    try:
        for item in carried:
            destination = _carry(channel, item)
            copied.append(destination)
            body = _rewrite(body, item, destination.name)
        target = _unique(inbox, filename(body, slug=slug, now=now))
        # Staged at the top of the channel: the session watches inbox/, and
        # nobody reads the root for anything but README.md.
        _atomic_write(target, body + "\n", staging=channel.root)
    except BaseException:
        # Nothing half-sent: a payload whose message never landed is a file
        # nobody points at, which the contract says is invisible -- and an
        # invisible file in a synced folder is still a file being synced.
        for path in copied:
            path.unlink(missing_ok=True)
        raise
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


def _atomic_write(path: Path, text: str, *, staging: Path | None = None) -> None:
    """Write via a temp file, then rename it into place.

    The rename is atomic on the same filesystem, so a reader -- the session or
    the sync client -- never sees a partial message.  ``staging`` says where
    the temporary lives until then; it has to be on the same filesystem as
    ``path``, and it should not be a folder anybody watches for work.  Without
    it the temporary sits beside the target, which is atomic but visible.

    A staging directory that turns out to be a different filesystem is not an
    error worth failing a send over: the rename says so, and the write falls
    back to the target's own directory, which is what the contract's reading
    rule covers anyway.
    """
    for directory in (staging or path.parent, path.parent):
        handle, tmp = tempfile.mkstemp(dir=str(directory), prefix=".inzaghi-", suffix=".partial")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            # mkstemp is 0600; the session reading this folder may well be
            # another user on another machine, so widen to the usual
            # umask-respecting mode.
            os.chmod(tmp, 0o666 & ~_umask())
            os.replace(tmp, path)
            return
        except OSError as exc:
            Path(tmp).unlink(missing_ok=True)
            if exc.errno != errno.EXDEV or directory == path.parent:
                raise
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _umask() -> int:
    """Read the process umask without leaving it changed."""
    current = os.umask(0)
    os.umask(current)
    return current


# -- files a message carries ----------------------------------------------


@dataclass(frozen=True, slots=True)
class Attaching:
    """A local file a draft points at, on its way into ``inbox/attachments/``."""

    #: What the draft wrote, verbatim, so the rewrite can find it again.
    target: str
    source: Path
    #: The name it will be given in the folder, before collisions are settled.
    name: str
    #: The link text, or ``""`` for a path standing alone on its own line.
    label: str = ""
    #: Why it cannot go, if it cannot. A send refuses rather than carrying the
    #: message without the file: a session cannot tell a reference that was
    #: never going to resolve from a payload that has not synced yet.
    problem: str = ""

    @property
    def sendable(self) -> bool:
        return not self.problem


def attaching(text: str) -> list[Attaching]:
    """Every local file ``text`` points at, in the order it points at them.

    Two ways to point, and both are things a terminal hands you: a Markdown
    link whose target is a path on this machine, and a path standing alone on
    a line of its own -- which is what dropping a file on a terminal pastes.

    A path mid-sentence is prose, and so is a target that names nothing here:
    a message may perfectly well talk about ``scripts/run.sh`` on the machine
    the session is running on. What exists locally and is pointed at
    deliberately is a delivery; everything else is left alone.
    """
    found: dict[str, Attaching] = {}
    for link in parse.markdown_links(text):
        source = _local_file(link.target)
        if source is not None and link.target not in found:
            found[link.target] = _judge(link.target, source, link.label)
    for line in parse.without_code(text).splitlines():
        bare = line.strip()
        if not bare or bare.startswith(("-", "*", ">", "#")) or line.startswith(("    ", "\t")):
            continue  # a bullet, a quote, a heading, an indented block: prose
        source = _local_file(bare)
        if source is not None and bare not in found:
            found[bare] = _judge(bare, source, "")
    return list(found.values())


def _local_file(target: str) -> Path | None:
    """The path ``target`` names on this machine, or ``None`` if it is prose."""
    target = target.strip()
    if not target or "://" in target or target.startswith(("#", "mailto:", ATTACHMENTS + "/")):
        return None
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    try:
        path = Path(target).expanduser()
        # ``exists`` follows the link, which is right: what is copied is the
        # file at the end of it, and the copy is what crosses.
        return path if path.exists() else None
    except (OSError, ValueError):  # pragma: no cover -- a path the OS refuses
        return None


def _judge(target: str, source: Path, label: str) -> Attaching:
    """Decide whether ``source`` can go, and say why not in the same breath."""
    problem = ""
    name = source.name
    if source.is_dir():
        problem = "is a folder, not a file"
    elif not source.is_file():
        problem = "is not a regular file"
    elif not name or name in (".", ".."):  # pragma: no cover -- belt and braces
        problem = "has no name to carry"
    else:
        try:
            size = source.stat().st_size
        except OSError as exc:
            problem = f"cannot be read ({exc.strerror or exc})"
        else:
            if size > MAX_ATTACHMENT_BYTES:
                problem = (
                    f"is {size / (1 << 20):.0f} MB; the limit is "
                    f"{MAX_ATTACHMENT_BYTES // (1 << 20)} MB"
                )
    return Attaching(target=target, source=source, name=name, label=label, problem=problem)


def _carry(channel: Channel, item: Attaching) -> Path:
    """Copy one file into ``inbox/attachments/``, atomically and last-named."""
    folder = channel.inbox_dir / ATTACHMENTS
    folder.mkdir(parents=True, exist_ok=True)
    destination = _unique(folder, item.name)
    _atomic_copy(item.source, destination, staging=channel.root)
    return destination


def _rewrite(body: str, item: Attaching, name: str) -> str:
    """Point the draft's own reference at the copy, and change nothing else."""
    reference = f"{ATTACHMENTS}/{name}"
    if item.label or f"]({item.target})" in body or f"](<{item.target}>)" in body:
        for written in (f"]({item.target})", f"](<{item.target}>)"):
            if written in body:
                return body.replace(written, f"]({reference})", 1)
    # A path on a line of its own becomes a link, so that what the session
    # reads says what the file is called rather than where it used to live.
    return body.replace(item.target, f"[{name}]({reference})", 1)


def _atomic_copy(source: Path, path: Path, *, staging: Path | None = None) -> None:
    """``_atomic_write`` for bytes off the disk rather than a string in hand.

    Same reasoning, and the same fallback: the payload appears in the folder
    whole or not at all, and the temporary it is renamed from is staged where
    nobody is watching for work.
    """
    for directory in (staging or path.parent, path.parent):
        handle, tmp = tempfile.mkstemp(dir=str(directory), prefix=".inzaghi-", suffix=".partial")
        try:
            with open(source, "rb") as reader, os.fdopen(handle, "wb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            os.chmod(tmp, 0o666 & ~_umask())
            os.replace(tmp, path)
            return
        except OSError as exc:
            Path(tmp).unlink(missing_ok=True)
            if exc.errno != errno.EXDEV or directory == path.parent:
                raise
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
