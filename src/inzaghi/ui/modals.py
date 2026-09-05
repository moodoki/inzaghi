"""Modal screens: composing a message, and confirming a dangerous one."""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from rich.markup import escape
from textual.widgets import Button, Label, Static, TextArea

from ..compose import QuickAction


@dataclass(frozen=True, slots=True)
class Draft:
    """What the compose screen hands back."""

    text: str
    #: Set when the draft is a recognised keyword rather than free-form text.
    action: QuickAction | None = None


class ComposeScreen(ModalScreen[Draft | None]):
    """A message to a session, which will not be read for a while.

    The screen shows when the session is next expected, because that -- not
    typing speed -- decides whether a message is worth sending at all.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "send", "Send", show=True, priority=True),
        Binding("ctrl+e", "editor", "$EDITOR", show=True, priority=True),
    ]

    def __init__(self, channel_name: str, due: str, initial: str = "") -> None:
        super().__init__()
        self._channel_name = channel_name
        self._due = due
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="compose-box"):
            yield Label(f"Message to [b]{escape(self._channel_name)}[/]", id="compose-title")
            yield Static(f"[dim]session {self._due}[/]", id="compose-due")
            yield TextArea(self._initial, id="compose-text", soft_wrap=True)
            with Horizontal(id="compose-buttons"):
                yield Button("Send", variant="primary", id="send")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def action_send(self) -> None:
        text = self.query_one(TextArea).text.strip()
        self.dismiss(Draft(text=text) if text else None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_editor(self) -> None:
        """Hand the draft to $EDITOR, then take back whatever it saved."""
        area = self.query_one(TextArea)
        with self.app.suspend():
            edited = _edit_externally(area.text)
        if edited is not None:
            area.text = edited
        area.focus()

    @on(Button.Pressed, "#send")
    def _send(self) -> None:
        self.action_send()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.action_cancel()


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no gate in front of anything that changes what a session is doing."""

    BINDINGS = [
        Binding("escape", "no", "No"),
        Binding("n", "no", "No"),
        Binding("y", "yes", "Yes"),
    ]

    def __init__(self, question: str, detail: str = "", confirm_label: str = "Send") -> None:
        super().__init__()
        self._question = question
        self._detail = detail
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(escape(self._question), id="confirm-question")
            if self._detail:
                yield Static(f"[dim]{escape(self._detail)}[/]", id="confirm-detail")
            with Horizontal(id="confirm-buttons"):
                yield Button(f"{self._confirm_label} (y)", variant="error", id="yes")
                yield Button("Cancel (n)", id="no")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(False)


def _edit_externally(text: str) -> str | None:
    """Round-trip ``text`` through $EDITOR; ``None`` if the editor failed."""
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    handle, name = tempfile.mkstemp(suffix=".md", prefix="inzaghi-")
    path = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        result = subprocess.run([*editor.split(), str(path)])
        if result.returncode != 0:
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
    finally:
        path.unlink(missing_ok=True)
