"""Local read-state.

Read receipts live on this machine, never in the channel.  The folder is a
shared workspace that a live session also writes; adding bookkeeping files to it
would put us in a race with the session and pollute its view of its own inbox.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import state_dir
from .model import Event, Snapshot


@dataclass
class ReadState:
    """Which files have been seen, keyed by channel and content signature."""

    path: Path
    #: channel key -> file path -> "<mtime>:<size>" at the time it was read
    seen: dict[str, dict[str, str]] = field(default_factory=dict)
    _dirty: bool = field(default=False, repr=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "ReadState":
        path = path or (state_dir() / "read.json")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path=path)
        seen = {
            str(channel): {str(f): str(sig) for f, sig in files.items()}
            for channel, files in raw.get("seen", {}).items()
            if isinstance(files, dict)
        }
        return cls(path=path, seen=seen)

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "seen": self.seen}, indent=1, sort_keys=True)
        _atomic_write(self.path, payload)
        self._dirty = False

    def is_read(self, channel_key: str, event: Event) -> bool:
        return self.seen.get(channel_key, {}).get(str(event.path)) == _signature(event)

    def mark_read(self, channel_key: str, event: Event) -> None:
        entry = self.seen.setdefault(channel_key, {})
        signature = _signature(event)
        if entry.get(str(event.path)) != signature:
            entry[str(event.path)] = signature
            self._dirty = True

    def mark_all_read(self, channel_key: str, snapshot: Snapshot) -> None:
        for event in snapshot.events:
            self.mark_read(channel_key, event)

    def unread(self, channel_key: str, snapshot: Snapshot) -> list[Event]:
        return [e for e in snapshot.events if not self.is_read(channel_key, e)]

    def forget(self, channel_key: str) -> None:
        """Drop a whole channel's receipts, so the file does not grow forever.

        Deliberately whole-channel. A receipt is keyed by path *and* content
        signature, so one pointing at a file that is not there costs a line and
        resurrects correctly if the file comes back -- whereas a notification
        missing from one scan of a synced folder is not evidence that it is
        gone, and dropping its receipt would make something already read
        reappear as new. Which channels to forget is decided in the app, from
        the config and from an absence that has been corroborated.
        """
        if self.seen.pop(channel_key, None) is not None:
            self._dirty = True


def _signature(event: Event) -> str:
    return f"{event.doc.mtime:.0f}:{event.doc.size}"


def _atomic_write(path: Path, text: str) -> None:
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
