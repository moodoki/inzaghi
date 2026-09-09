"""The bottom of the reader: a delivered text file, shown where it was named.

Markdown and plain text are what this pane renders all day, so handing one to
the desktop would start an application to show something the reader is already
capable of showing.  They open here instead -- under the notification that
delivered them, above nothing, in a strip of the reader that is dragged or
keyed to whatever share of the height the file is worth.

It starts three quarters deep, because somebody who asked for a file is
reading the file: the notification that announced it keeps the top quarter,
which is enough to see what it said, and the divider is dragged or keyed back
down by anyone who wants the prose again.

Nothing here reads the volume.  The text arrives from the app's worker, the
same way a snapshot does, because the file sits in a synced folder that
answers when it likes.
"""

from __future__ import annotations

from pathlib import Path
from time import monotonic

from rich.markup import escape
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Markdown, Static

from .. import attach, fmt
from ..model import Attachment
from .mounting import composed

#: Suffixes rendered as Markdown.  Everything else a ``read`` delivery can be
#: is shown as the plain text it is -- a log is not improved by having its
#: hyphens read as bullets.
AS_MARKDOWN = frozenset({".md", ".markdown"})

#: What share of the reader column an opened file gets.  The prose keeps the
#: rest; opening a file is asking to read it, not to glance at it.
START_PERCENT = 75

#: How far the divider goes either way.  Neither pane may be closed by
#: dragging: a pane at no rows is indistinguishable from one that is not
#: there, and closing this one has a key of its own.
MIN_PERCENT = 10
MAX_PERCENT = 85

#: One press of the resize keys.
STEP_PERCENT = 5


class Grip(Static):
    """The divider line, which also says what is beneath it.

    Draggable, so the split can be put where the file needs it without
    learning a key first.  A drag is reported in rows; where that lands is the
    pane's business, not the divider's.
    """

    class Dragged(Message):
        """The divider was pulled ``rows`` down the screen (negative: up)."""

        def __init__(self, rows: int) -> None:
            super().__init__()
            self.rows = rows

    def on_mouse_down(self, event: events.MouseDown) -> None:
        event.stop()
        self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        # Only while the button is down.  A mouse crossing the divider on its
        # way to the timeline is not a drag, and captured is how we know.
        if event.delta_y and self.app.mouse_captured is self:
            self.post_message(self.Dragged(event.delta_y))

    def on_mouse_up(self, event: events.MouseUp) -> None:
        event.stop()
        self.release_mouse()


class Preview(Vertical):
    """One text delivery, rendered in place beneath the reader."""

    BINDINGS = [
        Binding("escape", "close", "Close file", show=False),
        Binding("plus,equals_sign", "resize(1)", "Taller", show=False),
        Binding("minus,underscore", "resize(-1)", "Shorter", show=False),
    ]

    class Closed(Message):
        """Closed while it held the keyboard; somewhere else needs it now."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: The split, as a percentage of the reader column.  A share rather
        #: than a row count so that resizing the terminal keeps the
        #: proportion somebody chose rather than the height they chose it at.
        self.percent = START_PERCENT
        #: What is on screen: the name, and the file facts that produced the
        #: text.  A re-read of the same file leaves the scroll position where
        #: it was; a different file starts at the top.
        self.showing: tuple | None = None
        #: When the text on screen was read off the volume.  A file is only
        #: believed for so long: the facts the scan compares come from a stat,
        #: and a sync client's stat is allowed to stop moving.
        self.read_at: float | None = None
        #: The text of it, kept rather than dropped after rendering: a search
        #: in this pane matches against the source it was given, the same way
        #: a search in the reader matches the document it rendered.
        self.text = ""
        #: Whether a search is running in here, which the pane sets.  It
        #: changes what ``escape`` means: the nearer thing to undo is the
        #: search, and only once that is gone does the key close the file.
        self.searching = False

    def compose(self) -> ComposeResult:
        yield Grip("", id="preview-grip", markup=True)
        with VerticalScroll(id="preview-body"):
            # Both mounted up front and shown one at a time. Mounting a widget
            # is asynchronous, and this is written into from a worker's
            # callback: a pane that is always there is one less thing to be
            # halfway through when the text lands.
            #
            # ``open_links=False`` for the reason it is off in the reader, and
            # more so: this document is a file an unattended session wrote.
            yield Markdown("", id="preview-md", open_links=False)
            yield Static("", id="preview-text", markup=False)

    def on_mount(self) -> None:
        self.display = False
        self.styles.height = f"{self.percent}%"

    # -- what is in it ----------------------------------------------------

    @property
    def showing_name(self) -> str | None:
        """The file on screen, for a scan that wants to know if it changed."""
        return self.showing[0] if self.showing else None

    def is_current(self, attachment: Attachment) -> bool:
        """Whether what is on screen was read from exactly this file.

        Lets a scan tell a delivery that has actually changed from one that
        merely appeared in a strip rebuilt for a different file.
        """
        if self.showing is None:
            return False
        return self.showing[:4] == (
            attachment.name,
            attachment.size,
            attachment.mtime,
            attachment.fingerprint,
        )

    def is_stale(self, older_than: float) -> bool:
        """Whether what is on screen has been believed for long enough.

        The last defence, for a stat that has stopped answering truthfully
        altogether rather than merely late: past this, the file is read again
        whatever the scan thinks it knows about it. A re-read that finds the
        same text costs a comparison and changes nothing on screen.
        """
        return self.read_at is not None and monotonic() - self.read_at >= older_than

    def show(self, attachment: Attachment, text: str, truncated: bool, *, focus: bool) -> None:
        """Put a text delivery on screen.

        ``focus`` separates somebody asking to see the file from a re-read of
        one already open: a payload that finished syncing must not take the
        keyboard away from whatever it was doing.
        """
        if not composed(self, "#preview-body"):
            return  # the tab is coming down; the text has nowhere to go
        stamp = (
            attachment.name,
            attachment.size,
            attachment.mtime,
            attachment.fingerprint,
            truncated,
        )
        self.read_at = monotonic()
        body = self.query_one("#preview-body", VerticalScroll)
        self.query_one("#preview-grip", Grip).update(_heading(attachment, truncated))
        # The text as well as the stamp, because the stamp is made of what a
        # stat said and the text is the file. When the two disagree the file
        # wins: a re-read that had to be asked for by the clock rather than by
        # the scan is precisely the one whose stamp did not move.
        if stamp != self.showing or text != self.text:
            fresh = self.showing_name != attachment.name
            self.showing = stamp
            self._write(attachment, text)
            if fresh:
                body.scroll_home(animate=False)
        self.display = True
        if focus:
            body.focus()

    def searchable(self) -> tuple[str, Widget, str] | None:
        """What there is to search here: ``(kind, widget, text)``, or None.

        ``kind`` separates the two ways a match can be shown: ``markdown`` is
        a column of blocks to scroll to, ``text`` is one string we laid out
        ourselves and can highlight to the character.
        """
        if not self.display or self.showing is None:
            return None
        markdown = self.query_one("#preview-md", Markdown)
        if markdown.display:
            return "markdown", markdown, self.text
        return "text", self.query_one("#preview-text", Static), self.text

    def _write(self, attachment: Attachment, text: str) -> None:
        self.text = text
        as_markdown = Path(attachment.name).suffix.lower() in AS_MARKDOWN
        markdown = self.query_one("#preview-md", Markdown)
        plain = self.query_one("#preview-text", Static)
        markdown.display = as_markdown
        plain.display = not as_markdown
        if as_markdown:
            markdown.update(text or "*(empty file)*")
        else:
            plain.update(text or "(empty file)")

    def close(self) -> None:
        """Give the room back to the prose, and the keyboard back if we held it.

        The text goes with it. What is left is a quarter of a megabyte of a log
        nobody is looking at, and reopening the file re-reads it anyway --
        which is what should happen: it may well have changed since.
        """
        if not composed(self, "#preview-body"):
            return
        held = self.query_one("#preview-body", VerticalScroll).has_focus
        self.display = False
        self.showing = None
        self.read_at = None
        self.text = ""
        self.query_one("#preview-md", Markdown).update("")
        self.query_one("#preview-text", Static).update("")
        if held:
            self.post_message(self.Closed())

    def action_close(self) -> None:
        self.close()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hand ``escape`` back while a search is running in here.

        Refused, the key carries on to the pane, which clears the search. The
        next press finds no search and closes the file, so one key undoes two
        things in the order they were done.
        """
        if action == "close" and self.searching:
            return False
        return True

    # -- how big it is ----------------------------------------------------

    def action_resize(self, direction: int) -> None:
        self._set_percent(self.percent + direction * STEP_PERCENT)

    def on_grip_dragged(self, event: Grip.Dragged) -> None:
        event.stop()
        column = self.parent.size.height if self.parent is not None else 0
        if column <= 0:  # mid-layout; the next drag event will land
            return
        # Down the screen is a smaller share: the divider is our top edge.
        self._set_percent(self.percent - event.rows * 100 / column)

    def _set_percent(self, percent: float) -> None:
        clamped = max(MIN_PERCENT, min(MAX_PERCENT, round(percent)))
        if clamped == self.percent:
            return
        self.percent = clamped
        self.styles.height = f"{clamped}%"


def _heading(attachment: Attachment, truncated: bool) -> str:
    """The divider's label: which file, how big, and how to move or close it.

    The session's description of the file is left out; the line in the strip
    directly above this one is already carrying it. Clipped from the right, so
    the two hints are ordered with the one worth keeping first.
    """
    parts = [escape(attachment.name), fmt.size(attachment.size)]
    if truncated:
        parts.append(f"first {fmt.size(attach.PREVIEW_BYTES)}")
    return f"[dim]{' · '.join(parts)}  —  esc closes · drag or +/- resizes[/]"
