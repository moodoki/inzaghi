"""Fixture channels built from miniature files, never from a live folder."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from inzaghi.channel import Channel

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 5, 6, 0, tzinfo=TZ)

# Every timestamp below is written in ``TZ``, and so is ``NOW``. A filename
# stamp carries no zone, so ``parse._stamp_to_dt`` reads one as *local* time
# -- which is right, and which quietly makes the fixtures mean "local is
# +08:00". Run the suite anywhere else and a local reading is compared against
# a +08:00 literal: ``round_trip`` comes out an offset too long, and an event
# sorted by a local stamp changes places with one carrying an explicit zone,
# so an assertion about ``events[0]`` reads a different event entirely.
#
# So pin it, rather than write every fixture twice. Asia/Singapore is +08:00
# the year round -- no DST to move underneath a literal -- and this is set at
# import, before a fixture has read the clock. The project ships for macOS and
# Linux, both of which have ``tzset``.
os.environ["TZ"] = "Asia/Singapore"
time.tzset()
assert datetime.now().astimezone().utcoffset() == TZ.utcoffset(None), (
    "the suite's fixtures are written in +08:00 and the local zone is not"
)


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
