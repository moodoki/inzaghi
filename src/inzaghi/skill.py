"""Installing the channel protocol into an agent harness.

The protocol itself is harness-neutral: ``skills/inzaghi/SKILL.md`` is plain
Markdown describing how a session should operate a channel, and only its YAML
front matter is Claude Code's format. Supporting another harness means adding an
entry to :data:`HARNESSES` and, if that harness wants a different header, a
wrapper beside the existing one — never a second copy of the instructions.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .protocol import CHANNEL_README, PROTOCOL_VERSION

SKILL_NAME = "inzaghi"
SKILLS_ROOT = Path(__file__).parent / "skills"
SOURCE = SKILLS_ROOT / SKILL_NAME
REFERENCE = SOURCE / "reference" / "channel-README.md"
#: The name a rendered reference stands in for, since it documents no one project.
SAMPLE_NAME = "<project>"


class UnknownHarness(KeyError):
    """Raised for a harness Inzaghi does not know how to install into."""


@dataclass(frozen=True, slots=True)
class Harness:
    """Where one agent harness looks for skills."""

    name: str
    #: Installed for every session this user starts.
    user_dir: Path
    #: Installed for one project only, relative to that project's root.
    project_dir: str
    layout: str

    def destination(self, project: Path | None = None) -> Path:
        base = (project / self.project_dir) if project else self.user_dir.expanduser()
        return base / SKILL_NAME


HARNESSES: dict[str, Harness] = {
    "claude-code": Harness(
        name="claude-code",
        user_dir=Path("~/.claude/skills"),
        project_dir=".claude/skills",
        layout="a directory holding SKILL.md with YAML front matter",
    ),
}


def render_reference() -> str:
    """The channel contract as the skill ships it, from the one source."""
    return CHANNEL_README.format(name=SAMPLE_NAME, version=PROTOCOL_VERSION)


def sync_reference() -> bool:
    """Rewrite the bundled contract from ``protocol.py``. True if it changed."""
    rendered = render_reference()
    if REFERENCE.exists() and REFERENCE.read_text(encoding="utf-8") == rendered:
        return False
    REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE.write_text(rendered, encoding="utf-8")
    return True


def install(
    harness: str = "claude-code",
    *,
    project: Path | None = None,
    link: bool = True,
    force: bool = False,
) -> tuple[Path, str]:
    """Put the skill where ``harness`` will find it. Returns (path, how).

    Linking by default, so the skill a session reads is the one in the repo:
    edit it and every harness that points at it is already up to date.
    """
    try:
        target = HARNESSES[harness]
    except KeyError:
        raise UnknownHarness(harness) from None

    destination = target.destination(Path(project).expanduser() if project else None)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() or destination.is_symlink():
        if not force:
            raise FileExistsError(destination)
        _remove_existing(destination)

    if link:
        destination.symlink_to(SOURCE.resolve(), target_is_directory=True)
        return destination, "linked"
    shutil.copytree(SOURCE, destination)
    return destination, "copied"


def _remove_existing(destination: Path) -> None:
    """Replace only something that is plausibly a previous install of this skill.

    ``--force`` should never be a way to delete an unrelated directory that
    happens to sit at the destination path.
    """
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
        return
    if not (destination / "SKILL.md").is_file():
        raise FileExistsError(
            f"{destination} exists and does not look like an installed skill; "
            "remove it yourself if that is what you want"
        )
    shutil.rmtree(destination)
