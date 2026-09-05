"""Configuration and channel discovery.

Channels are found by scanning sync roots for folders that carry both halves of
the contract, so a new project shows up without an edit here.  Explicit entries
exist for the ones that live outside a root, or that need a display name or a
``read_only`` guard.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .channel import Channel, is_channel

DEFAULT_POLL_SECONDS = 2.0
#: Discovery costs a listing of each root, so it runs far less often than
#: the file poll.  New channels are rare; new files are not.
DEFAULT_DISCOVER_SECONDS = 30.0
DEFAULT_SCAN_DEPTH = 2


def config_path() -> Path:
    if override := os.environ.get("INZAGHI_CONFIG"):
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "inzaghi" / "config.toml"


def state_dir() -> Path:
    if override := os.environ.get("INZAGHI_STATE_DIR"):
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return base / "inzaghi"


@dataclass(frozen=True, slots=True)
class Alerts:
    """Which events are worth interrupting you for."""

    hard_stop: bool = True
    error: bool = True
    heartbeat_late: bool = False
    waiting: bool = True
    bell: bool = True
    banner: bool = False  # macOS notification centre


@dataclass(frozen=True, slots=True)
class RootSpec:
    path: Path
    read_only: bool = False
    depth: int = DEFAULT_SCAN_DEPTH


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    path: Path
    name: str = ""
    read_only: bool = False


@dataclass(slots=True)
class Config:
    roots: list[RootSpec] = field(default_factory=list)
    channels: list[ChannelSpec] = field(default_factory=list)
    poll_seconds: float = DEFAULT_POLL_SECONDS
    discover_seconds: float = DEFAULT_DISCOVER_SECONDS
    alerts: Alerts = field(default_factory=Alerts)
    #: Where this config came from, or None for one built in memory.
    source: Path | None = None
    #: False when ``source`` names a file that does not exist yet.
    loaded: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        if not path.is_file():
            # Remember where it would live, so a config written later is picked
            # up by the next reload rather than needing a restart.
            return cls(source=path)
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        return cls(
            roots=[
                RootSpec(
                    path=Path(str(entry["path"])).expanduser(),
                    read_only=bool(entry.get("read_only", False)),
                    depth=int(entry.get("depth", DEFAULT_SCAN_DEPTH)),
                )
                for entry in raw.get("roots", [])
                if entry.get("path")
            ],
            channels=[
                ChannelSpec(
                    path=Path(str(entry["path"])).expanduser(),
                    name=str(entry.get("name", "")),
                    read_only=bool(entry.get("read_only", False)),
                )
                for entry in raw.get("channels", [])
                if entry.get("path")
            ],
            poll_seconds=float(raw.get("poll_seconds", DEFAULT_POLL_SECONDS)),
            discover_seconds=float(raw.get("discover_seconds", DEFAULT_DISCOVER_SECONDS)),
            alerts=Alerts(**{k: bool(v) for k, v in raw.get("alerts", {}).items()}),
            source=path,
            loaded=True,
        )

    def reload(self) -> "Config":
        """Re-read the config file, keeping the current one if it will not parse.

        A config being edited is momentarily truncated or invalid, and that is
        no reason to drop every channel out of a running app.
        """
        if self.source is None:
            return self  # built in memory; there is no file behind it
        try:
            return Config.load(self.source)
        except (OSError, ValueError):
            return self

    def discover(self) -> list[Channel]:
        """All channels, explicit entries taking precedence over scanned ones.

        Order is by display name so tabs do not reshuffle when a folder's mtime
        changes underneath us.
        """
        found: dict[Path, Channel] = {}
        for root in self.roots:
            for path in scan_root(root.path, root.depth):
                found[path] = Channel(root=path, read_only=root.read_only)
        for spec in self.channels:
            path = spec.path.expanduser()
            if is_channel(path):
                found[path] = Channel(root=path, name=spec.name, read_only=spec.read_only)
        return sorted(found.values(), key=lambda c: c.name.lower())


def scan_root(root: Path, depth: int = DEFAULT_SCAN_DEPTH) -> list[Path]:
    """Folders under ``root`` (to ``depth`` levels) that look like channels."""
    root = Path(root).expanduser()
    if not root.is_dir():
        return []
    if is_channel(root):
        return [root]
    out: list[Path] = []
    frontier = [(root, 0)]
    while frontier:
        current, level = frontier.pop()
        if level >= depth:
            continue
        try:
            entries = sorted(p for p in current.iterdir() if p.is_dir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name in {"node_modules", "inbox", "notifications"}:
                continue
            if is_channel(entry):
                out.append(entry)
            else:
                frontier.append((entry, level + 1))
    return sorted(out)
