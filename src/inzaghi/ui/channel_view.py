"""One channel: liveness strip, timeline, reader."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.binding import Binding
from textual.widgets import Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from .. import fmt
from ..channel import Channel
from ..model import Snapshot
from .rows import (
    ALL,
    HEALTH_STYLE,
    Filter,
    Row,
    build_rows,
    divider_label,
    filter_bar,
    kind_cycle,
    unread_divider,
)

def _line(markup: str) -> Text:
    """One row, clipped rather than wrapped: the timeline is a list, not prose."""
    text = Text.from_markup(markup)
    text.no_wrap = True
    text.overflow = "ellipsis"
    return text


_EMPTY = "*Nothing here yet.*\n\nThe session has not written anything to this channel."


class ChannelPane(Vertical):
    """The per-channel view.

    Rebuilding is driven by a poll, so it takes care not to move the cursor or
    the reader's scroll position under someone who is reading: the timeline is
    only rebuilt when the set of rows or their labels actually changed.
    """

    class Read(Message):
        """A row was displayed long enough to count as read."""

        def __init__(self, channel_key: str, path: str) -> None:
            super().__init__()
            self.channel_key = channel_key
            self.path = path

    BINDINGS = [
        Binding("slash", "search", "Search"),
        Binding("f", "cycle_kind", "Filter"),
        Binding("F", "cycle_kind(-1)", "Filter back", show=False),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    def __init__(self, channel: Channel, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channel = channel
        self.snapshot: Snapshot | None = None
        self.filter = Filter()
        self._all_rows: list[Row] = []
        self._rows: list[Row] = []
        self._labels: list[str] = []
        self._divider: tuple[int, int] | None = None
        self._selected: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="strip", markup=True)
        yield Static("", id="filterbar", markup=True)
        yield Input(placeholder="search this channel", id="search")
        with Horizontal(id="channel-body"):
            yield OptionList(id="timeline")
            with VerticalScroll(id="reader"):
                yield Markdown(_EMPTY, id="doc")

    # -- updating ---------------------------------------------------------

    def on_mount(self) -> None:
        self.query_one("#search", Input).display = False

    def update(self, snapshot: Snapshot, unread: set[str], now: datetime) -> None:
        self.snapshot = snapshot
        self._all_rows = build_rows(snapshot, unread, now)
        self._apply_filter()
        self.update_strip(now)

    def _apply_filter(self) -> None:
        """Re-render only when the visible rows or their labels actually change.

        The poll runs every couple of seconds; rebuilding unconditionally would
        move the cursor under someone mid-read.
        """
        rows = self.filter.apply(self._all_rows)
        labels = [row.label for row in rows]
        divider = unread_divider(rows)
        if (labels, divider) != (self._labels, self._divider):
            self._rows, self._labels, self._divider = rows, labels, divider
            self._render_rows()
        searched = self.filter.with_kind(ALL).apply(self._all_rows)
        self.query_one("#filterbar", Static).update(filter_bar(searched, self.filter))

    def _render_rows(self) -> None:
        timeline = self.query_one(OptionList)
        timeline.clear_options()
        options: list[Option] = []
        pinned_done = False
        for index, row in enumerate(self._rows):
            if not row.pinned and not pinned_done and options:
                # A rule between the live panels and the append-only log.
                options.append(Option(Text("─" * 4, "dim"), disabled=True))
            pinned_done = pinned_done or not row.pinned
            options.append(Option(_line(row.label), id=row.key))
            if self._divider and index == self._divider[0]:
                options.append(
                    Option(_line(divider_label(self._divider[1])), disabled=True)
                )
        if not options:
            options.append(Option(Text("no matches", "dim italic"), disabled=True))
        timeline.add_options(options)
        target = self._index_of(self._selected)
        if target is None and self._rows:
            target = 0
        if target is not None:
            timeline.highlighted = target
            self._show(self._rows[target])

    def update_strip(self, now: datetime) -> None:
        """Refresh only the liveness line, which changes every second."""
        snapshot = self.snapshot
        if snapshot is None:
            return
        mark, colour = HEALTH_STYLE[snapshot.health(now)]
        heartbeat = snapshot.heartbeat
        parts = [f"[{colour}]{mark}[/]"]
        if heartbeat:
            parts.append(f"heard {fmt.ago(heartbeat.updated, now)}")
            parts.append(f"[{colour}]{fmt.countdown(heartbeat.next_by, now)}[/]")
            if heartbeat.state:
                parts.append(f"[dim]{_escape(heartbeat.state)}[/]")
        else:
            parts.append("[dim]no heartbeat file[/]")
        if self.channel.read_only:
            parts.append("[dim italic]read-only[/]")

        line = "  ·  ".join(parts)
        if snapshot.waiting:
            line += f"\n[bold red]waiting on you:[/] {_escape(snapshot.waiting.splitlines()[0])}"
        if snapshot.conflicts:
            count = len(snapshot.conflicts)
            noun = "copy" if count == 1 else "copies"
            hint = "ignored" if self.channel.read_only else "press k to delete"
            line += f"\n[yellow]{count} sync-conflict {noun}[/] [dim]· {hint}[/]"
        self.query_one("#strip", Static).update(line)

    # -- selection --------------------------------------------------------

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id is None:
            return
        self._selected = event.option.id
        row = self._row_for(event.option.id)
        if row is not None:
            self._show(row)
            if row.unread:
                self.post_message(self.Read(self.channel.key, row.key))

    def _show(self, row: Row) -> None:
        self.query_one("#doc", Markdown).update(row.doc.body or _EMPTY)
        self.query_one("#reader", VerticalScroll).scroll_home(animate=False)

    def _row_for(self, key: str) -> Row | None:
        return next((row for row in self._rows if row.key == key), None)

    def _index_of(self, key: str | None) -> int | None:
        if key is None:
            return None
        return next((i for i, row in enumerate(self._rows) if row.key == key), None)

    # -- filtering --------------------------------------------------------

    def action_cycle_kind(self, step: int = 1) -> None:
        cycle = kind_cycle(self._all_rows)
        try:
            index = cycle.index(self.filter.kind)
        except ValueError:
            index = 0
        self.filter = self.filter.with_kind(cycle[(index + step) % len(cycle)])
        self._apply_filter()

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()

    def action_clear_filter(self) -> None:
        """One key back to the whole log, whatever combination got you here."""
        search = self.query_one("#search", Input)
        search.value = ""
        search.display = False
        self.filter = Filter()
        self._apply_filter()
        self.focus_timeline()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.filter = self.filter.with_query(event.value)
        self._apply_filter()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter keeps the search applied but gives the rows their space back.

        The query stays visible in the filter bar, and pressing ``/`` again
        reopens the box with it still in place.
        """
        self.query_one("#search", Input).display = False
        self.focus_timeline()

    def focus_timeline(self) -> None:
        self.query_one(OptionList).focus()


def _escape(text: str) -> str:
    return text.replace("[", "\\[")
