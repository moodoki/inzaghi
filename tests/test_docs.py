"""The README and the app must not drift apart."""

from __future__ import annotations

from pathlib import Path

from inzaghi.ui.overview import PIGEON

README = Path(__file__).resolve().parent.parent / "README.md"


def test_the_readme_shows_the_same_bird_the_app_does():
    """One source: edit the art in overview.py and paste it here, or vice versa."""
    assert PIGEON in README.read_text(encoding="utf-8")


def test_the_art_is_fenced_so_markdown_leaves_it_alone():
    """Backslashes and backticks in prose would be mangled by renderers."""
    text = README.read_text(encoding="utf-8")
    before = text[: text.index(PIGEON)]
    assert before.rstrip().endswith("```")
