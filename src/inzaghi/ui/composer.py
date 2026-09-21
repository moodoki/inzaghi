"""The inline message composer.

It lives in the bottom half of the timeline column rather than over the whole
screen, because writing a message to a session is mostly an act of *reading*:
you are quoting a number out of one notification while checking what a third
one said. A modal that hides the log makes you compose from memory.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Static, TextArea

from .. import compose as composer
from .. import fmt
from .mounting import composed
from .modals import PromptScreen

#: How long after a keystroke the draft is looked over for paths. Deciding
#: whether a path is a file means asking the filesystem, and a draft is typed
#: rather than pasted a character at a time.
LOOK_AFTER_SECONDS = 0.3

class Composer(Vertical):
    """A draft addressed to one channel, kept until sent or discarded."""

    BINDINGS = [
        Binding("ctrl+s", "send", "Send", priority=True),
        Binding("escape", "back", "Back to list"),
        Binding("ctrl+e", "editor", "$EDITOR", priority=True),
        Binding("ctrl+g", "discard", "Discard", priority=True),
        Binding("ctrl+a", "attach", "Attach a file", priority=True),
    ]

    class Send(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Closed(Message):
        """The draft was put away; focus should go back to the timeline."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: Who the draft is addressed to, kept so the head line can be redrawn
        #: when what it is carrying changes rather than only when it opens.
        self._addressed = ("", "")
        #: The look-over the last keystroke asked for and has not had yet.
        self._looking: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="composer-head", markup=True)
        # No key hints here: these bindings surface in the footer whenever the
        # composer has focus, which is exactly when they are wanted, and the
        # column is too narrow to spell them out without truncating.
        yield TextArea("", id="compose-text", soft_wrap=True)

    def on_mount(self) -> None:
        self.display = False

    @property
    def text(self) -> str:
        return self.query_one(TextArea).text

    @property
    def has_draft(self) -> bool:
        return bool(self.text.strip())

    def open(self, channel_name: str, due: str) -> None:
        """Show the composer, keeping whatever was already typed."""
        self._addressed = (channel_name, due)
        self._head()
        self.display = True
        self.query_one(TextArea).focus()
        self._look_soon()

    # -- what the draft is carrying ---------------------------------------

    def _head(self, carrying: list[composer.Attaching] | None = None) -> None:
        """Redraw the line above the draft: who it is for, and what goes with it."""
        if not composed(self, "#composer-head"):
            return
        name, due = self._addressed
        line = f"[b]→ {escape(name)}[/]  [dim]· session {escape(due)}[/]"
        refused = [item for item in carrying or [] if not item.sendable]
        if refused:
            first = refused[0]
            line += (
                f"  [bold red]· {escape(first.source.name)} {escape(first.problem)}[/]"
            )
        elif carrying:
            total = sum(item.source.stat().st_size for item in carrying)
            noun = "file" if len(carrying) == 1 else "files"
            line += f"  [cyan]· carrying {len(carrying)} {noun}, {fmt.size(total)}[/]"
        self.query_one("#composer-head", Static).update(line)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._look_soon()

    def _look_soon(self) -> None:
        """Look the draft over once the typing stops, never on the keystroke."""
        if self._looking is not None:
            self._looking.stop()
        self._looking = self.set_timer(LOOK_AFTER_SECONDS, self._look)

    def _look(self) -> None:
        self._looking = None
        if self.display:
            self._find_attachments(self.text)

    @work(thread=True, exclusive=True, group="attaching")
    def _find_attachments(self, text: str) -> None:
        """Ask the filesystem about the paths in the draft, off the UI thread.

        A path someone typed is usually on a local disk and answers at once.
        Usually: the file worth sending back to a session is quite often one
        that came *from* the channel, and that folder belongs to a sync client
        which answers when it likes. See ``ui.volume``.
        """
        try:
            carrying = composer.attaching(text)
        except OSError:  # pragma: no cover -- a volume that stopped answering
            return
        self.app.call_from_thread(self._head, carrying)

    def close(self, *, clear: bool = True) -> None:
        if clear:
            self.query_one(TextArea).text = ""
        self.display = False
        self.post_message(self.Closed())

    def action_send(self) -> None:
        text = self.text.strip()
        if text:
            self.post_message(self.Send(text))

    def action_back(self) -> None:
        """Leave the draft standing and hand the keyboard to the timeline."""
        self.post_message(self.Closed())

    def action_discard(self) -> None:
        self.close(clear=True)

    def action_attach(self) -> None:
        """Ask for a path, and write it into the draft as a link.

        The draft is what carries it: a path in the text is the whole record,
        which is why dropping a file on the terminal works without this key
        existing, and why deleting the line is how you change your mind.
        """
        def written(path: str | None) -> None:
            if not path:
                return
            source = Path(path.strip()).expanduser()
            area = self.query_one(TextArea)
            text = area.text.rstrip()
            link = f"[{source.name}]({source})"
            area.text = f"{text}\n\n{link}\n" if text else f"{link}\n"
            area.focus()

        self.app.push_screen(PromptScreen("Attach which file?", "path to a file"), written)

    def action_editor(self) -> None:
        area = self.query_one(TextArea)
        with self.app.suspend():
            edited = _edit_externally(area.text)
        if edited is not None:
            area.text = edited
        area.focus()


def _edit_externally(text: str) -> str | None:
    """Round-trip ``text`` through $EDITOR; ``None`` if the editor failed."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    handle, name = tempfile.mkstemp(suffix=".md", prefix="inzaghi-")
    path = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        if subprocess.run([*editor.split(), str(path)]).returncode != 0:
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
    finally:
        path.unlink(missing_ok=True)
