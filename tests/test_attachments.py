"""Files a notification delivers.

Two properties hold everything else together: a file exists because a
notification points at it, and a file that has not arrived yet is late rather
than gone. Both are consequences of the folder being synced -- the payload and
the prose announcing it cross separately, in either order.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from unittest import mock

import pytest
from textual.widgets import Markdown, OptionList, Static, TabbedContent

from conftest import NOW, write

from inzaghi import attach
from inzaghi.channel import Channel
from inzaghi.model import Attachment, Disposition, Doc
from inzaghi.ui.channel_view import ChannelPane
from inzaghi.ui.preview import MIN_PERCENT, START_PERCENT, STEP_PERCENT, Grip
from test_ui import make_app, settle


def deliver(root: Path, name: str, payload: bytes = b"%PDF-1.4 payload") -> Path:
    """Put a file in the attachments folder, without announcing it."""
    path = root / "notifications" / "attachments" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def announce(root: Path, body: str, *, slug: str = "delivery") -> Path:
    return write(root / "notifications" / f"2026-09-05_0530_milestone_{slug}.md", body)


def only(snapshot) -> tuple[Attachment, ...]:
    """The attachments of the one notification in the fixture that has any."""
    assert len(snapshot.attachments) == 1, snapshot.attachments
    return next(iter(snapshot.attachments.values()))


def doc(body: str, meta: dict[str, str] | None = None) -> Doc:
    return Doc(path=Path("n.md"), text=body, meta=meta or {}, body=body, mtime=0.0, size=len(body))


# -- what counts as a delivery -------------------------------------------


def test_a_link_into_the_folder_is_a_delivery_described_by_its_link_text(channel_root):
    deliver(channel_root, "regression-report.pdf", b"x" * 2400)
    announce(
        channel_root,
        "# [milestone] Sweep closed\n\n"
        "Numbers: [the three charts, 12 runs](attachments/regression-report.pdf).\n",
    )
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    assert found.name == "regression-report.pdf"
    assert found.note == "the three charts, 12 runs"
    assert (found.arrival, found.disposition, found.size) == ("here", "view", 2400)
    assert found.openable


def test_a_file_nobody_points_at_stays_invisible(channel_root):
    """The whole reason unreferenced files are ignored: this could be half a file."""
    deliver(channel_root, "orphan.pdf")
    snapshot = Channel(root=channel_root).scan(now=NOW)
    assert snapshot.attachments == {}
    assert not any("orphan" in str(event.path) for event in snapshot.events)


def test_the_front_matter_form_names_files_without_describing_them(channel_root):
    deliver(channel_root, "bench.tar.gz")
    deliver(channel_root, "notes.txt")
    announce(
        channel_root,
        "---\nkind: milestone\nattachments: bench.tar.gz, notes.txt\n---\n\n# [milestone] Done\n",
    )
    found = only(Channel(root=channel_root).scan(now=NOW))
    assert [(a.name, a.note) for a in found] == [("bench.tar.gz", ""), ("notes.txt", "")]


def test_a_name_given_twice_is_delivered_once_keeping_the_description(channel_root):
    deliver(channel_root, "bench.tar.gz")
    announce(
        channel_root,
        "---\nkind: milestone\nattachments: bench.tar.gz\n---\n\n"
        "# [milestone] Done\n\n[raw criterion output](attachments/bench.tar.gz)\n",
    )
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    assert found.note == "raw criterion output"


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/report.pdf",
        "2026-09-04_2325_milestone_shard-2-reindexed.md",
        "report.pdf",  # in the notifications folder, not the attachments one
        "#a-section",
        "mailto:someone@example.com",
    ],
)
def test_a_link_to_anything_else_is_not_a_delivery(target):
    assert attach.refs(doc(f"see [it]({target})")) == []


def test_an_example_in_a_code_block_is_not_a_delivery():
    """Notifications quote commands and listings; a link there is illustration."""
    body = (
        "Run this:\n\n```sh\ntar xzf [bench](attachments/bench.tar.gz)\n```\n\n"
        "and `[inline](attachments/nope.pdf)` too.\n"
    )
    assert attach.refs(doc(body)) == []


def test_a_filename_with_spaces_survives_both_ways_of_writing_it():
    bracketed = attach.refs(doc("[final](<attachments/final report v2.pdf>)"))
    encoded = attach.refs(doc("[final](attachments/final%20report%20v2.pdf)"))
    assert bracketed == encoded == [("final report v2.pdf", "final")]


# -- arrival --------------------------------------------------------------


def test_a_referenced_file_that_is_not_there_is_late_rather_than_gone(channel_root):
    """It was announced, and the folder is synced. Absence is not evidence."""
    announce(channel_root, "# [milestone] Done\n\n[bench](attachments/bench.tar.gz)\n")
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    assert found.arrival == "syncing"
    assert found.size is None
    assert not found.openable
    assert not found.problem  # nothing has gone wrong


def test_a_payload_that_lands_later_is_picked_up_by_the_next_scan(channel_root):
    announce(channel_root, "# [milestone] Done\n\n[bench](attachments/bench.tar.gz)\n")
    channel = Channel(root=channel_root)
    assert only(channel.scan(now=NOW))[0].arrival == "syncing"
    deliver(channel_root, "bench.tar.gz", b"z" * 99)
    # The notification itself has not changed, so its parse is still cached;
    # the answer about the file on disk must not have been cached with it.
    landed = only(channel.scan(now=NOW))[0]
    assert (landed.arrival, landed.size) == ("here", 99)


# -- what may be opened ---------------------------------------------------


@pytest.mark.parametrize(
    ("name", "disposition"),
    [
        ("report.pdf", "view"),
        ("chart.PNG", "view"),
        ("run.log", "view"),
        ("bench.tar.gz", "reveal"),
        ("logs.zip", "reveal"),
        ("diagram.svg", "reveal"),  # a browser would run script in it
        ("install.command", "reveal"),
        ("thing.unheard-of", "reveal"),
    ],
)
def test_only_known_viewable_types_are_handed_to_the_desktop(channel_root, name, disposition):
    deliver(channel_root, name)
    announce(channel_root, f"# [milestone] Done\n\n[f](attachments/{name})\n")
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    assert (found.arrival, found.disposition) == ("here", disposition)


@pytest.mark.parametrize("name", ["notes.txt", "REPORT.MD", "plan.markdown"])
def test_a_type_this_reader_renders_is_read_here_instead(channel_root, name):
    """Launching an application to show Markdown is a detour out of the terminal."""
    deliver(channel_root, name, b"# heading\n")
    announce(channel_root, f"# [milestone] Done\n\n[f](attachments/{name})\n")
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    assert (found.arrival, found.disposition) == ("here", "read")
    assert found.readable


def test_a_text_delivery_is_never_handed_to_a_launcher():
    """It is already on screen; a second window onto it is not showing it."""
    with pytest.raises(attach.CannotOpen, match="shown in the reader"):
        attach.argv(here("notes.md", "read"), platform="linux")
    with pytest.raises(attach.CannotOpen, match="shown in the reader"):
        attach.launch(here("notes.md", "read"), platform="darwin")


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("attachments/../../../etc/passwd", "outside the channel"),
        ("attachments/../README.md", "outside the channel"),
        ("attachments/%2e%2e/%2e%2e/etc/passwd", "outside the channel"),
    ],
)
def test_a_link_that_climbs_out_of_the_folder_is_refused(channel_root, target, reason):
    announce(channel_root, f"# [milestone] Done\n\n[x]({target})\n")
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    assert found.arrival == "refused"
    assert reason in found.problem


@pytest.mark.parametrize("target", ["/etc/passwd", "../../etc/passwd", "https://e.com/x.pdf"])
def test_a_link_outside_the_folder_is_not_a_delivery_at_all(channel_root, target):
    """A link has to name the folder, or citing a sibling file would deliver it."""
    announce(channel_root, f"# [milestone] Done\n\n[x]({target})\n")
    assert Channel(root=channel_root).scan(now=NOW).attachments == {}


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("/etc/passwd", "an absolute path"),
        ("~/.ssh/id_rsa", "an absolute path"),
        ("../../etc/passwd", "outside the channel"),
        ("..\\..\\secrets", "not a plain filename"),
    ],
)
def test_a_header_entry_that_is_not_a_plain_filename_is_refused(channel_root, name, reason):
    """The header form is relative to the folder by definition, so anything
    that tries to be a path is refused rather than reinterpreted."""
    announce(channel_root, f"---\nkind: milestone\nattachments: {name}\n---\n\n# [milestone] X\n")
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    assert found.arrival == "refused"
    assert reason in found.problem


def test_a_symlink_is_refused_rather_than_followed(channel_root, tmp_path):
    secret = tmp_path / "outside.pdf"
    secret.write_bytes(b"not yours")
    folder = channel_root / "notifications" / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.pdf").symlink_to(secret)
    announce(channel_root, "# [milestone] Done\n\n[r](attachments/report.pdf)\n")
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    assert (found.arrival, found.problem) == ("refused", "refused: a symlink")


def test_a_directory_is_not_a_file(channel_root):
    (channel_root / "notifications" / "attachments" / "logs").mkdir(parents=True)
    announce(channel_root, "# [milestone] Done\n\n[logs](attachments/logs)\n")
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    assert (found.arrival, found.problem) == ("refused", "refused: not a file")


def test_a_refused_reference_is_never_launched():
    refused = Attachment(
        name="..", path=Path("/nowhere"), arrival="refused", problem="refused: outside the channel"
    )
    with pytest.raises(attach.CannotOpen, match="outside the channel"):
        attach.launch(refused)


# -- reading one into the pane -------------------------------------------


def readable(root: Path, name: str, payload: bytes) -> Attachment:
    """A ``read`` delivery of ``payload``, resolved the way a scan would."""
    deliver(root, name, payload)
    announce(root, f"# [milestone] Done\n\n[f](attachments/{name})\n")
    (found,) = only(Channel(root=root).scan(now=NOW))
    assert found.disposition == "read"
    return found


def test_the_text_of_a_delivery_is_read_whole_when_it_is_small(channel_root):
    found = readable(channel_root, "notes.md", b"# Shard 3\n\nReindexed.\n")
    assert attach.read_text(found) == ("# Shard 3\n\nReindexed.\n", False)


def test_a_log_that_grew_all_night_is_cut_at_a_line_and_says_so(channel_root):
    """A session writes these unattended; the file at the end of a reference
    is whatever it happened to produce, not something sized for a pane."""
    found = readable(channel_root, "run.txt", b"a line that is long enough\n" * 4000)
    text, truncated = attach.read_text(found, limit=1000)
    assert truncated
    assert len(text) <= 1000
    assert text.endswith("\n"), "cut mid-line"


def test_a_file_that_is_not_text_is_still_shown_as_something(channel_root):
    """Nothing says a .txt is UTF-8. Mojibake beats an empty pane and a stack."""
    found = readable(channel_root, "notes.txt", b"caf\xe9 \xff\xfe latin-1")
    text, _ = attach.read_text(found)
    assert "caf" in text and "latin-1" in text


def test_what_resolve_refused_is_refused_again_at_read_time(channel_root, tmp_path):
    """The snapshot is a poll old; what sits at that path now is the sync
    client's business, so the checks are made again rather than trusted."""
    found = readable(channel_root, "notes.md", b"# fine for now\n")
    secret = tmp_path / "outside.md"
    secret.write_text("# not yours\n")
    found.path.unlink()
    found.path.symlink_to(secret)
    with pytest.raises(attach.CannotOpen, match="symlink"):
        attach.read_text(found)


def test_a_delivery_that_has_not_arrived_is_not_read(channel_root):
    announce(channel_root, "# [milestone] Done\n\n[f](attachments/notes.md)\n")
    (found,) = only(Channel(root=channel_root).scan(now=NOW))
    with pytest.raises(attach.CannotOpen, match="has not arrived"):
        attach.read_text(found)


# -- handing it to the desktop -------------------------------------------


def here(name: str, disposition: Disposition) -> Attachment:
    return Attachment(
        name=name,
        path=Path("/ch/notifications/attachments") / name,
        arrival="here",
        disposition=disposition,
        size=10,
    )


def test_macos_opens_a_viewable_file_and_reveals_everything_else():
    assert attach.argv(here("r.pdf", "view"), platform="darwin") == [
        "open",
        "/ch/notifications/attachments/r.pdf",
    ]
    assert attach.argv(here("b.tar.gz", "reveal"), platform="darwin") == [
        "open",
        "-R",
        "/ch/notifications/attachments/b.tar.gz",
    ]


def test_linux_uses_xdg_open_and_falls_back_to_the_containing_folder():
    """xdg-open has no -R, so revealing means opening the folder itself."""
    assert attach.argv(here("r.pdf", "view"), platform="linux") == [
        "xdg-open",
        "/ch/notifications/attachments/r.pdf",
    ]
    assert attach.argv(here("b.tar.gz", "reveal"), platform="linux") == [
        "xdg-open",
        "/ch/notifications/attachments",
    ]


def test_a_relatively_named_channel_still_gets_an_absolute_command(monkeypatch, tmp_path):
    """The launcher is a separate process; it does not share our cwd on purpose."""
    monkeypatch.chdir(tmp_path)
    relative = Attachment(
        name="r.pdf",
        path=Path("channels/n/notifications/attachments/r.pdf"),
        arrival="here",
        disposition="view",
        size=1,
    )
    assert attach.argv(relative, platform="linux") == [
        "xdg-open",
        str(tmp_path / "channels/n/notifications/attachments/r.pdf"),
    ]


def test_a_platform_with_no_launcher_says_so_instead_of_guessing():
    with pytest.raises(attach.CannotOpen, match="win32"):
        attach.argv(here("r.pdf", "view"), platform="win32")


def test_a_launcher_that_is_not_installed_is_reported(monkeypatch):
    """A Linux box without xdg-open must say that, not fail silently."""

    def missing(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(attach.subprocess, "Popen", missing)
    with pytest.raises(attach.CannotOpen, match="xdg-open"):
        attach.launch(here("r.pdf", "view"), platform="linux")


def test_a_launcher_that_refuses_the_file_is_reported(monkeypatch):
    class Refuses:
        returncode = 1

        def communicate(self, timeout: float = 0.0):
            return b"", b"xdg-open: no method available for opening it\n"

        def poll(self):
            return 1

    monkeypatch.setattr(attach.subprocess, "Popen", lambda *_a, **_k: Refuses())
    with pytest.raises(attach.CannotOpen, match="no method available"):
        attach.launch(here("r.pdf", "view"), platform="linux")


def test_a_launcher_that_holds_on_is_left_running(monkeypatch):
    """Some xdg-open handlers do not return until the viewer they started exits."""

    class Holds:
        returncode = None

        def communicate(self, timeout: float = 0.0):
            raise subprocess.TimeoutExpired(cmd="xdg-open", timeout=timeout)

        def poll(self):
            return None

    holding = Holds()
    monkeypatch.setattr(attach.subprocess, "Popen", lambda *_a, **_k: holding)
    assert attach.launch(here("r.pdf", "view"), platform="linux") == [
        "xdg-open",
        "/ch/notifications/attachments/r.pdf",
    ]
    assert holding in attach._DETACHED  # not killed, and not collectable
    attach._DETACHED.discard(holding)


# -- in the UI ------------------------------------------------------------


@pytest.fixture
def delivered(channel_root):
    """A channel whose newest milestone delivered two files, one still in flight."""
    deliver(channel_root, "regression-report.pdf", b"x" * 4096)
    announce(
        channel_root,
        "# [milestone] Sweep closed\n\n"
        "Charts: [the three charts](attachments/regression-report.pdf).\n"
        "Raw: [criterion output, 12 runs](attachments/bench.tar.gz).\n",
        slug="sweep-closed",
    )
    return channel_root


async def open_pane(app, pilot) -> ChannelPane:
    await settle(app, pilot)
    app.query_one("#tabs", TabbedContent).active = "ch0"
    await pilot.pause()
    await pilot.press("a")  # settle unread, so only the files are still moving
    await settle(app, pilot)
    return app.query_one(ChannelPane)


def select(app, key: str) -> None:
    timeline = app.screen.query_one("#timeline", OptionList)
    timeline.highlighted = timeline.get_option_index(key)


def strip(app) -> OptionList:
    return app.screen.query_one("#attachments", OptionList)


def labels(app) -> list[str]:
    listing = strip(app)
    return [str(listing.get_option_at_index(i).prompt) for i in range(listing.option_count)]


async def test_the_strip_lists_what_an_entry_delivered(delivered):
    app = make_app(delivered)
    async with app.run_test() as pilot:
        pane = await open_pane(app, pilot)
        select(app, str(delivered / "notifications" / "2026-09-05_0530_milestone_sweep-closed.md"))
        await pilot.pause()
        assert strip(app).display is True
        shown = labels(app)
        assert "regression-report.pdf" in shown[0] and "4.0 kB" in shown[0]
        assert "the three charts" in shown[0]
        assert "bench.tar.gz" in shown[1] and "waiting on sync" in shown[1]
        assert pane.check_action("attachments", ()) is True


async def test_the_strip_stays_out_of_the_way_of_prose(delivered):
    """Most notifications deliver nothing, so the key is hidden, not dimmed."""
    app = make_app(delivered)
    async with app.run_test() as pilot:
        pane = await open_pane(app, pilot)
        select(app, "pin:STATUS.md")
        await pilot.pause()
        assert strip(app).display is False
        assert pane.check_action("attachments", ()) is False


async def test_a_payload_arriving_updates_the_strip_without_moving_the_reader(delivered):
    """The prose did not change, so nothing about the document may be re-rendered."""
    app = make_app(delivered)
    async with app.run_test() as pilot:
        pane = await open_pane(app, pilot)
        select(app, str(delivered / "notifications" / "2026-09-05_0530_milestone_sweep-closed.md"))
        await pilot.pause()
        assert "waiting on sync" in labels(app)[1]
        showing = pane._showing

        deliver(delivered, "bench.tar.gz", b"z" * 2048)
        renders = 0
        original = pane._render_rows

        def counted():
            nonlocal renders
            renders += 1
            original()

        pane._render_rows = counted  # type: ignore[method-assign]
        app.rescan()
        await settle(app, pilot)
        assert renders == 0, "the timeline was rebuilt for a file landing"

        assert "2.0 kB" in labels(app)[1]
        assert "in folder" in labels(app)[1]  # an archive is never viewed
        assert pane._showing == showing


async def test_opening_a_file_never_blocks_the_ui(delivered):
    """A launcher waits on the sync client: iCloud downloads an evicted file first."""
    app = make_app(delivered)
    callers: list[int] = []

    def watched(attachment, **kwargs):
        callers.append(threading.get_ident())
        return ["open", str(attachment.path)]

    async with app.run_test() as pilot:
        await open_pane(app, pilot)
        ui_thread = threading.get_ident()
        select(app, str(delivered / "notifications" / "2026-09-05_0530_milestone_sweep-closed.md"))
        await pilot.pause()
        with mock.patch("inzaghi.ui.app.attach.launch", watched):
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("enter")
            await settle(app, pilot)

    assert callers, "nothing was opened"
    assert ui_thread not in callers


async def test_a_file_that_has_not_arrived_is_not_launched(delivered):
    app = make_app(delivered)
    async with app.run_test() as pilot:
        await open_pane(app, pilot)
        select(app, str(delivered / "notifications" / "2026-09-05_0530_milestone_sweep-closed.md"))
        await pilot.pause()
        with mock.patch("inzaghi.ui.app.attach.launch") as launcher:
            await pilot.press("v")
            await pilot.pause()
            strip(app).highlighted = 1  # the one still crossing the sync
            await pilot.press("enter")
            await settle(app, pilot)
        launcher.assert_not_called()


async def test_a_read_only_channel_can_still_be_read_from(delivered):
    """Opening a delivered file is reading; read-only forbids writing."""
    app = make_app(delivered, read_only=True)
    async with app.run_test() as pilot:
        await open_pane(app, pilot)
        select(app, str(delivered / "notifications" / "2026-09-05_0530_milestone_sweep-closed.md"))
        await pilot.pause()
        with mock.patch("inzaghi.ui.app.attach.launch", return_value=["open", "x"]) as launcher:
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("enter")
            await settle(app, pilot)
        launcher.assert_called_once()


def click(pane, href: str, selector: str = "#doc") -> None:
    """A click on a link, posted where Textual posts one: at the widget.

    Not at the pane. The Markdown widget has a handler of its own for this
    message, and that handler is the one that used to hand the href to the
    desktop, so a test that skipped it proved nothing.
    """
    document = pane.query_one(selector, Markdown)
    document.post_message(Markdown.LinkClicked(document, href))


async def test_clicking_the_reference_in_the_prose_opens_the_same_file(delivered):
    """The link the session wrote is where the eye lands, so it has to work."""
    app = make_app(delivered)
    async with app.run_test() as pilot:
        pane = await open_pane(app, pilot)
        select(app, str(delivered / "notifications" / "2026-09-05_0530_milestone_sweep-closed.md"))
        await pilot.pause()
        with mock.patch("inzaghi.ui.app.attach.launch", return_value=["open", "x"]) as launcher:
            click(pane, "attachments/regression-report.pdf")
            await settle(app, pilot)
        launcher.assert_called_once()


async def test_a_link_to_anywhere_else_in_the_prose_launches_nothing(delivered):
    """This is a channel reader, not a browser."""
    app = make_app(delivered)
    async with app.run_test() as pilot:
        pane = await open_pane(app, pilot)
        select(app, str(delivered / "notifications" / "2026-09-05_0530_milestone_sweep-closed.md"))
        await pilot.pause()
        with mock.patch("inzaghi.ui.app.attach.launch") as launcher:
            click(pane, "https://example.com/report.pdf")
            click(pane, "attachments/bench.tar.gz")  # announced, still syncing
            await settle(app, pilot)
        launcher.assert_not_called()


async def test_the_markdown_widget_never_opens_a_href_itself(delivered):
    """Textual's Markdown hands a clicked href to ``app.open_url`` -- the web
    browser, or xdg-open -- unless it is built with ``open_links=False``. That
    would put every decision here in the hands of whatever an unattended
    session wrote, and it runs before this pane hears about the click."""
    app = make_app(delivered)
    async with app.run_test() as pilot:
        pane = await open_pane(app, pilot)
        select(app, str(delivered / "notifications" / "2026-09-05_0530_milestone_sweep-closed.md"))
        await pilot.pause()
        with mock.patch.object(type(app), "open_url") as opener:
            with mock.patch("inzaghi.ui.app.attach.launch", return_value=["open", "x"]):
                click(pane, "https://example.com/report.pdf")
                click(pane, "file:///etc/passwd")
                click(pane, "attachments/regression-report.pdf")
                await settle(app, pilot)
        opener.assert_not_called()
        assert pane.query_one("#doc", Markdown)._open_links is False


async def test_the_timeline_marks_the_entry_that_carried_files(delivered):
    app = make_app(delivered)
    async with app.run_test() as pilot:
        await open_pane(app, pilot)
        timeline = app.screen.query_one("#timeline", OptionList)
        marked = [
            str(timeline.get_option_at_index(i).prompt)
            for i in range(timeline.option_count)
            if "⧉" in str(timeline.get_option_at_index(i).prompt)
        ]
        assert len(marked) == 1
        assert "⧉2" in marked[0]  # announced, not arrived: one is still in flight


# -- read in the pane itself ---------------------------------------------


ENTRY = "notifications/2026-09-05_0530_milestone_sweep-closed.md"


@pytest.fixture
def read_here(channel_root):
    """A channel whose newest milestone delivered a note and a tail of a log."""
    deliver(channel_root, "shard-3-notes.md", b"# Shard 3\n\nChecksums verified.\n")
    deliver(channel_root, "tail.txt", b"worker-2: reindex complete\n")
    announce(
        channel_root,
        "# [milestone] Sweep closed\n\n"
        "Notes: [what shard 3 did](attachments/shard-3-notes.md).\n"
        "Tail: [the last lines](attachments/tail.txt).\n",
        slug="sweep-closed",
    )
    return channel_root


async def open_file(app, pilot, index: int = 0) -> ChannelPane:
    """Select the entry that delivered files, then ask for one of them."""
    pane = await open_pane(app, pilot)
    select(app, str(Path(app.channels[0].root) / ENTRY))
    await pilot.pause()
    await pilot.press("v")
    await pilot.pause()
    strip(app).highlighted = index
    await pilot.press("enter")
    await settle(app, pilot)
    return pane


def rendered(app) -> str:
    return str(app.screen.query_one("#preview-md", Markdown)._markdown)


def plain(app) -> str:
    return str(app.screen.query_one("#preview-text", Static).content)


async def test_markdown_is_rendered_in_the_pane_instead_of_being_launched(read_here):
    """It is the format this reader is made of; starting an application for it
    would leave the terminal to show what the terminal can show."""
    app = make_app(read_here)
    async with app.run_test() as pilot:
        with mock.patch("inzaghi.ui.app.attach.launch") as launcher:
            pane = await open_file(app, pilot)
        launcher.assert_not_called()
        assert "in reader" in labels(app)[0]
        assert pane.preview.display is True
        assert "Checksums verified" in rendered(app)
        assert app.screen.query_one("#preview-text", Static).display is False
        # The keyboard follows the file, so it can be scrolled and resized
        # without hunting for it first.
        assert app.screen.query_one("#preview-body").has_focus


async def test_plain_text_is_shown_as_the_text_it_is(read_here):
    """A log is not improved by having its punctuation read as Markdown."""
    app = make_app(read_here)
    async with app.run_test() as pilot:
        await open_file(app, pilot, index=1)
        assert "worker-2: reindex complete" in plain(app)
        assert app.screen.query_one("#preview-md", Markdown).display is False


async def test_reading_a_delivery_never_blocks_the_ui(read_here):
    """The file is on a synced volume, which answers when it likes."""
    app = make_app(read_here)
    callers: list[int] = []

    def watched(attachment, **kwargs):
        callers.append(threading.get_ident())
        return "# read off-thread\n", False

    async with app.run_test() as pilot:
        ui_thread = threading.get_ident()
        with mock.patch("inzaghi.ui.app.attach.read_text", watched):
            await open_file(app, pilot)

    assert callers, "nothing was read"
    assert ui_thread not in callers


async def test_a_rewritten_file_is_re_read_without_taking_the_keyboard(read_here):
    """A session rewrites its own files. The text has to follow; the cursor
    and the scroll position belong to whoever is reading."""
    app = make_app(read_here)
    async with app.run_test() as pilot:
        pane = await open_file(app, pilot)
        pane.focus_timeline()
        await pilot.pause()
        body = app.screen.query_one("#preview-body")
        scrolls: list[int] = []
        body.scroll_home = lambda **kw: scrolls.append(1)

        deliver(read_here, "shard-3-notes.md", b"# Shard 3\n\nChecksums verified twice.\n")
        app.rescan()
        await settle(app, pilot)

        assert "verified twice" in rendered(app)
        assert scrolls == [], "the file was scrolled home for a re-read"
        assert app.screen.query_one("#timeline", OptionList).has_focus


async def test_a_file_changing_leaves_the_cursor_on_the_one_it_was_on(read_here):
    """The strip is refilled whenever any delivery changes, and a session
    appending to a log it delivered changes one every poll."""
    app = make_app(read_here)
    async with app.run_test() as pilot:
        await open_file(app, pilot, index=1)  # the cursor is on tail.txt
        deliver(read_here, "shard-3-notes.md", b"# Shard 3\n\nVerified twice.\n")
        app.rescan()
        await settle(app, pilot)
        assert strip(app).highlighted == 1
        assert "worker-2: reindex complete" in plain(app), "the open file changed under it"


async def test_a_sibling_landing_does_not_re_read_the_open_file(read_here):
    """Re-reading the volume for a file nothing has said anything about."""
    app = make_app(read_here)
    async with app.run_test() as pilot:
        await open_file(app, pilot)
        deliver(read_here, "tail.txt", b"worker-2: reindex complete\nand again\n")
        with mock.patch("inzaghi.ui.app.attach.read_text") as reader:
            app.rescan()
            await settle(app, pilot)
        reader.assert_not_called()


async def test_moving_to_another_entry_takes_its_files_with_it(read_here):
    """A delivered file belongs to the notification that announced it."""
    app = make_app(read_here)
    async with app.run_test() as pilot:
        pane = await open_file(app, pilot)
        select(app, "pin:STATUS.md")
        await settle(app, pilot)
        assert pane.preview.display is False
        assert pane.preview.showing_name is None


async def test_a_text_delivery_still_crossing_the_sync_is_not_read(channel_root):
    app = make_app(channel_root)
    announce(
        channel_root,
        "# [milestone] Sweep closed\n\nNotes: [what shard 3 did](attachments/late.md).\n",
        slug="sweep-closed",
    )
    async with app.run_test() as pilot:
        with mock.patch("inzaghi.ui.app.attach.read_text") as reader:
            pane = await open_file(app, pilot)
        reader.assert_not_called()
        assert pane.preview.display is False


async def test_a_link_in_the_prose_opens_the_file_in_the_pane_too(read_here):
    app = make_app(read_here)
    async with app.run_test() as pilot:
        pane = await open_pane(app, pilot)
        select(app, str(read_here / ENTRY))
        await pilot.pause()
        click(pane, "attachments/shard-3-notes.md")
        await settle(app, pilot)
        assert "Checksums verified" in rendered(app)


async def test_a_link_inside_a_delivered_file_is_the_sessions_text_not_ours(read_here):
    """What the file links to is content. Acting on it would make the reader a
    browser for whatever an unattended session wrote."""
    app = make_app(read_here)
    async with app.run_test() as pilot:
        pane = await open_file(app, pilot)
        with mock.patch("inzaghi.ui.app.attach.read_text") as reader:
            with mock.patch("inzaghi.ui.app.attach.launch") as launcher:
                click(pane, "attachments/tail.txt", selector="#preview-md")
                await settle(app, pilot)
        reader.assert_not_called()
        launcher.assert_not_called()


# -- the split ------------------------------------------------------------


async def test_the_split_starts_at_three_quarters_and_is_keyed_from_there(read_here):
    app = make_app(read_here)
    async with app.run_test() as pilot:
        pane = await open_file(app, pilot)
        # Somebody who asked for a file is reading the file; the notification
        # that announced it keeps the quarter above.
        assert pane.preview.percent == START_PERCENT == 75

        await pilot.press("minus")
        assert pane.preview.percent == START_PERCENT - STEP_PERCENT
        await pilot.press("plus", "plus")
        assert pane.preview.percent == START_PERCENT + STEP_PERCENT
        assert pane.preview.styles.height.value == START_PERCENT + STEP_PERCENT

        for _ in range(40):
            await pilot.press("minus")
        # Never to nothing: a pane at no rows is indistinguishable from one
        # that is not there, and closing has a key of its own.
        assert pane.preview.percent == MIN_PERCENT
        assert pane.preview.display is True


async def test_the_divider_can_be_dragged(read_here):
    app = make_app(read_here)
    async with app.run_test() as pilot:
        pane = await open_file(app, pilot)
        preview = pane.preview

        preview.post_message(Grip.Dragged(-3))  # pulled up the screen
        await pilot.pause()
        taller = preview.percent
        assert taller > START_PERCENT

        preview.post_message(Grip.Dragged(3))
        await pilot.pause()
        assert preview.percent < taller

        preview.post_message(Grip.Dragged(200))  # thrown at the bottom
        await pilot.pause()
        assert preview.percent == MIN_PERCENT


async def test_escape_closes_the_file_and_hands_the_keyboard_back(read_here):
    app = make_app(read_here)
    async with app.run_test() as pilot:
        pane = await open_file(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert pane.preview.display is False
        assert strip(app).has_focus  # where it was opened from
