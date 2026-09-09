"""Finding a string inside the document on screen.

The channel search up at the top of the pane asks *which entries mention
this*.  This asks the other question -- *where in this one does it say that* --
which on a phase summary of eighty lines is the one you actually have.

Both halves of the answer are awkward for different reasons.

Matching is done against the **source**, because that is the text a session
wrote and the text a search should agree with.  Offsets come from a regex over
the original string rather than a lowercased copy: casefolding is not
length-preserving for every character, and a shifted offset highlights the
wrong words.

Showing the match is where the two panes part company.  A rendered Markdown
document is not text on screen, it is a column of block widgets, so a match is
shown by scrolling to the block whose source range contains it and tinting
that -- the innermost one, so a list item lights up rather than the whole
list.  A delivered ``.txt`` is rendered by us, one string in one widget, so
there the match itself is highlighted and the only question is which screen
row it wrapped onto.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from textwrap import wrap

from textual.widget import Widget


@dataclass(frozen=True, slots=True)
class Match:
    """One occurrence, as both an offset span and a source line."""

    #: Offsets into the whole text, for styling a span of it.
    start: int
    end: int
    #: The source line it fell on, for finding the block that renders it.
    line: int


def find_all(text: str, query: str) -> list[Match]:
    """Every case-insensitive occurrence of ``query`` in ``text``, in order.

    ``query`` is taken literally, not as a pattern: the box is one line at the
    bottom of a pane with nowhere to report that a bracket was unbalanced, and
    a session's prose is full of brackets.
    """
    if not query or not text:
        return []
    starts = _line_starts(text)
    return [
        Match(found.start(), found.end(), bisect_right(starts, found.start()) - 1)
        for found in re.finditer(re.escape(query), text, re.IGNORECASE)
    ]


def next_index(matches: list[Match], current: int | None, step: int) -> int | None:
    """The match ``step`` away from ``current``, wrapping the way ``n`` does."""
    if not matches:
        return None
    if current is None:
        return 0 if step > 0 else len(matches) - 1
    return (current + step) % len(matches)


def block_for(document: Widget, line: int) -> Widget | None:
    """The rendered block that ``line`` of the source landed in.

    The innermost one: blocks nest, and a paragraph inside a list item is a
    better answer than the list. ``source_range`` is the half-open line span
    markdown-it gave the block, which is why this can be asked at all.
    """
    best: Widget | None = None
    best_span = None
    for block in document.query("MarkdownBlock"):
        span = getattr(block, "source_range", None)
        if not span or not span[0] <= line < span[1]:
            continue
        size = span[1] - span[0]
        if best_span is None or size < best_span:
            best, best_span = block, size
    return best


def wrapped_row(text: str, line: int, width: int) -> int:
    """Which screen row ``line`` starts on, once the text has wrapped.

    An estimate, and knowingly so: it counts the rows the lines above it take
    at this width, which is what Rich will do with them but not provably to
    the row. The caller centres the match rather than putting it at the top,
    so being a row or two out is invisible; being exact would mean laying the
    whole file out again on every press of ``n``.
    """
    if width <= 0:
        return 0
    rows = 0
    for source in text.splitlines()[:line]:
        rows += len(wrap(source, width)) or 1
    return rows


def _line_starts(text: str) -> list[int]:
    """The offset each line begins at, for turning an offset into a line."""
    starts = [0]
    at = text.find("\n")
    while at != -1:
        starts.append(at + 1)
        at = text.find("\n", at + 1)
    return starts
