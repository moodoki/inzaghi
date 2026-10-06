"""Links in a document: which ones a click may open, and copying the rest.

A channel is written by an unattended session, so the reader decides what a
link does rather than the ``Markdown`` widget. A web address opens in the
browser when clicked, because a click is somebody choosing to go there. That is
the whole whitelist. Anything else -- a path, ``file:``, a scheme some
application registered -- is never launched; it can be copied, from the
keyboard, and then it is the reader's to paste wherever they meant to use it.

A label can say one thing and point somewhere else, so the address is shown
on hover and named again when it opens.
"""

from __future__ import annotations

import ast
import re
from urllib.parse import urlsplit

from rich.style import Style
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from .. import parse
from .markup import escape

#: The schemes a click may hand to the browser.
WEB_SCHEMES = frozenset({"http", "https"})

# A bare address in prose. Textual's parser turns these into links too, so a
# copy list that left them out would leave out links that are on screen.
_BARE_URL_RE = re.compile(r"https?://[^\s<>()\[\]`]+")
# What the widget puts behind a link: ``link('...')`` as a click action.
_ACTION_RE = re.compile(r"^link\((?P<arg>.*)\)$", re.S)


def is_web(href: str) -> bool:
    """Whether ``href`` is an address a click may open in the browser."""
    try:
        parts = urlsplit(href.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in WEB_SCHEMES and bool(parts.netloc)


def host(href: str) -> str:
    """Where a web address goes, for saying so in a toast."""
    try:
        return urlsplit(href.strip()).netloc or href
    except ValueError:
        return href


def href_at(style: Style | None) -> str | None:
    """The link under the mouse, read from the style the widget drew it with.

    Textual renders a Markdown link as text carrying a ``@click`` action of
    ``link('<href>')``. Nothing else in a document carries one.
    """
    if style is None:
        return None
    action = style.meta.get("@click")
    if not isinstance(action, str):
        return None
    match = _ACTION_RE.match(action)
    if match is None:
        return None
    try:
        href = ast.literal_eval(match["arg"])
    except (ValueError, SyntaxError):
        return None
    return href if isinstance(href, str) and href else None


def document_links(text: str) -> list[parse.Link]:
    """Every link in ``text``, in order, each address once.

    Written links first as the prose labelled them, then any bare web address
    that was not already the target of one. Code is skipped, for the reason
    ``parse.markdown_links`` skips it: a link in a fence is being quoted.
    """
    found: dict[str, parse.Link] = {}
    for link in parse.markdown_links(text):
        found.setdefault(link.target, link)
    prose = parse.without_code(text)
    for match in _BARE_URL_RE.finditer(prose):
        url = match.group(0).rstrip(".,;:!?'\"")
        found.setdefault(url, parse.Link(label="", target=url))
    return list(found.values())


def _option(link: parse.Link) -> Option:
    label = link.label.strip()
    if label and label != link.target:
        prompt = f"{escape(label)}\n[dim]{escape(link.target)}[/]"
    else:
        prompt = escape(link.target)
    return Option(prompt)


class LinkScreen(ModalScreen[str | None]):
    """The links in one document, to copy from the keyboard.

    Dismissed with the address chosen, or ``None``. Copying is the one thing it
    does, for every kind of link: a web address can be clicked, but a keyboard
    has nothing to click with, and copying needs no decision about whether
    the address is safe to launch.
    """

    BINDINGS = [
        Binding("escape,q", "cancel", "Cancel"),
        Binding("y", "choose", "Copy"),
        Binding("j", "cursor('down')", "Down", show=False),
        Binding("k", "cursor('up')", "Up", show=False),
    ]

    def __init__(self, links: list[parse.Link]) -> None:
        super().__init__()
        self._links = links

    def compose(self) -> ComposeResult:
        with Vertical(id="links-box"):
            yield Label("Copy which link?  [dim]enter or y copies, esc closes[/]", id="links-question")
            yield OptionList(*(_option(link) for link in self._links), id="links-list")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def action_cursor(self, direction: str) -> None:
        listing = self.query_one(OptionList)
        if direction == "down":
            listing.action_cursor_down()
        else:
            listing.action_cursor_up()

    def action_choose(self) -> None:
        index = self.query_one(OptionList).highlighted
        self.dismiss(None if index is None else self._links[index].target)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#links-list")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self._links[event.option_index].target)
