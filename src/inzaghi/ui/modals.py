"""Modal screens. Only one: confirming something that changes a run."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from rich.markup import escape


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
