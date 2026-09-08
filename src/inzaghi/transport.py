"""Moving a channel over ssh, driven from the end that is not always on.

A channel does not care how it reaches the watcher, and a sync client is the
easy answer. This is the other one: rsync over ssh on a timer, run from here.
It is code rather than a snippet in a README because the *order* of the four
steps is the part that goes wrong, and an order can be tested.

What makes it tractable is that the two ends never write the same thing.
``notifications/`` and ``inbox/done/`` belong to the session; ``inbox/`` is
written here and consumed there. So the cycle is three one-way copies -- no
merge, no conflict rules -- plus the one step that has to decide something:

    1. pull  notifications/   the session's, ours to overwrite wholesale
    2. pull  inbox/done/      its receipts for what it has picked up
    3. retire the local copy of every message that has turned up in done/
    4. push  inbox/           what is left: messages not yet picked up

Step 3 sits where it does for a reason. Run step 4 before it and we upload a
message the session has already acted on, and the session acts on it again --
the double pickup. Only after retirement is ``inbox/`` a true statement about
what is still outstanding.

Nothing here carries ``--delete`` for ``inbox/``, in either direction.
Downward it would resurrect what the session just consumed. Upward it would
delete an instruction written thirty seconds ago and not yet read. Retirement
is by exact name instead, and only for a message the session has finished
with.
"""

from __future__ import annotations

import fcntl
import hashlib
import subprocess
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import parse
from .channel import Channel, ReadOnlyChannel
from .config import state_dir

#: Non-interactive on purpose: this runs from cron, where a passphrase prompt
#: is not a prompt but a hang.
SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=10"

#: Flags every leg shares.
#:
#: ``-a`` is not a convenience. A message's send time *is* its mtime, so a
#: copy that rewrites mtimes rewrites the record -- the same reason the
#: development notes say never to reach for plain ``cp -R``.
#:
#: ``--partial`` is deliberately absent. rsync writes into a dot-prefixed
#: temporary and renames on completion, which is why a half-transferred file
#: is invisible to the scanner and an attachment reads as *syncing* until all
#: of it is here. ``--partial`` puts the half file under its final name and
#: throws that away.
BASE: tuple[str, ...] = ("-a", "--timeout=120", "-e", SSH_COMMAND)

#: ``(argv) -> (exit code, stderr)``.  Injectable so the order of the cycle
#: can be tested without a host on the other end.
Runner = Callable[[Sequence[str]], "tuple[int, str]"]


class SyncFailed(RuntimeError):
    """A leg came back non-zero.  The marker is left untouched."""


class AlreadyRunning(RuntimeError):
    """Another cycle holds the lock -- a big attachment outlasting a short cron."""


@dataclass
class Result:
    """What one cycle did, in the order it did it."""

    commands: list[list[str]] = field(default_factory=list)
    #: Messages whose local copy was retired, by filename.
    retired: list[str] = field(default_factory=list)
    #: The marker touched on success, if the channel names one.
    marked: Path | None = None


def sync(
    channel: Channel,
    *,
    remote: str | None = None,
    marker: Path | None = None,
    run: Runner | None = None,
    dry_run: bool = False,
) -> Result:
    """Run one full cycle for ``channel``.

    The marker is touched only at the end, so its mtime means "a whole cycle
    got through" rather than "something was attempted". That is the whole
    basis on which the watcher is later allowed to blame the link instead of
    the session.
    """
    remote = (remote if remote is not None else channel.remote).rstrip("/")
    if not remote:
        raise ValueError(f"{channel.name}: no remote configured to sync with")
    if channel.read_only:
        # Pulling writes into the folder, and read-only means untouched.
        raise ReadOnlyChannel(f"{channel.name} is configured read-only; refusing to sync")

    marker = marker if marker is not None else channel.sync_marker
    runner = run or _run
    result = Result()

    with _held(channel):
        for directory in (channel.notifications_dir, channel.done_dir):
            directory.mkdir(parents=True, exist_ok=True)  # a mirror may be new

        # 1. and 2. -- the session's own subtrees, ours only to mirror.
        _leg(runner, result, dry_run, *BASE, "--delete",
             f"{remote}/notifications/", f"{channel.notifications_dir}/")
        _leg(runner, result, dry_run, *BASE,
             f"{remote}/inbox/done/", f"{channel.done_dir}/")

        # 3. -- before the push, always.
        if not dry_run:
            result.retired = retire(channel)

        # 4. -- and never with --delete; see the module docstring.
        _leg(runner, result, dry_run, *BASE, "--exclude=/done/",
             f"{channel.inbox_dir}/", f"{remote}/inbox/")

        if marker is not None and not dry_run:
            _touch(marker)
            result.marked = marker
    return result


def retire(channel: Channel) -> list[str]:
    """Delete the local copy of each message the session has finished with.

    Matched on the exact filename rather than the fuzzy key threading uses:
    this ends in ``unlink``, and the two jobs deserve different standards of
    proof. A ``done/`` entry retires ``X.md`` only when its own name is
    ``X.md`` with one pickup stamp in front of it.

    Like ``channel.remove_conflicts``, every path is re-checked at the moment
    of unlinking rather than trusted from the listing: the folder is being
    written by someone else, and a listing is already a little old.
    """
    twins: dict[str, Path] = {}
    for entry in _plain_files(channel.done_dir):
        twins.setdefault(parse.strip_stamp(entry.name), entry)

    retired: list[str] = []
    for path in _plain_files(channel.inbox_dir):
        twin = twins.get(path.name)
        if twin is None:
            continue
        if not (twin.is_file() and path.is_file() and not path.is_symlink()):
            continue
        if path.parent != channel.inbox_dir:  # pragma: no cover -- belt and braces
            continue
        try:
            path.unlink()
        except OSError:
            continue
        retired.append(path.name)
    return sorted(retired)


def _plain_files(directory: Path) -> list[Path]:
    """Files directly in ``directory``: no subdirectories, no dotfiles.

    Dotfiles are skipped for the same reason the scanner skips them -- an
    rsync still in flight is one.
    """
    try:
        return sorted(
            p
            for p in directory.iterdir()
            if p.is_file() and not p.is_symlink() and not p.name.startswith(".")
        )
    except OSError:
        return []


def _leg(runner: Runner, result: Result, dry_run: bool, *args: str) -> None:
    command = ["rsync", *args]
    result.commands.append(command)
    if dry_run:
        return
    code, error = runner(command)
    if code:
        raise SyncFailed(f"{' '.join(command)}\n  exit {code}: {error}" if error
                         else f"{' '.join(command)}: exit {code}")


def _run(command: Sequence[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(list(command), capture_output=True, text=True)
    except OSError as exc:  # no rsync on this machine
        return 127, str(exc)
    return done.returncode, (done.stderr or "").strip()


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


@contextmanager
def _held(channel: Channel):
    """Refuse to start a second cycle for the same channel.

    ``fcntl`` rather than the ``flock`` command, which macOS does not ship --
    and the machine driving the sync is as likely to be the laptop as not.
    """
    path = _lock_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AlreadyRunning(f"{channel.name}: a sync is already running") from None
        yield


def _lock_path(channel: Channel) -> Path:
    digest = hashlib.sha1(channel.key.encode("utf-8")).hexdigest()[:12]
    return state_dir() / "locks" / f"{digest}.lock"


def sync_all(channels: Iterable[Channel], **kwargs) -> dict[str, Result | Exception]:
    """Sync each channel, keeping going past one that fails.

    A host that is down, or a laptop that was asleep, must not stop the
    channels that are reachable from being brought up to date.
    """
    out: dict[str, Result | Exception] = {}
    for channel in channels:
        try:
            out[channel.name] = sync(channel, **kwargs)
        except (SyncFailed, AlreadyRunning, ReadOnlyChannel, ValueError, OSError) as exc:
            out[channel.name] = exc
    return out
