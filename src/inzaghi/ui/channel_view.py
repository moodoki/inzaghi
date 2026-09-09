"""One channel: liveness strip, timeline, reader."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.content import Content, Span
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option, OptionDoesNotExist

from . import find
from .. import attach, fmt, parse
from ..channel import Channel
from ..model import Attachment, Snapshot
from .composer import Composer
from .mounting import composed
from .preview import Preview
from .rows import (
    ALL,
    HEALTH_STYLE,
    Filter,
    Row,
    attachment_label,
    build_rows,
    divider_label,
    filter_bar,
    kind_cycle,
    unread_divider,
)

#: Two option lists live in this pane, and every handler below has to say
#: which one it means.
TIMELINE = "timeline"
ATTACHMENTS = "attachments"

#: The panes the keyboard can reach, as they sit on screen: two columns, each
#: listed top to bottom, naming the widget in each that actually takes focus.
#: This is the map ``ctrl+w`` and a direction walk, so it has to stay in the
#: order the layout puts them in -- and a pane that is not on screen is not
#: somewhere the cursor can go, so hidden ones drop out as they are read.
COLUMNS = (
    (f"#{TIMELINE}", "#compose-text"),
    ("#reader", f"#{ATTACHMENTS}", "#preview-body"),
)

#: The two panes that hold a document rather than a list, and so are the two
#: places ``/`` means "find this in what I am reading".
READING = {"reader": "doc", "preview-body": "file"}

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

    class Open(Message):
        """An attachment someone asked to see, on its way to the app.

        The app owns it for the same reason it owns writes: showing a file
        means either launching something or reading it, and both wait on the
        channel's volume.
        """

        def __init__(
            self, channel_key: str, attachment: Attachment, *, refresh: bool = False
        ) -> None:
            super().__init__()
            self.channel_key = channel_key
            self.attachment = attachment
            #: A re-read of something already on screen rather than somebody
            #: asking to see it: it must not take the keyboard or the scroll
            #: position away from whoever is reading.
            self.refresh = refresh

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
        Binding("v", "attachments", "Files"),
        # Vim's search keys. They work from anywhere in the pane, so a query
        # typed in the reader can still be stepped through after the keyboard
        # has moved on -- which is what hlsearch does.
        Binding("n", "find_next(1)", "Next match", show=False),
        Binding("N", "find_next(-1)", "Prev match", show=False),
    ]

    def __init__(self, channel: Channel, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channel = channel
        self.snapshot: Snapshot | None = None
        self._unread: set[str] = set()
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
        #: The in-document search, which is a different question from
        #: ``self.filter``: that one asks which entries mention a word, this
        #: one asks where in the entry on screen it says it.
        self._find = ""
        self._matches: list[find.Match] = []
        self._match: int | None = None
        #: Which pane the query is running in -- ``doc`` for the reader,
        #: ``file`` for an opened attachment.
        self._find_in = "doc"
        #: The block currently tinted, kept so it can be untinted: a rendered
        #: document has no cursor to move, so the highlight *is* the cursor.
        self._lit: Widget | None = None
        #: What the strip beneath the reader is listing, and for which row.
        #: Tracked apart from the document: an attachment arrives long after
        #: the notification that announced it, and only the strip changes.
        self._attachments: tuple[Attachment, ...] = ()
        #: Whether the strip's cursor is where somebody put it, as opposed to
        #: where filling the list left it.
        self._stepped = False

    def compose(self) -> ComposeResult:
        yield Static("", id="strip", markup=True)
        yield Static("", id="filterbar", markup=True)
        yield Input(placeholder="search this channel", id="search")
        with Horizontal(id="channel-body"):
            with Vertical(id="left"):
                yield OptionList(id=TIMELINE)
                yield Composer(id="composer")
            with Vertical(id="reader-column"):
                with VerticalScroll(id="reader"):
                    # ``open_links=False``: left on, the widget hands every
                    # href it is clicked on straight to ``app.open_url`` --
                    # the web browser, or xdg-open -- before this pane hears
                    # about it. A channel is written by an unattended session,
                    # so what a link does here is decided below and nowhere
                    # else.
                    yield Markdown(_EMPTY, id="doc", open_links=False)
                # Outside the scroll: what a notification delivered belongs to
                # the notification, not to whatever part of it is on screen.
                yield OptionList(id=ATTACHMENTS)
                # Beneath the list, so that a file the reader can render sits
                # directly under the line that named it.
                yield Preview(id="preview")
                # One row at the bottom of the column being searched, where
                # vim puts it, and hidden until asked for.
                with Horizontal(id="find-bar"):
                    yield Input(placeholder="find in this document", id="find")
                    yield Static("", id="find-count", markup=True)

    # -- updating ---------------------------------------------------------

    def on_mount(self) -> None:
        self.query_one("#search", Input).display = False
        self.query_one("#find-bar").display = False
        self.query_one(f"#{ATTACHMENTS}", OptionList).display = False
        if self.snapshot is not None:  # a scan that landed while mounting
            self.update(self.snapshot, self._unread, self.snapshot.scanned_at)

    def update(self, snapshot: Snapshot, unread: set[str], now: datetime) -> None:
        self.snapshot = snapshot
        self._unread = unread
        if not composed(self, "#strip"):
            return  # kept, and written out by on_mount above
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
            self._render_rows()  # ends by showing the row it restored
        else:
            if labels != self._labels:
                self._retitle(labels)
            # Whether or not the list moved, what the *selected* entry holds
            # may have. A rewritten status file and a payload that has just
            # finished syncing both leave the timeline identical. ``_show``
            # compares before it writes, so this costs nothing when nothing
            # changed -- which is most polls.
            selected = self._row_for(self._selected)
            if selected is not None:
                self._show(selected)

        searched = self.filter.with_kind(ALL).apply(self._all_rows)
        self.query_one("#filterbar", Static).update(filter_bar(searched, self.filter))

    def _retitle(self, labels: list[str]) -> None:
        """Rewrite changed row labels in place, leaving the cursor alone."""
        timeline = self.query_one(f"#{TIMELINE}", OptionList)
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
        timeline = self.query_one(f"#{TIMELINE}", OptionList)
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
        if snapshot is None or not composed(self, "#strip"):
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
            hint = "ignored" if self.channel.read_only else "press K to delete"
            line += f"\n[yellow]{count} sync-conflict {noun}[/] [dim]· {hint}[/]"
        self.query_one("#strip", Static).update(line)

    # -- selection --------------------------------------------------------

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id != TIMELINE or event.option.id is None:
            return
        self._selected = event.option.id
        row = self._row_for(event.option.id)
        if row is not None:
            self._show(row)
            if row.unread:
                self.post_message(self.Read(self.channel.key, row.key))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter, or a click, on a delivered file asks to see it."""
        if event.option_list.id != ATTACHMENTS:
            return
        event.stop()
        self._open(self._attachment_at(event.option_index))

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        """A reference in the prose opens the same file the strip would.

        Where the session wrote the link is where someone's eye lands, so it
        has to work from there too. Anything else the document links to is
        left alone -- this is a channel reader, not a browser, and the widget
        is built with ``open_links=False`` so that nothing else can decide
        otherwise.
        """
        event.stop()
        if event.markdown.id != "doc":
            return  # a link inside a delivered file is the session's text, not ours
        name = attach.link_name(event.href)
        self._open(next((a for a in self._attachments if a.name == name), None))

    def _open(self, attachment: Attachment | None) -> None:
        """Hand one delivery up to the app, or say why it cannot be shown."""
        if attachment is None:
            return
        if not attachment.openable:
            self.notify(
                attachment.problem or f"{attachment.name} has not arrived yet",
                severity="warning",
            )
            return
        self.post_message(self.Open(self.channel.key, attachment))

    def _show(self, row: Row) -> None:
        """Put a document in the reader, scrolling only when it is a new one.

        A document that was rewritten while being read is re-rendered in place;
        jumping back to the top of a status file every time the session
        refreshes it would make it unreadable.
        """
        stamp = (row.key, row.doc.mtime, row.doc.size)
        if stamp != self._showing:
            is_new_document = self._showing is None or self._showing[0] != row.key
            self._showing = stamp
            self.query_one("#doc", Markdown).update(row.doc.body or _EMPTY)
            if self._find and self._find_in == "doc":
                # The blocks the tint was on have gone, and the text they were
                # rendered from may have changed. Recomputed after the refresh
                # rather than now: the new blocks are not mounted yet.
                self._lit = None
                self.call_after_refresh(
                    self._refind, keep=not is_new_document, jump=not is_new_document
                )
            if is_new_document:
                self.query_one("#reader", VerticalScroll).scroll_home(animate=False)
                # A delivered file belongs to the entry that announced it, so
                # it goes when the entry does rather than sitting under prose
                # that never mentioned it.
                self._close_preview()
                self._stepped = False
        # Checked separately from the document above: a payload lands minutes
        # after the notification that announced it, and re-rendering the prose
        # for that would throw away the reader's place for nothing.
        self._show_attachments(row.attachments)

    def _show_attachments(self, attachments: tuple[Attachment, ...]) -> None:
        """Refill the strip beneath the reader, only when it would differ.

        Compared by value, so ``waiting on sync`` becoming a size rewrites the
        line, while a poll that found the same files again leaves the cursor
        where it was.
        """
        if attachments == self._attachments:
            return
        self._attachments = attachments
        listing = self.query_one(f"#{ATTACHMENTS}", OptionList)
        held = self._highlighted_name(listing)
        listing.clear_options()
        listing.display = bool(attachments)
        if not attachments:
            if listing.has_focus:
                self.focus_timeline()
            self.refresh_bindings()
            return
        listing.add_options(
            [Option(_line(attachment_label(a)), id=a.name) for a in attachments]
        )
        # Restored by name, the way the timeline restores by option id: this
        # list is refilled whenever anything about a delivery changes, and a
        # session appending to a log it delivered changes one every poll.
        listing.highlighted = 0
        if held is not None:
            try:
                listing.highlighted = listing.get_option_index(held)
            except OptionDoesNotExist:
                pass  # that file is no longer delivered here
        self.refresh_bindings()
        self._follow_preview(attachments)

    def _follow_preview(self, attachments: tuple[Attachment, ...]) -> None:
        """Keep an open file in step with what the scan now says about it.

        The strip only differs when something about a delivery does, and for
        the one being read that means the text on screen is out of date: the
        session rewrote it, or it has only just finished crossing the sync.
        The re-read goes back out through the app, because reading the volume
        is the app's job and not this thread's.

        A file the notification no longer names, or that has stopped being
        readable, closes rather than sitting there as a stale copy of
        something that is no longer being claimed.
        """
        name = self.preview.showing_name
        if name is None:
            return
        current = next((a for a in attachments if a.name == name), None)
        if current is None or not current.readable:
            self._close_preview()
        elif not self.preview.is_current(current):
            # Only when this file changed. The strip is rebuilt whenever any
            # of the deliveries does, and re-reading the open one for a
            # sibling that finished syncing is a read for nothing.
            self.post_message(self.Open(self.channel.key, current, refresh=True))

    @property
    def preview(self) -> Preview:
        return self.query_one(Preview)

    def show_preview(
        self, attachment: Attachment, text: str, truncated: bool, *, focus: bool
    ) -> None:
        """Put a text delivery in the bottom of the reader.  Called by the app.

        Guarded like a timed refresh, and for the same reason: this arrives
        from a worker, so the tab it was read for may have gone since.
        """
        if not composed(self, "#preview"):
            return
        self.preview.show(attachment, text, truncated, focus=focus)
        if self._find and self._find_in == "file":
            # The text was replaced, so the matches and the highlight in it
            # were too.
            self.call_after_refresh(self._refind, keep=True)

    def _close_preview(self) -> None:
        """Close the open file, and forget a search that was running inside it."""
        self.preview.close()
        if self._find and self._find_in == "file":
            self._clear_find()

    def on_preview_closed(self, event: Preview.Closed) -> None:
        """The file had the keyboard; give it back to the list that opened it."""
        event.stop()
        if self._find and self._find_in == "file":
            self._clear_find()  # the text it matched against has gone
        listing = self.query_one(f"#{ATTACHMENTS}", OptionList)
        if listing.display:
            listing.focus()
        else:
            self.focus_timeline()

    @staticmethod
    def _highlighted_name(listing: OptionList) -> str | None:
        """Which delivery the strip's cursor is on, before the list is refilled."""
        index = listing.highlighted
        if index is None or not 0 <= index < listing.option_count:
            return None
        return listing.get_option_at_index(index).id

    # -- the files a document references ----------------------------------

    def step_reference(self, step: int) -> None:
        """``]f`` and ``[f``: the next file this notification points at.

        The strip's cursor is the selection, so it moves there -- lit, and
        already the thing ``gf`` and ``enter`` open -- and the prose scrolls
        to where the reference was written, which is the part of the document
        that the file is about.
        """
        if not self._attachments:
            return
        listing = self.query_one(f"#{ATTACHMENTS}", OptionList)
        if not self._stepped or listing.highlighted is None:
            # The strip lights its first row the moment it is filled, so
            # "where the cursor is" means nothing until somebody has moved it.
            # Until then ]f is the first file and [f is the last.
            self._stepped = True
            listing.highlighted = 0 if step > 0 else len(self._attachments) - 1
        else:
            listing.highlighted = (listing.highlighted + step) % len(self._attachments)
        self._scroll_to_reference(self._attachment_at(listing.highlighted))

    def open_reference(self) -> None:
        """``gf``: show the file the cursor is on, from wherever you are."""
        listing = self.query_one(f"#{ATTACHMENTS}", OptionList)
        self._open(self._attachment_at(listing.highlighted if self._attachments else None))

    def _scroll_to_reference(self, attachment: Attachment | None) -> None:
        """Put the line that names ``attachment`` on screen, if the prose does.

        A file named only in the front matter has no line in the body to scroll
        to, and the reader stays where it is rather than jumping somewhere
        arbitrary to say so.
        """
        row = self._row_for(self._selected) if self._selected else None
        if attachment is None or row is None:
            return
        # The targets that really point at this file, taken from the parse
        # that ignores code fences -- then the first line carrying one of
        # them. Line by line rather than by offset arithmetic, so a name
        # written in angle brackets is found where it was written.
        targets = {
            link.target
            for link in parse.markdown_links(row.doc.body)
            if attach.link_name(link.target) == attachment.name
        }
        if not targets:
            return
        for number, line in enumerate(row.doc.body.splitlines()):
            if not any(target in line for target in targets):
                continue
            block = find.block_for(self.query_one("#doc", Markdown), number)
            if block is not None:
                self.query_one("#reader", VerticalScroll).scroll_to_widget(
                    block, center=True, animate=False
                )
            return

    def _attachment_at(self, index: int | None) -> Attachment | None:
        if index is None or not 0 <= index < len(self._attachments):
            return None
        return self._attachments[index]

    def _row_for(self, key: str) -> Row | None:
        return next((row for row in self._rows if row.key == key), None)

    def _index_of(self, key: str | None) -> int | None:
        if key is None:
            return None
        return next((i for i, row in enumerate(self._rows) if row.key == key), None)

    def action_attachments(self) -> None:
        """Put the cursor on the files this entry delivered."""
        listing = self.query_one(f"#{ATTACHMENTS}", OptionList)
        if listing.display:
            listing.focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide the files key on an entry that delivered none.

        ``False`` rather than ``None``: most notifications are prose only, so a
        greyed-out key would sit in the footer nearly all the time.
        """
        if action == "attachments":
            return bool(self._attachments)
        return True

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
        """``/``: the channel, or the document, depending on where you are.

        In a pane holding a document it is the document -- which is what the
        key means in anything that shows one -- and everywhere else it stays
        the channel search it has always been.
        """
        if self._reading() is not None:
            self.action_find()
            return
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()

    def action_clear_filter(self) -> None:
        """One key back: the document search first, then the channel filter.

        Two things ``esc`` can undo, and the nearer one goes first -- the
        search you are in the middle of, before the filter you set earlier.
        """
        if self._find or self.query_one("#find-bar").display:
            self._clear_find()
            return
        search = self.query_one("#search", Input)
        search.value = ""
        search.display = False
        self.filter = Filter()
        self._apply_filter()
        self.focus_timeline()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "find":
            self._find = event.value
            self._refind()
            return
        self.filter = self.filter.with_query(event.value)
        self._apply_filter()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter keeps the search applied but gives the rows their space back.

        The query stays visible -- in the filter bar for the channel search,
        as a lit match for the document one -- and ``n`` and ``N`` go on
        stepping through it after the box has gone.
        """
        if event.input.id == "find":
            self.query_one("#find-bar").display = False
            target = self._pane("#preview-body" if self._find_in == "file" else "#reader")
            (target or self.query_one(f"#{TIMELINE}", OptionList)).focus()
            return
        self.query_one("#search", Input).display = False
        self.focus_timeline()

    def focus_timeline(self) -> None:
        self.query_one(f"#{TIMELINE}", OptionList).focus()

    # -- finding a word in what is on screen ------------------------------

    def _reading(self) -> str | None:
        """Which document pane holds the keyboard, if either does."""
        focused = self.app.focused
        if focused is None or not self.screen or focused not in self.query("*"):
            return None
        return READING.get(focused.id or "")

    def action_find(self) -> None:
        """Open the find box against the pane the keyboard is in."""
        self._find_in = self._reading() or self._find_in
        self.query_one("#find-bar").display = True
        box = self.query_one("#find", Input)
        box.value = self._find
        box.focus()

    def action_find_next(self, step: int) -> None:
        """``n`` and ``N``, which keep working after the box has closed."""
        if not self._matches:
            return
        self._match = find.next_index(self._matches, self._match, step)
        self._paint()

    def _searchable(self) -> tuple[str, Widget, str] | None:
        """The document being searched: ``(kind, widget, text)``, or None."""
        if self._find_in == "file":
            return self.preview.searchable()
        row = self._row_for(self._selected) if self._selected else None
        if row is None:
            return None
        return "markdown", self.query_one("#doc", Markdown), row.doc.body

    def _refind(self, *, keep: bool = False, jump: bool = True) -> None:
        """Recompute the matches, because the query or the text changed.

        Three ways that happens, and each wants something different done with
        the place in the list. A new query goes to the first match, the way
        incsearch does. A document the session rewrote under the search
        ``keep``s its place: the word is usually still there, and being thrown
        back to the first match by a status update nobody asked about is worse
        than being one match out. A *different* document does not ``jump`` at
        all -- the query survives moving through the timeline, as vim's does
        through buffers, but nothing moves until ``n`` asks it to.
        """
        target = self._searchable()
        self.preview.searching = bool(self._find) and self._find_in == "file"
        self._matches = find.find_all(target[2] if target else "", self._find)
        if not self._matches or not jump:
            self._match = None
        elif self._match is None or not keep:
            self._match = 0 if self._find else None
        else:
            self._match = min(self._match, len(self._matches) - 1)
        self._paint()

    def _paint(self) -> None:
        """Write the count, then light the match -- each in its own way."""
        if not composed(self, "#find-bar"):
            return
        count = self.query_one("#find-count", Static)
        if not self._find:
            count.update("")
        elif not self._matches:
            count.update("[yellow]no matches[/]")
        elif self._match is None:
            # Found, but nothing lit: a query carried into a document nobody
            # has stepped through yet. Saying "1/7" here would be a lie about
            # where the highlight is.
            found = len(self._matches)
            count.update(f"[dim]{found} {'match' if found == 1 else 'matches'}[/]")
        else:
            count.update(f"[dim]{self._match + 1}/{len(self._matches)}[/]")

        target = self._searchable()
        if target is None:
            return
        kind, widget, text = target
        at = self._matches[self._match] if self._match is not None and self._matches else None
        if kind == "text":
            self._light_text(widget, text, at)
        else:
            self._light_block(widget, at)

    def _light_text(self, widget: Static, text: str, at: find.Match | None) -> None:
        """A file we rendered ourselves: highlight the matches in the text.

        Every match is underlined and the current one reversed, which is as
        close to hlsearch as a pane gets. Terminal attributes rather than
        colours, so it reads the same in whatever theme is loaded.
        """
        widget.update(
            Content(text).add_spans(
                [
                    Span(found.start, found.end, "reverse" if i == self._match else "underline")
                    for i, found in enumerate(self._matches)
                ]
            )
        )
        if at is None:
            return
        pane = self.query_one("#preview-body", VerticalScroll)
        row = find.wrapped_row(text, at.line, widget.content_size.width)
        pane.scroll_to(y=max(0, row - pane.size.height // 2), animate=False)

    def _light_block(self, document: Markdown, at: find.Match | None) -> None:
        """A rendered document: tint the block the match fell in, and show it."""
        if self._lit is not None:
            self._lit.remove_class("-match")
            self._lit = None
        if at is None:
            return
        block = find.block_for(document, at.line)
        if block is None:
            return
        block.add_class("-match")
        self._lit = block
        pane = "#preview-body" if self._find_in == "file" else "#reader"
        self.query_one(pane, VerticalScroll).scroll_to_widget(
            block, center=True, animate=False
        )

    def _clear_find(self) -> None:
        """Put the document back the way it was, and the keyboard with it."""
        was = self._find_in if self._find else None
        self._find = ""
        self._matches = []
        self._match = None
        if composed(self, "#preview"):
            self.preview.searching = False
        if composed(self, "#find-bar"):
            box = self.query_one("#find", Input)
            held = box.has_focus
            box.value = ""
            self.query_one("#find-bar").display = False
            self._paint()  # untints the block, and re-renders the plain text
            if held:
                pane = "#preview-body" if was == "file" else "#reader"
                target = self._pane(pane)
                (target or self.query_one(f"#{TIMELINE}", OptionList)).focus()

    # -- moving between panes ---------------------------------------------

    def focus_neighbour(self, direction: str) -> None:
        """Move the keyboard one pane over, the way ``ctrl+w`` does in vim.

        Sideways keeps the row where it can: coming out of the files strip and
        back returns to the files strip, not to the top of the column. Neither
        axis wraps -- ``ctrl+w k`` at the top of a column is a no-op, because
        in vim it is one too.
        """
        columns = [[pane for pane in map(self._pane, ids) if pane] for ids in COLUMNS]
        here = self.app.focused
        for x, column in enumerate(columns):
            if here in column:
                y = column.index(here)
                break
        else:
            # The keyboard is somewhere that is not a pane -- the search box,
            # or nowhere at all. Then this is a request to be in a pane.
            x, y = 0, 0
            here = None

        if direction in ("left", "right"):
            x = max(0, min(len(columns) - 1, x + (1 if direction == "right" else -1)))
        elif here is not None:
            y += 1 if direction == "down" else -1

        column = columns[x]
        if not column:
            return  # nothing on screen in that column to move to
        if direction in ("up", "down") and not 0 <= y < len(column):
            return  # off the end: at the top of a column, ctrl+w k does nothing
        column[max(0, min(len(column) - 1, y))].focus()

    def _pane(self, selector: str) -> Widget | None:
        """The widget at ``selector``, if it is actually on screen.

        Its own ``display`` is not enough: the file preview's body is always
        displayed inside a container that is hidden until a file is opened.
        """
        for widget in self.query(selector):
            if all(getattr(node, "display", True) for node in widget.ancestors_with_self):
                return widget
        return None

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
