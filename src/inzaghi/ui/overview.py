"""The overview panel: every channel on one line.

This is the screen you leave open.  It answers, per channel, the only four
questions that matter between wakeups: is it alive, when does it speak next,
does it need me, and is there anything I have not read.
"""

from __future__ import annotations

import random
from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import DataTable, Static

from .. import fmt
from ..channel import Channel
from ..model import Snapshot
from .mounting import composed
from .rows import HEALTH_STYLE

_COLUMNS = ("", "channel", "heartbeat", "next update", "", "state")

#: Home to the loft, message still attached.
PIGEON = r"""
   .-.
  ( o )>
   \   `--._
    \       `--.___
     \              `--.
      \      .-"-.      `.
       \    /     \       \
        \  |       |       |
         \  \     /       /
          `. `---'      ,'
            `-.______.-'
               ||  ||
              _||__||_
   ~~~~~~~~~~~~~~~~~~~~~~~~
""".strip("\n")

#: The same bird with its eye shut. Derived rather than drawn a second time, so
#: redrawing the pigeon cannot leave the two out of step -- and if the eye ever
#: stops matching, the blink quietly does nothing rather than showing a
#: different bird.
PIGEON_BLINK = PIGEON.replace("( o )", "( - )")

#: Long enough to read as a blink, short enough not to look like a glitch.
BLINK_SHUT_SECONDS = 0.12
#: Range between blinks. Irregular, because a metronome reads as a cursor.
BLINK_GAP_SECONDS = (3.0, 9.0)

PIGEON_HEIGHT = len(PIGEON.splitlines())
PIGEON_WIDTH = max(len(line) for line in PIGEON.splitlines())
#: Rows the table needs before the bird: summary, blank, header.
_TABLE_CHROME = 4


class OverviewPane(Vertical):
    """A table of channels; selecting one opens its tab."""

    class Open(Message):
        """Raised when a channel row is chosen."""

        def __init__(self, channel_key: str) -> None:
            super().__init__()
            self.channel_key = channel_key

    def compose(self) -> ComposeResult:
        yield Static("", id="overview-summary")
        table = DataTable(id="overview-table", cursor_type="row", zebra_stripes=False)
        table.add_columns(*_COLUMNS)
        yield table
        with Horizontal(id="pigeon-dock"):
            yield Static(PIGEON, id="pigeon", markup=False)

    def update(
        self,
        channels: list[Channel],
        snapshots: dict[str, Snapshot],
        unread: dict[str, set[str]],
        now: datetime,
    ) -> None:
        if not composed(self, "#overview-table"):
            return
        table = self.query_one(DataTable)
        cursor = table.cursor_row
        table.clear()
        needs_you = 0
        for channel in channels:
            snapshot = snapshots.get(channel.key)
            if snapshot is None:
                table.add_row(Text("·", "dim"), channel.name, "", "", "", Text("scanning…", "dim"), key=channel.key)
                continue
            seen = unread.get(channel.key, set())
            if snapshot.attention(now, seen):
                needs_you += 1
            table.add_row(*_row(channel, snapshot, seen, now), key=channel.key)

        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
        self._fit_pigeon(len(channels))
        unread_total = sum(len(paths) for paths in unread.values())
        self.query_one("#overview-summary", Static).update(
            Text.from_markup(
                f"[b]{len(channels)}[/] channel{'s' if len(channels) != 1 else ''}"
                + (f" · [bold red]{needs_you} need you[/]" if needs_you else " · [green]none waiting[/]")
                + (f" · {unread_total} unread" if unread_total else "")
            )
        )

    def focus_table(self) -> None:
        self.query_one(DataTable).focus()

    def on_mount(self) -> None:
        self.focus_table()
        self._schedule_blink()

    # -- the bird -----------------------------------------------------------

    def _schedule_blink(self) -> None:
        self.set_timer(random.uniform(*BLINK_GAP_SECONDS), self._blink)

    def _blink(self) -> None:
        """Shut the eye, and open it again shortly after.

        Skipped whole while the bird is not on show: it is decoration, and
        decoration should not cost a redraw nobody can see.
        """
        if not self.query_one("#pigeon-dock").display:
            self._schedule_blink()
            return
        self.query_one("#pigeon", Static).update(PIGEON_BLINK)
        self.set_timer(BLINK_SHUT_SECONDS, self._open_eye)

    def _open_eye(self) -> None:
        self.query_one("#pigeon", Static).update(PIGEON)
        self._schedule_blink()

    def on_resize(self) -> None:
        self._fit_pigeon(self.query_one(DataTable).row_count)

    def _fit_pigeon(self, channel_count: int) -> None:
        """Show the bird only where it costs nothing.

        Decoration must never push a channel off the screen, so it appears only
        when the table has already been given every row it needs.
        """
        spare = self.size.height - channel_count - _TABLE_CHROME
        self.query_one("#pigeon-dock").display = (
            spare >= PIGEON_HEIGHT and self.size.width >= PIGEON_WIDTH + 4
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value:
            self.post_message(self.Open(str(event.row_key.value)))


def _row(channel: Channel, snapshot: Snapshot, unread: set[str], now: datetime) -> tuple[Text, ...]:
    health = snapshot.health(now)
    mark, colour = HEALTH_STYLE[health]
    heartbeat = snapshot.heartbeat

    name = Text(channel.name, "bold" if snapshot.attention(now, unread) else "")
    if channel.read_only:
        name.append("  r/o", "dim italic")

    next_update = Text(
        fmt.countdown(heartbeat.next_by, now) if heartbeat else "—",
        "bold red" if health == "stale" else "yellow" if health == "late" else "dim",
    )

    flags = Text()
    if snapshot.waiting:
        flags.append("!", "bold red")
    if unread:
        flags.append(f"{len(unread)}", "bold")
    if snapshot.in_flight:
        flags.append("^", "bright_blue")

    state = snapshot.waiting or (heartbeat.state if heartbeat else None) or (
        snapshot.status.headline if snapshot.status else ""
    )
    return (
        Text(mark, colour),
        name,
        Text(fmt.ago(heartbeat.updated if heartbeat else None, now), "dim"),
        next_update,
        flags,
        Text(_one_line(state), "red" if snapshot.waiting else ""),
    )


def _one_line(text: str, limit: int = 90) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"
