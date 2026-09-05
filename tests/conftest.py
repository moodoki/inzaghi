"""Fixture channels built from miniature files, never from a live folder."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from inzaghi.channel import Channel

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 5, 6, 0, tzinfo=TZ)


def write(path: Path, text: str, *, mtime: datetime | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip("\n"), encoding="utf-8")
    if mtime:
        stamp = mtime.timestamp()
        os.utime(path, (stamp, stamp))
    return path


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Never touch the real ~/.local/state or ~/.config while testing.

    Lives here rather than beside the UI tests: anything that builds an
    InzaghiApp loads and saves read receipts, and a fixture in one module does
    not cover the others that do it.
    """
    monkeypatch.setenv("INZAGHI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("INZAGHI_CONFIG", str(tmp_path / "config" / "inzaghi.toml"))


@pytest.fixture
def channel_root(tmp_path: Path) -> Path:
    """A channel with a heartbeat, a status, a log, and one answered message."""
    root = tmp_path / "northwind"
    notifications = root / "notifications"
    inbox = root / "inbox"
    (inbox / "done").mkdir(parents=True)

    write(
        notifications / "HEARTBEAT.md",
        """
# northwind session heartbeat

- **updated:** 2026-09-05T05:57:46+08:00
- **next update expected by:** 2026-09-05T06:27:46+08:00 (+30 min)
- **state:** shard 3 of 8 reindexing on worker-2
""",
    )
    write(
        notifications / "STATUS.md",
        """
# northwind status

*updated 2026-09-05T05:57:46+08:00*

## Where the run is
- Shard 3 of 8 reindexed; throughput steady.

## Waiting on you
Nothing.
""",
    )
    write(notifications / "TASK_OVERVIEW.md", "# task overview\n\n| Task | Status |\n|---|---|\n")
    write(
        notifications / "2026-09-04_2325_milestone_shard-2-reindexed.md",
        "# [milestone] Shard 2 reindexed\n\nChecksums verified against the manifest.\n",
    )
    write(
        notifications / "2026-09-04_2304_ack_re-2026-09-04-heartbeat-md.md",
        "# [ack] re: 2026-09-04-heartbeat.md\n\n*2026-09-04T23:04:47+08:00*\n\nDone.\n",
    )
    write(
        inbox / "done" / "2026-09-04_2304_2026-09-04-heartbeat.md",
        "also create a HEARTBEAT.md every 30 min\n",
        mtime=datetime(2026, 9, 4, 12, 28, tzinfo=TZ),
    )
    return root


@pytest.fixture
def channel(channel_root: Path) -> Channel:
    return Channel(root=channel_root)
