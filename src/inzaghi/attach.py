"""Files a notification delivers alongside its prose.

A session that has something too big or too binary for Markdown writes it into
``notifications/attachments/`` and points at it from the notification that
explains it.  Inzaghi never renders these; it hands them to the desktop.

Two rules shape everything here.

**Nothing in the folder is read on its own.** An attachment exists because a
notification names it.  A file nobody points at stays invisible, so a payload
still crossing the sync is never mistaken for a delivery, and a leftover from
three runs ago never appears as one either.

**A channel is not trusted with what runs.** It is written by an unattended
session and relayed by a sync client, and ``open`` on an arbitrary file is a
decision about which application starts.  So the rule is narrow: a known
viewable type opens in the system viewer, and everything else -- archives
included -- only ever gets its containing folder shown.  A reference that
climbs out of the folder is refused rather than followed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath
from stat import S_ISREG
from urllib.parse import unquote

from . import parse
from .model import Attachment, Doc

#: The one folder a notification may deliver files from.
ATTACHMENTS = "attachments"

#: Types handed to the system viewer.  Anything absent from this set is only
#: ever revealed in a file manager, which is why the set is a whitelist and
#: not a blacklist of the dangerous ones: a type nobody thought about must
#: land on the safe side.  ``.svg`` is deliberately out -- it opens in a
#: browser and can carry script.
VIEWABLE = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".txt",
        ".md",
        ".markdown",
        ".log",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
    }
)

#: How long to wait on a launcher before assuming it has settled in.  Long
#: enough to catch one that refused outright, short enough not to hold a
#: worker while someone reads a PDF.
REAP_SECONDS = 2.0

#: Launchers we have handed a file to and stopped waiting for.  Held only so
#: that a still-running child is not garbage-collected out from under itself.
_DETACHED: set[subprocess.Popen] = set()


class CannotOpen(RuntimeError):
    """Raised when an attachment cannot be shown to the person at the terminal."""


# -- finding them ---------------------------------------------------------


def refs(doc: Doc) -> list[tuple[str, str]]:
    """``(name, note)`` for every attachment ``doc`` points at, in order.

    Two ways to point, because a session may be writing prose or filling in a
    header: a Markdown link into ``attachments/``, where the link text is the
    description, or a flat ``attachments:`` front-matter key listing names.
    A name given both ways appears once, keeping whichever description exists.
    """
    found: dict[str, str] = {}
    for entry in doc.meta.get(ATTACHMENTS, "").replace(";", ",").split(","):
        name = _relative_to_folder(entry.strip())
        if name:
            found.setdefault(name, "")
    for link in parse.markdown_links(doc.body):
        name = _relative_to_folder(link.target, require_folder=True)
        if name and not found.get(name):
            found[name] = link.label
    return list(found.items())


def _relative_to_folder(target: str, *, require_folder: bool = False) -> str:
    """Reduce a reference to a name inside the attachments folder, or ``""``.

    A front-matter entry is already relative to that folder.  A link has to
    say so -- ``require_folder`` -- or a citation of another notification
    would be read as a delivery.
    """
    if not target or "://" in target or target.startswith(("#", "mailto:")):
        return ""
    # Only a leading "./" is dropped, never a leading "../": an attempt to
    # climb out has to survive to be refused, not be quietly re-anchored.
    target = unquote(target).strip()
    while target.startswith("./"):
        target = target[2:]
    parts = PurePosixPath(target).parts
    if require_folder:
        if not parts or parts[0] != ATTACHMENTS:
            return ""
        parts = parts[1:]
    elif parts and parts[0] == ATTACHMENTS:
        parts = parts[1:]  # a header that spelled the folder out anyway
    return "/".join(parts)


def link_name(target: str) -> str:
    """What an inline link points at inside the folder, or ``""``.

    The reader needs this to match a clicked link back to something already
    resolved: the reference in the prose and the entry in the strip beneath
    it are the same delivery, reached two ways.
    """
    return _relative_to_folder(target, require_folder=True)


def resolve(folder: Path, doc: Doc) -> tuple[Attachment, ...]:
    """Every attachment ``doc`` names, checked against ``folder``.

    Touches the volume -- a couple of calls per reference -- so it belongs in
    the scan worker with everything else that can wait on a sync client.
    """
    return tuple(_resolve_one(folder, name, note) for name, note in refs(doc))


def _resolve_one(folder: Path, name: str, note: str) -> Attachment:
    disposition = "view" if Path(name).suffix.lower() in VIEWABLE else "reveal"
    refusal = _lexical_refusal(name)
    path = folder / name
    if refusal:
        return Attachment(name=name, path=path, note=note, arrival="refused", problem=refusal)

    try:
        if path.is_symlink():
            # A link could point anywhere, and nothing in the contract needs
            # one.  Cheaper to refuse the whole idea than to vouch for a target.
            return Attachment(
                name=name,
                path=path,
                note=note,
                arrival="refused",
                problem="refused: a symlink",
            )
        if not _inside(folder, path):
            return Attachment(
                name=name,
                path=path,
                note=note,
                arrival="refused",
                problem="refused: outside the channel",
            )
        info = path.stat()
    except OSError:
        # Absent, or on a volume that has stopped answering.  Both read as
        # still on its way: a missing file in a synced folder is late at
        # least as often as it is gone, and this one was announced.
        return Attachment(name=name, path=path, note=note, disposition=disposition)

    if not S_ISREG(info.st_mode):
        return Attachment(
            name=name, path=path, note=note, arrival="refused", problem="refused: not a file"
        )
    return Attachment(
        name=name,
        path=path,
        note=note,
        arrival="here",
        disposition=disposition,
        size=info.st_size,
    )


def _lexical_refusal(name: str) -> str:
    """Why ``name`` can be rejected without touching the filesystem, if it can."""
    if not name:
        return "refused: no filename"
    pure = PurePosixPath(name)
    if pure.is_absolute() or name.startswith("~"):
        return "refused: an absolute path"
    if ".." in pure.parts:
        return "refused: outside the channel"
    if "\\" in name:
        # A Windows path.  Harmless as a literal name, but it would sit there
        # reading "waiting on sync" forever, which is not what went wrong.
        return "refused: not a plain filename"
    return ""


def _inside(folder: Path, path: Path) -> bool:
    """Whether ``path`` really lands inside ``folder``, symlinked parents and all."""
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


# -- showing them ---------------------------------------------------------


def argv(attachment: Attachment, *, platform: str = sys.platform) -> list[str]:
    """The command that shows ``attachment`` to the person at the terminal.

    ``view`` hands the file to the desktop's default application for its type;
    ``reveal`` asks for the folder it sits in instead.  macOS can highlight the
    file within that folder, Linux can only open the folder: ``xdg-open`` has
    no equivalent of ``open -R``.

    Absolute either way.  A launcher is a separate process that inherits our
    working directory, and a config may well name a channel relatively.
    """
    target = attachment.path.absolute()
    if platform == "darwin":
        if attachment.disposition == "view":
            return ["open", str(target)]
        return ["open", "-R", str(target)]
    if platform.startswith("linux") or platform.startswith(("freebsd", "openbsd", "netbsd")):
        if attachment.disposition == "view":
            return ["xdg-open", str(target)]
        return ["xdg-open", str(target.parent)]
    raise CannotOpen(f"opening files is not wired up for {platform}")


def launch(attachment: Attachment, *, platform: str = sys.platform) -> list[str]:
    """Hand an attachment to the desktop.  Returns the command it ran.

    Never on the UI thread.  Both launchers can wait a long time: macOS
    ``open`` on a file iCloud has evicted blocks on the download, and
    ``xdg-open`` under some desktops does not return until the viewer it
    started exits.  So this spawns detached and waits only long enough to
    reap the fast, ordinary case and to notice a launcher that refused.
    """
    if not attachment.openable:
        raise CannotOpen(attachment.problem or f"{attachment.name} has not arrived yet")
    command = argv(attachment, platform=platform)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:  # no launcher installed, most likely
        raise CannotOpen(f"{command[0]}: {exc}") from None

    _DETACHED.difference_update({p for p in _DETACHED if p.poll() is not None})
    try:
        _, err = process.communicate(timeout=REAP_SECONDS)
    except subprocess.TimeoutExpired:
        # Still going, which for a foreground handler means it worked.  Kept
        # referenced rather than killed: the viewer is its child.
        _DETACHED.add(process)
        return command
    if process.returncode:
        detail = err.decode(errors="replace").strip().splitlines()
        raise CannotOpen(f"{command[0]}: {detail[-1] if detail else f'exit {process.returncode}'}")
    return command
