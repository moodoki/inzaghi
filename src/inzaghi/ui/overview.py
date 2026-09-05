"""The overview panel: every channel on one line.

This is the screen you leave open.  It answers, per channel, the only four
questions that matter between wakeups: is it alive, when does it speak next,
does it need me, and is there anything I have not read.
"""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Static

from .. import fmt
from ..channel import Channel
from ..model import Snapshot
from .rows import HEALTH_STYLE

_COLUMNS = ("", "channel", "heartbeat", "next update", "", "state")


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

    def update(
        self,
        channels: list[Channel],
        snapshots: dict[str, Snapshot],
        unread: dict[str, int],
        now: datetime,
    ) -> None:
        table = self.query_one(DataTable)
        cursor = table.cursor_row
        table.clear()
        needs_you = 0
        for channel in channels:
            snapshot = snapshots.get(channel.key)
            if snapshot is None:
                table.add_row(Text("·", "dim"), channel.name, "", "", "", Text("scanning…", "dim"), key=channel.key)
                continue
            if snapshot.attention(now):
                needs_you += 1
            table.add_row(*_row(channel, snapshot, unread.get(channel.key, 0), now), key=channel.key)

        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
        unread_total = sum(unread.values())
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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value:
            self.post_message(self.Open(str(event.row_key.value)))


def _row(channel: Channel, snapshot: Snapshot, unread: int, now: datetime) -> tuple[Text, ...]:
    health = snapshot.health(now)
    mark, colour = HEALTH_STYLE[health]
    heartbeat = snapshot.heartbeat

    name = Text(channel.name, "bold" if snapshot.attention(now) else "")
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
        flags.append(f"{unread}", "bold")
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
