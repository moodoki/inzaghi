"""One channel: liveness strip, timeline, reader."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.binding import Binding
from textual.widgets import Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option, OptionDoesNotExist

from .. import fmt
from ..channel import Channel
from ..model import Snapshot
from .composer import Composer
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

    class Send(Message):
        """A finished draft, on its way to the app that owns the write."""

        def __init__(self, channel_key: str, text: str) -> None:
            super().__init__()
            self.channel_key = channel_key
            self.text = text

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
        self._signature: list[tuple] = []
        self._divider: tuple[int, int] | None = None
        self._selected: str | None = None
        #: (key, mtime, size) of what the reader is showing, so an unchanged
        #: document is never re-rendered and never scrolled back to the top.
        self._showing: tuple | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="strip", markup=True)
        yield Static("", id="filterbar", markup=True)
        yield Input(placeholder="search this channel", id="search")
        with Horizontal(id="channel-body"):
            with Vertical(id="left"):
                yield OptionList(id="timeline")
                yield Composer(id="composer")
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
        """Update the timeline without disturbing whoever is reading it.

        Rebuilding the list moves the cursor, so it must happen only when the
        list actually differs -- which is a question about *which rows exist*,
        not about how they currently render. Labels embed relative times
        ("4m ago"), so they change on nearly every poll while the rows behind
        them are identical; those are written into place instead.
        """
        rows = self.filter.apply(self._all_rows)
        labels = [row.label for row in rows]
        signature = [(row.key, row.unread, row.kind, row.pinned) for row in rows]
        divider = unread_divider(rows)

        self._rows = rows
        if (signature, divider) != (self._signature, self._divider):
            self._signature, self._divider = signature, divider
            self._labels = labels
            self._render_rows()
        elif labels != self._labels:
            self._retitle(labels)

        searched = self.filter.with_kind(ALL).apply(self._all_rows)
        self.query_one("#filterbar", Static).update(filter_bar(searched, self.filter))

    def _retitle(self, labels: list[str]) -> None:
        """Rewrite changed row labels in place, leaving the cursor alone."""
        timeline = self.query_one(OptionList)
        for row, label, previous in zip(self._rows, labels, self._labels):
            if label == previous:
                continue
            try:
                timeline.replace_option_prompt(row.key, _line(label))
            except OptionDoesNotExist:
                self._render_rows()  # the list moved under us; rebuild instead
                return
        self._labels = labels

    def _render_rows(self) -> None:
        timeline = self.query_one(OptionList)
        # Read the selection before clearing: emptying and refilling the list
        # highlights the first row, which fires the handler below and would
        # otherwise overwrite the very thing being restored.
        selected = self._selected
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

        # Restore by key, never by position: the list also holds the pinned/log
        # rule and the unread divider, so a row's index among the rows is not
        # its index among the options.
        row = self._row_for(selected) or (self._rows[0] if self._rows else None)
        if row is None:
            self._selected = None
            return
        try:
            timeline.highlighted = timeline.get_option_index(row.key)
        except OptionDoesNotExist:  # pragma: no cover -- just added it
            return
        self._selected = row.key
        self._show(row)

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
        """Put a document in the reader, scrolling only when it is a new one.

        A document that was rewritten while being read is re-rendered in place;
        jumping back to the top of a status file every time the session
        refreshes it would make it unreadable.
        """
        stamp = (row.key, row.doc.mtime, row.doc.size)
        if stamp == self._showing:
            return
        is_new_document = self._showing is None or self._showing[0] != row.key
        self._showing = stamp
        self.query_one("#doc", Markdown).update(row.doc.body or _EMPTY)
        if is_new_document:
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

    # -- composing --------------------------------------------------------

    @property
    def composer(self) -> Composer:
        return self.query_one(Composer)

    def open_composer(self, due: str) -> None:
        self.composer.open(self.channel.name, due)

    def close_composer(self, *, clear: bool = True) -> None:
        self.composer.close(clear=clear)

    def on_composer_send(self, event: Composer.Send) -> None:
        """Hand the draft up; the app owns writes and the read-only guard."""
        event.stop()
        self.post_message(self.Send(self.channel.key, event.text))

    def on_composer_closed(self, event: Composer.Closed) -> None:
        event.stop()
        self.focus_timeline()


def _escape(text: str) -> str:
    return text.replace("[", "\\[")
