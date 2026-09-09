"""What a vim motion means to whichever pane holds the keyboard.

``j`` is the same key wherever it is pressed, but the panes it lands in are
different things: a list moves a cursor, a document scrolls.  So a motion is
named for the movement -- down, up, top, bottom -- and each widget is asked
for it in the terms it has.  The first action it owns is the one that runs.

Nothing here knows about keys or about the app.  It is the translation layer
between "the person pressed j" and "this particular widget goes down", so a
pane added later needs an entry in a table rather than a binding of its own.
"""

from __future__ import annotations

from textual.widget import Widget

#: Each motion, in the order a widget is asked for it.  Order is the whole
#: point: an ``OptionList`` has both ``first`` and ``scroll_home``, and ``gg``
#: on a list means the first row, not the top of the scroll.
MOTIONS: dict[str, tuple[str, ...]] = {
    "down": ("cursor_down", "scroll_down"),
    "up": ("cursor_up", "scroll_up"),
    "top": ("first", "scroll_top", "scroll_home"),
    "bottom": ("last", "scroll_bottom", "scroll_end"),
}

#: Where a widget keeps its cursor, if it has one.  Read to tell a list from a
#: document, and to notice a step that has run off the end of the list.
CURSORS = ("highlighted", "cursor_row")


def move(widget: Widget, motion: str) -> bool:
    """Perform ``motion`` on ``widget``.  False if it cannot do it at all."""
    for action in MOTIONS[motion]:
        method = getattr(widget, f"action_{action}", None)
        if method is not None:
            method()
            return True
    return False


def half_page(widget: Widget, direction: int) -> bool:
    """Half a screen up (-1) or down (1), the way ``ctrl+d`` and ``ctrl+u`` do.

    A document scrolls by the rows it has room for.  A list steps its cursor
    the same distance, one row at a time rather than by arithmetic, because
    the timeline is not only rows: the pinned/log rule and the unread divider
    sit in it as disabled options, and stepping is what knows to pass over
    them.

    Textual's cursor actions wrap, which is right for a single ``j`` at the
    last row and wrong thirteen steps into a half page -- it would spin round
    the end and carry on.  So the end of the list is read off the step itself:
    a cursor that came back the other way has wrapped and is stepped back
    once, and one that did not move at all was already there.  Stepping rather
    than assigning, because not every widget with a cursor will let it be set
    -- a ``DataTable`` exposes ``cursor_row`` read-only.
    """
    rows = max(1, widget.size.height // 2)
    cursor = _cursor(widget)
    if cursor is None:
        if not hasattr(widget, "scroll_relative"):
            return False
        widget.scroll_relative(y=rows * direction, animate=False)
        return True

    name, _ = cursor
    motion = "down" if direction > 0 else "up"
    back = "up" if direction > 0 else "down"
    for _ in range(rows):
        before = getattr(widget, name)
        if not move(widget, motion):
            return False
        after = getattr(widget, name)
        if before is None or after is None or after == before:
            break  # nowhere to go, or it stopped at the end of its own accord
        if (after - before) * direction < 0:
            move(widget, back)  # it wrapped: back to the end, and stop there
            break
    return True


def _cursor(widget: Widget) -> tuple[str, object] | None:
    """The name and value of ``widget``'s cursor, or None if it has no cursor."""
    for name in CURSORS:
        if hasattr(widget, name):
            return name, getattr(widget, name)
    return None
