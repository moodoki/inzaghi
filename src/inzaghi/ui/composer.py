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
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, TextArea

class Composer(Vertical):
    """A draft addressed to one channel, kept until sent or discarded."""

    BINDINGS = [
        Binding("ctrl+s", "send", "Send", priority=True),
        Binding("escape", "back", "Back to list"),
        Binding("ctrl+e", "editor", "$EDITOR", priority=True),
        Binding("ctrl+g", "discard", "Discard", priority=True),
    ]

    class Send(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Closed(Message):
        """The draft was put away; focus should go back to the timeline."""

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
        self.query_one("#composer-head", Static).update(
            f"[b]→ {escape(channel_name)}[/]  [dim]· session {escape(due)}[/]"
        )
        self.display = True
        self.query_one(TextArea).focus()

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
