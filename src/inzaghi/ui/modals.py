"""Modal screens: confirming something that changes a run, and asking for a path."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from .markup import escape


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no gate in front of anything that changes what a session is doing."""

    BINDINGS = [
        Binding("escape", "no", "No"),
        Binding("n", "no", "No"),
        Binding("y", "yes", "Yes"),
        # Left and right change channel everywhere else, and a Button binds
        # neither, so without these the tabs would shuffle about behind a
        # dialog asking whether to stop a run. In front of two buttons they
        # mean what they look like they mean.
        Binding("left", "previous_button", "Previous", show=False),
        Binding("right", "next_button", "Next", show=False),
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

    def action_next_button(self) -> None:
        self.focus_next()

    def action_previous_button(self) -> None:
        self.focus_previous()

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


class PromptScreen(ModalScreen[str | None]):
    """One line of text, for when there is nothing to drag.

    A file reaches a draft by being dropped on the terminal, which pastes its
    path -- and that is the whole of the interaction on a machine with a mouse
    and a file manager. This is the other half: somewhere to type the path
    when the file is on the far side of an ssh session.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, question: str, placeholder: str = "") -> None:
        super().__init__()
        self._question = question
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-box"):
            yield Label(escape(self._question), id="prompt-question")
            yield Input(placeholder=self._placeholder, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#prompt-input")
    def _submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)
