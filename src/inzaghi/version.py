"""What build of Inzaghi this is.

There are no releases yet, so the package version alone says almost nothing:
every checkout between two of them reports the same number. What actually
distinguishes one running copy from another is the commit under it, and for a
development install that is readable -- ``uv tool install --editable`` leaves
the entry point pointing at a working tree, and a working tree has a git
directory above it.

So: the version, and for a source checkout the branch and the short hash, with
a mark when the tree has been edited since that commit. An installed wheel has
no tree to read and says only its version, which is the honest answer there.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as _installed
from pathlib import Path

#: Long enough that git itself would answer, short enough to sit in a corner.
HASH_LENGTH = 7

#: A git call on a working tree is local and fast, but the tree can live on a
#: mount that is not -- and this runs while the app is starting. Two seconds
#: and we do without.
GIT_TIMEOUT = 2.0

#: What is appended when the tree has been edited since the commit named.
DIRTY = "*"

#: A branch name has no length limit and this sits in a corner beside the key
#: hints. Cut the branch rather than let the line grow, because the hash is
#: the part that identifies the build and it comes after.
BRANCH_LENGTH = 20


def _git(root: Path, *args: str, run=None) -> str:
    """One git command in ``root``, or ``""`` if it cannot be answered.

    Every failure is the same failure here -- no git, no repository, a tree on
    a mount that has stopped answering -- and all of them mean the same thing:
    this copy cannot say which commit it is, which is worth knowing but not
    worth an exception during startup.
    """
    runner = run or _run
    try:
        return runner(["git", "-C", str(root), *args])
    except (OSError, subprocess.SubprocessError):
        return ""


def _run(command: list[str]) -> str:
    done = subprocess.run(
        command, capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def source_root(start: Path | None = None) -> Path | None:
    """The working tree this package is being run out of, if it is one.

    ``src/inzaghi/version.py`` puts the repository two directories above the
    package. A wheel installed into site-packages has no ``.git`` above it, and
    that absence is the test -- not an environment variable, not a marker file
    we would have to remember to ship.
    """
    here = (start or Path(__file__)).resolve()
    root = here.parents[2] if len(here.parents) > 2 else None
    return root if root and (root / ".git").exists() else None


def installed_version() -> str:
    try:
        return _installed("inzaghi")
    except PackageNotFoundError:  # pragma: no cover -- running from a raw tree
        return "0+unknown"


def describe(root: Path | None = None, run=None, *, checked: bool = False) -> str:
    """``0.1.0 · main 1a2b3c4*`` for a checkout, ``0.1.0`` for a wheel.

    ``checked`` skips the ``source_root`` test, for a caller that has already
    decided which tree it means.
    """
    number = installed_version()
    tree = root if (root and checked) else source_root(root)
    if tree is None:
        return number
    commit = _git(tree, "rev-parse", f"--short={HASH_LENGTH}", "HEAD", run=run)
    if not commit:
        return number  # a directory with a .git that git will not read
    branch = _git(tree, "rev-parse", "--abbrev-ref", "HEAD", run=run)
    dirty = DIRTY if _git(tree, "status", "--porcelain", run=run) else ""
    # A detached head has no branch name to print; the hash is the whole answer.
    where = f"{_clipped(branch)} {commit}" if branch and branch != "HEAD" else commit
    return f"{number} · {where}{dirty}"


def _clipped(branch: str) -> str:
    if len(branch) <= BRANCH_LENGTH:
        return branch
    return branch[: BRANCH_LENGTH - 1] + "…"


@lru_cache(maxsize=1)
def line() -> str:
    """``describe()`` for the running copy, asked once.

    Cached because it shells out and because the answer cannot change under a
    running process in any way that matters: a commit made while the app is up
    is not the commit the app is running.
    """
    return describe()
