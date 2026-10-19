"""The upright divider between the timeline column and the reader.

How much of the width the log deserves depends on what is being read: a quick
look down the timeline wants it wide, a long notification or an opened file
wants the reader wide. So the divider moves -- dragged, or keyed with vim's
window commands, ``ctrl+w <``, ``ctrl+w >`` and ``ctrl+w =``.

The split is a share of the width rather than a number of columns, for the
reason the preview pane's is: resizing the terminal should keep the proportion
somebody chose, not the width they chose it at. And there is one split for the
whole app, not one per channel -- changing tab must not move the divider.
"""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widget import Widget

#: The timeline column's share of the width when nobody has moved it.
START_PERCENT = 46

#: How far it goes either way. Neither column may be dragged closed: a column
#: at no width is indistinguishable from one that is not there. The timeline
#: also keeps its own ``min-width`` in the stylesheet, which wins on a narrow
#: terminal.
MIN_PERCENT = 20
MAX_PERCENT = 80

#: One press of ``ctrl+w <`` or ``ctrl+w >``.
STEP_PERCENT = 5


def clamp(percent: float) -> int:
    """A share the divider may actually sit at."""
    return max(MIN_PERCENT, min(MAX_PERCENT, round(percent)))


class Divider(Widget):
    """One column wide, between the two halves of a channel, and draggable.

    A drag is reported in columns; where that lands is the app's business,
    because the split is shared by every channel.
    """

    class Dragged(Message):
        """The divider was pulled ``columns`` to the right (negative: left)."""

        def __init__(self, columns: int) -> None:
            super().__init__()
            self.columns = columns

    def render(self) -> str:
        return ""

    def on_mouse_down(self, event: events.MouseDown) -> None:
        event.stop()
        self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        # Only while the button is down: a mouse crossing on its way to the
        # reader is not a drag, and being captured is how we know.
        if event.delta_x and self.app.mouse_captured is self:
            self.post_message(self.Dragged(event.delta_x))

    def on_mouse_up(self, event: events.MouseUp) -> None:
        event.stop()
        self.release_mouse()
