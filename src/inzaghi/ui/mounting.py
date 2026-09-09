"""Whether the widgets a refresh writes into exist yet.

Both refreshes run off bare timers with no relationship to mounting: the folder
poll every two seconds, the countdown tick every one.  Neither is late enough
to be safe.  A config naming a dozen channels takes longer than a second to
mount, so the first tick can arrive before the tabs are there at all; and
removing a tab tears a pane's children down a moment before the pane itself.

A refresh that lands in one of those windows has nothing to write into.  That
is a reason to skip it, not to take the app down: the next one is a second
behind, and a pane that missed a redraw is redrawn.  Letting the query raise
instead kills a whole session over a frame nobody would have seen.
"""

from __future__ import annotations

from textual.dom import DOMNode


def composed(node: DOMNode, anchor: str) -> bool:
    """True when ``anchor`` exists somewhere beneath ``node``.

    ``anchor`` should be a widget that is always present once the subtree is
    built, so that finding it answers for the rest of them.  An id selector,
    since Textual caches those lookups and this runs every second.
    """
    return bool(node.query(anchor))
