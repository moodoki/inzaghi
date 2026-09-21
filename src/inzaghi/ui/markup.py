"""Escaping text that is about to be pasted into a markup string.

Row labels are built as markup, and every piece of text that came out of a
channel is escaped on the way in -- titles genuinely look like
``[milestone] ...`` and would otherwise be read as a tag.

Rich's ``escape`` is the wrong tool for it. What these strings are parsed as is
Textual's markup, and the two dialects disagree about the backslash: Rich
doubles one so that its own parser can halve it again, while Textual's parser
only ever drops a backslash standing in front of a tag. A title ending in a
backslash therefore comes back out of ``rich.markup.escape`` carrying two.

A bracket is the only character that can open a tag, so escaping brackets alone
is both sufficient and exact -- every other character, backslash included,
survives untouched.

The one thing no escaping can do here is protect a tag that *follows* a
backslash: Textual drops one backslash from any run in front of a tag and then
renders the tag as text, whatever the run's length. Text ending in a backslash
therefore costs the tag after it -- a styled label that runs its colour to the
end of the line instead of closing where it meant to, on one row, on the day a
session ends a heading with a backslash. Doubling, which is the only other
move available, loses the tag too and doubles the character everywhere a tag
does not follow; being exact about the text is worth more.
"""

from __future__ import annotations


def escape(text: str) -> str:
    """Make ``text`` render as itself inside a markup string."""
    return text.replace("[", "\\[")
