"""The Inzaghi application.

Structure: an overview tab, then one tab per channel.  A background thread
re-reads the folders on a timer -- a sync client's writes arrive without any
local filesystem event, and the volume can block, so scanning never runs on the
UI thread, and never more than one scan at a time.  A separate one-second tick
refreshes only the countdowns.
"""

from __future__ import annotations

import asyncio
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import count
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.timer import Timer
from textual.widgets import Footer, Header, TabbedContent, TabPane

from .. import attach
from .. import compose as composer
from .. import fmt
from ..channel import Channel, absence_is_real, remove_conflicts
from ..compose import QUICK_ACTIONS, QUICK_BY_KEYWORD, QuickAction, ReadOnlyChannel
from ..config import Config
from ..model import LOUD_KINDS, Attachment, Snapshot
from ..state import ReadState
from .channel_view import ChannelPane
from .modals import ConfirmScreen
from .mounting import composed
from .overview import OverviewPane
from .rows import HEALTH_STYLE
from .vim import half_page, move

OVERVIEW_ID = "overview"
#: Inside OverviewPane; its presence answers for the whole overview subtree.
OVERVIEW_TABLE = "#overview-table"

#: Which way ``ctrl+w`` and a key means to move the keyboard.
PANES = {"h": "left", "j": "down", "k": "up", "l": "right"}

#: Every two-key sequence, as {prefix: {second key: what it runs}}.  One table
#: rather than a chain of conditions, because it is also the answer to "may
#: this key be taken out of the chain" -- see ``check_action``.
CHORDS: dict[str, dict[str, str]] = {
    "g": {"g": "motion('top')", "f": "open_reference"},
    "window": {key: f"pane('{way}')" for key, way in PANES.items()},
    "]": {"f": "reference(1)"},
    "[": {"f": "reference(-1)"},
}

#: Threads set aside for the calls that wait on a channel's volume: the scan
#: and the discovery sweep.  They are kept apart from the pool Textual hands
#: every other worker so that a volume which has stopped answering cannot take
#: sending, deleting and opening down with it -- and bounded so that a wedged
#: read costs one thread rather than a new one every poll.  One in flight of
#: each, plus room for the threads a cancelled worker leaves behind.
VOLUME_THREADS = 4

#: How long a prefix waits for the key that finishes it.  Vim calls this
#: timeoutlen.  A sequence nobody completed has to expire rather than sit
#: there waiting to give the next h a meaning it was not typed for.
CHORD_SECONDS = 1.0


class InzaghiApp(App):
    CSS_PATH = "inzaghi.tcss"
    TITLE = "Inzaghi"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("o", "overview", "Overview"),
        Binding("tab", "next_channel", "Next", show=False),
        # The arrows step through the tabs from wherever the keyboard is,
        # because the widgets that bind them cannot use them: a vertical-only
        # scroll has no sideways to go, and a row cursor has no column to move
        # to, and Textual hands a key back rather than eating it when the
        # action it is bound to would do nothing. A text box is the exception,
        # and keeps them: in a draft they are a cursor.
        Binding("right", "next_channel", "Next channel", show=False),
        Binding("left", "prev_channel", "Prev channel", show=False),
        Binding("c", "compose", "Compose"),
        Binding("s", "quick('STATUS')", "Status"),
        Binding("p", "quick('PAUSE')", "Pause"),
        Binding("u", "quick('RESUME')", "Resume", show=False),
        Binding("x", "quick('STOP')", "Stop"),
        Binding("a", "mark_all_read", "Mark read"),
        Binding("K", "clean_conflicts", "Clean"),
        Binding("r", "refresh_all", "Refresh"),
        Binding("i", "compose", "Compose", show=False),  # insert, as in write
        Binding("semicolon", "command_palette", "Commands", show=False),
        # -- vim motions, in whichever pane holds the keyboard -------------
        # j and k are the arrows, down to the wrap at the end of a list: the
        # same key doing the same thing, not a second nearly-identical one.
        Binding("j", "motion('down')", "Down", show=False),
        Binding("k", "motion('up')", "Up", show=False),
        Binding("G", "motion('bottom')", "Bottom", show=False),
        Binding("ctrl+d", "half(1)", "Half page down", show=False),
        Binding("ctrl+u", "half(-1)", "Half page up", show=False),
        # -- the two-key sequences ------------------------------------------
        # A prefix arms; the keys that complete it are the priority bindings
        # below, which run ahead of everything else in the chain and are
        # refused by check_action until there is something to complete. That
        # is what lets g, h and l keep their own meanings the rest of the time.
        Binding("g", "prefix('g')", "Top (gg)", show=False),
        Binding("ctrl+w", "prefix('window')", "Pane", show=False),
        # ] and [ are prefixes now rather than channel keys of their own:
        # h, l, the arrows and tab all still step the tabs, and ]f is worth
        # more than a fifth way to do that.
        Binding("]", "prefix(']')", "Next file", show=False),
        Binding("[", "prefix('[')", "Prev file", show=False),
        Binding("g", "chord('g')", priority=True, show=False),
        Binding("h", "chord('h')", priority=True, show=False),
        Binding("j", "chord('j')", priority=True, show=False),
        Binding("k", "chord('k')", priority=True, show=False),
        Binding("l", "chord('l')", priority=True, show=False),
        Binding("f", "chord('f')", priority=True, show=False),
        # Channels are the only horizontal axis here, so h and l are what the
        # arrows are.
        Binding("l", "next_channel", "Next channel", show=False),
        Binding("h", "prev_channel", "Prev channel", show=False),
    ]

    def __init__(self, config: Config, channels: list[Channel] | None = None) -> None:
        super().__init__()
        self.config = config
        self.channels = channels if channels is not None else config.discover()
        self.state = ReadState.load()
        self.snapshots: dict[str, Snapshot] = {}
        self._pane_ids: dict[str, str] = {}  # channel key -> TabPane id
        # Pane ids are never reused: a removed tab's id must not collide
        # with a later one, or Textual would mount into the wrong place.
        self._pane_counter = count()
        self._known_events: dict[str, set[str]] = {}
        self._known_health: dict[str, str] = {}
        #: Where a read of a channel's volume happens.  See ``VOLUME_THREADS``.
        self._volume = ThreadPoolExecutor(
            max_workers=VOLUME_THREADS, thread_name_prefix="inzaghi-volume"
        )
        #: Whether a scan or a sweep is out there, and whether one was asked
        #: for while it was.  A blocked read cannot be cancelled, only waited
        #: for, so the only way not to pile them up is not to start them.
        self._scanning = False
        self._scan_again = False
        self._discovering = False
        #: Set once the app is going away, so that a timer which fires during
        #: teardown does not hand work to a pool that has been shut down.
        self._closing = False
        #: The half-typed key sequence, if there is one, and the timer that
        #: gives up on it.
        self._pending: str | None = None
        self._pending_timer: Timer | None = None

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("Overview", id=OVERVIEW_ID):
                yield OverviewPane()
            for channel in self.channels:
                pane_id = self._new_pane_id(channel)
                with TabPane(channel.name, id=pane_id):
                    yield ChannelPane(channel)
        yield Footer()

    def on_mount(self) -> None:
        self.prune_receipts(self.config, set())
        self.rescan()
        self.set_interval(self.config.poll_seconds, self.rescan)
        self.set_interval(self.config.discover_seconds, self.rediscover)
        self.set_interval(1.0, self._tick)

    def _new_pane_id(self, channel: Channel) -> str:
        pane_id = f"ch{next(self._pane_counter)}"
        self._pane_ids[channel.key] = pane_id
        return pane_id

    def on_unmount(self) -> None:
        self._closing = True
        self.state.save()
        # Without waiting: a read that is still out there is out there on a
        # volume that has stopped answering, and quitting must not wait on it.
        self._volume.shutdown(wait=False, cancel_futures=True)

    # -- scanning ---------------------------------------------------------

    def action_refresh_all(self) -> None:
        """Re-read the config, look for new channels, and rescan every folder."""
        self.rediscover()

    def rescan(self) -> None:
        """Ask for a re-read of every channel, unless one is already out.

        A scan reads a volume a sync client owns, and such a read answers when
        that client feels like it. Textual can cancel the worker waiting on
        one; nothing can cancel the read itself, so the thread stays where it
        is. A poll firing every couple of seconds at a volume that has gone
        quiet therefore does not queue scans, it accumulates threads -- and
        they come from the pool that sending, deleting and opening are drawing
        on, so once it fills, everything that touches a disk stops until the
        app is restarted.

        So: one scan in flight, ever. A request that arrives while one is out
        is remembered rather than dropped -- the rescan after a send has to see
        the message it sent -- and runs when the scan comes back.
        """
        if self._closing:
            return
        if self._scanning:
            self._scan_again = True
            return
        self._scanning = True
        self._scan()

    @work(group="scan")
    async def _scan(self) -> None:
        """Read every channel on the volume pool, then hand the UI the result.

        The awaited part is the only part that blocks; everything either side
        of it runs on the UI thread, which is what lets the result be applied
        directly rather than posted back, and what makes cancelling this worker
        at exit enough to stop it reaching a screen that has gone away.
        """
        now = datetime.now().astimezone()
        loop = asyncio.get_running_loop()
        try:
            scanned = await loop.run_in_executor(self._volume, self._read_channels, now)
        finally:
            # A scan that was cancelled, or that failed, must not leave the
            # door shut behind it. The thread it left may still be blocked --
            # the pool is bounded for exactly that -- but the next poll is
            # allowed to try.
            self._scanning = False
        self._apply(scanned, now)
        if self._scan_again:
            self._scan_again = False
            self.rescan()

    def _read_channels(self, now: datetime) -> dict[str, Snapshot]:
        """Every channel, read on a volume thread. Never on the UI's."""
        scanned: dict[str, Snapshot] = {}
        for channel in list(self.channels):
            try:
                scanned[channel.key] = channel.scan(now=now)
            except OSError:
                continue  # a mount that went away; keep the last good snapshot
        return scanned

    def rediscover(self) -> None:
        """Look for channels that have appeared or gone since the last pass.

        Guarded like the scan above, and for the same reason: the sweep stats
        folders on the volume that has stopped answering.
        """
        if self._closing or self._discovering:
            return
        self._discovering = True
        self._discover()

    @work(group="discover")
    async def _discover(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            config, found, gone = await loop.run_in_executor(
                self._volume, self._read_config
            )
        finally:
            self._discovering = False
        await self._sync_channels(config, found, gone)

    def _read_config(self) -> tuple[Config, list[Channel], set[str]]:
        """Re-read the config and decide what has appeared or gone.

        Believing an absence means stat-ing the channel's own folder -- and a
        channel drops out of discovery precisely when its volume has stopped
        answering, so that is the one call most likely to block. Asked here, on
        a volume thread, for the same reason the scan is here.
        """
        config = self.config.reload()
        found = config.discover()
        present = {channel.key for channel in found}
        gone = {
            channel.key
            for channel in list(self.channels)
            if channel.key not in present and absence_is_real(channel.root)
        }
        return config, found, gone

    async def _sync_channels(
        self, config: Config, found: list[Channel], gone: set[str]
    ) -> None:
        self.config = config
        tabs = self.query_one("#tabs", TabbedContent)

        # An absent channel means "deleted" only when its absence can be
        # believed; an unmounted volume must not take the tabs with it. That
        # judgement is made in the worker above, where it is allowed to block.
        for channel in [c for c in self.channels if c.key in gone]:
            await self._drop_channel(tabs, channel)

        for channel in found:
            existing = next((c for c in self.channels if c.key == channel.key), None)
            if existing is None:
                await self._add_channel(tabs, channel)
            else:
                # Keep the live object -- it holds the document cache -- but let
                # an edited config change what we call it and whether we may write.
                existing.name = channel.name
                existing.read_only = channel.read_only

        self.prune_receipts(config, gone)
        self.rescan()

    def prune_receipts(self, config: Config, gone: set[str]) -> None:
        """Forget read receipts for channels nobody can use again.

        Two things make a channel's receipts dead weight: this config no longer
        points anywhere near it, or it was deleted from a volume we could see
        at the time. Neither question is put to the channel's own folder -- a
        synced folder is allowed to be absent, half-there or an hour behind,
        and none of that is evidence about anything.
        """
        for key in gone:
            self.state.forget(key)
        # A config naming nothing would condemn every channel at once. That is
        # a config being written, or one that has not been written yet, not a
        # decision to forget anything.
        if config.roots or config.channels:
            for key in [k for k in self.state.seen if not config.watches(Path(k))]:
                self.state.forget(key)
        self.state.save()

    async def _add_channel(self, tabs: TabbedContent, channel: Channel) -> None:
        self.channels.append(channel)
        await tabs.add_pane(TabPane(channel.name, ChannelPane(channel), id=self._new_pane_id(channel)))
        self.notify(f"New channel: {channel.name}")

    async def _drop_channel(self, tabs: TabbedContent, channel: Channel) -> None:
        pane_id = self._pane_ids.pop(channel.key, None)
        self.channels.remove(channel)
        self.snapshots.pop(channel.key, None)
        self._known_events.pop(channel.key, None)
        self._known_health.pop(channel.key, None)
        if pane_id:
            await tabs.remove_pane(pane_id)

    def _apply(self, scanned: dict[str, Snapshot], now: datetime) -> None:
        for key, snapshot in scanned.items():
            self._alert(key, snapshot, now)
        self.snapshots.update(scanned)
        self._refresh_widgets(now)
        self.state.save()

    def _refresh_widgets(self, now: datetime) -> None:
        unread_paths: dict[str, set[str]] = {}
        for channel in self.channels:
            snapshot = self.snapshots.get(channel.key)
            if snapshot is None:
                continue
            unread = {str(event.path) for event in self.state.unread(channel.key, snapshot)}
            unread_paths[channel.key] = unread
            pane = self._pane_for(channel.key)
            if pane is not None:
                pane.update(snapshot, unread, now)
            self._badge(channel, snapshot, len(unread), now)
        if composed(self, OVERVIEW_TABLE):
            self.query_one(OverviewPane).update(self.channels, self.snapshots, unread_paths, now)
        # check_action() results are cached, so the footer would keep offering
        # "k Clean" after the last conflict copy was deleted.
        self.refresh_bindings()

    def _tick(self) -> None:
        """Cheap per-second refresh: countdowns only, no disk access.

        The first of these fires one second in, which is not always long
        enough for the tabs to have mounted -- see ``ui.mounting``.
        """
        now = datetime.now().astimezone()
        for pane in self.query(ChannelPane):
            pane.update_strip(now)
        if not composed(self, OVERVIEW_TABLE):
            return
        unread = {
            channel.key: {str(event.path) for event in self.state.unread(channel.key, snapshot)}
            for channel in self.channels
            if (snapshot := self.snapshots.get(channel.key)) is not None
        }
        self.query_one(OverviewPane).update(self.channels, self.snapshots, unread, now)

    def _badge(self, channel: Channel, snapshot: Snapshot, unread: int, now: datetime) -> None:
        """Put liveness and unread count on the tab itself."""
        pane_id = self._pane_ids.get(channel.key)
        if not pane_id:
            return
        mark, colour = HEALTH_STYLE[snapshot.health(now)]
        label = f"[{colour}]{mark}[/] {channel.name}"
        if snapshot.waiting:
            label += " [bold red]![/]"
        elif unread:
            label += f" [b]{unread}[/]"
        try:
            self.query_one("#tabs", TabbedContent).get_tab(pane_id).label = label
        except Exception:  # a tab can be mid-teardown during a reload
            pass

    # -- alerts -----------------------------------------------------------

    def _alert(self, key: str, snapshot: Snapshot, now: datetime) -> None:
        """Notify on things that appeared since the last scan, never on backlog."""
        seen = self._known_events.get(key)
        paths = {str(event.path) for event in snapshot.events}
        if seen is not None:
            for event in snapshot.events:
                if str(event.path) in seen or event.kind not in LOUD_KINDS:
                    continue
                if getattr(self.config.alerts, event.kind.replace("-", "_"), True):
                    self._shout(f"{snapshot.name}: {event.title}", severity="error")
        self._known_events[key] = paths

        health = snapshot.health(now)
        was = self._known_health.get(key)
        # Only a transition is news. A channel that was already late when
        # Inzaghi opened is backlog, the same as the events above -- it is
        # visible in the overview without a popup demanding attention.
        if was == "fresh" and health in {"late", "stale"} and self.config.alerts.heartbeat_late:
            self._shout(f"{snapshot.name}: no heartbeat since its deadline", severity="warning")
        self._known_health[key] = health

    def _shout(self, message: str, severity: str = "information") -> None:
        self.notify(message, severity=severity, timeout=30)
        if self.config.alerts.bell:
            self.bell()
        if self.config.alerts.banner:
            self._raise_banner(message)

    @work(thread=True, group="banner")
    def _raise_banner(self, message: str) -> None:
        """osascript is a process launch, and slow enough to be felt."""
        _banner(message)

    # -- navigation -------------------------------------------------------

    @on(OverviewPane.Open)
    def _open_channel(self, event: OverviewPane.Open) -> None:
        pane_id = self._pane_ids.get(event.channel_key)
        if pane_id:
            self.query_one("#tabs", TabbedContent).active = pane_id
            pane = self._pane_for(event.channel_key)
            if pane is not None:
                pane.focus_timeline()

    @on(TabbedContent.TabActivated)
    def _focus_active_pane(self, event: TabbedContent.TabActivated) -> None:
        """Put the cursor where the keyboard is useful the moment a tab opens."""
        pane = event.pane
        for widget in pane.query(OverviewPane):
            widget.focus_table()
            return
        for widget in pane.query(ChannelPane):
            widget.focus_timeline()
            self.refresh_bindings()
            return

    def action_overview(self) -> None:
        self.query_one("#tabs", TabbedContent).active = OVERVIEW_ID

    # -- vim keys ---------------------------------------------------------

    def action_motion(self, motion: str) -> None:
        """j, k, gg, G: whatever the pane holding the keyboard makes of them."""
        if self.focused is not None:
            move(self.focused, motion)

    def action_half(self, direction: int) -> None:
        if self.focused is not None:
            half_page(self.focused, direction)

    def action_prefix(self, name: str) -> None:
        """Arm a two-key sequence: ``g`` for gg, ``ctrl+w`` for the panes.

        Nothing is drawn to say it is armed, the way vim draws nothing for the
        g in gg. The timer is what keeps that honest: a prefix that is never
        completed goes away instead of waiting all afternoon to swallow a key.
        """
        self._pending = name
        if self._pending_timer is not None:
            self._pending_timer.stop()
        self._pending_timer = self.set_timer(CHORD_SECONDS, self._forget_prefix)

    def _forget_prefix(self) -> None:
        self._pending = None
        self._pending_timer = None

    async def action_chord(self, key: str) -> None:
        """The second key of a sequence, once ``check_action`` has allowed it."""
        pending, self._pending = self._pending, None
        if self._pending_timer is not None:
            self._pending_timer.stop()
            self._pending_timer = None
        action = CHORDS.get(pending or "", {}).get(key)
        if action:
            await self.run_action(action)

    def action_reference(self, step: int) -> None:
        """``]f`` and ``[f``: through the files the entry on screen delivered."""
        pane = self._current_pane()
        if pane is not None:
            pane.step_reference(step)

    def action_open_reference(self) -> None:
        """``gf``: vim's go-to-file, on whichever reference is selected."""
        pane = self._current_pane()
        if pane is not None:
            pane.open_reference()

    def _current_pane(self) -> ChannelPane | None:
        channel = self.current_channel()
        return self._pane_for(channel.key) if channel else None

    def action_pane(self, direction: str) -> None:
        """Move the keyboard one pane over, within the channel on screen.

        The overview is a single pane, so there is nowhere to go from it and
        nothing to do -- which is the same answer vim gives for ctrl+w l in a
        window with no split.
        """
        pane = self._current_pane()
        if pane is not None:
            pane.focus_neighbour(direction)

    def action_next_channel(self) -> None:
        self._step(1)

    def action_prev_channel(self) -> None:
        self._step(-1)

    def _step(self, delta: int) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        order = [OVERVIEW_ID] + [self._pane_ids[c.key] for c in self.channels]
        try:
            index = order.index(tabs.active)
        except ValueError:
            index = 0
        tabs.active = order[(index + delta) % len(order)]

    # -- reading ----------------------------------------------------------

    @on(ChannelPane.Read)
    def _mark_read(self, event: ChannelPane.Read) -> None:
        snapshot = self.snapshots.get(event.channel_key)
        if snapshot is None:
            return
        for item in snapshot.events:
            if str(item.path) == event.path:
                self.state.mark_read(event.channel_key, item)
                break

    @on(ChannelPane.Open)
    def _open_attachment(self, event: ChannelPane.Open) -> None:
        """Show a delivered file. Reading, so a read-only channel allows it.

        Two ways of showing one, decided by what the file is: a type this
        reader renders is read into the bottom of the pane, and everything
        else is handed to the desktop. Both wait on the channel's volume, so
        both leave the UI thread here.
        """
        if event.attachment.disposition == "read":
            self._read_attachment(event.channel_key, event.attachment, event.refresh)
        else:
            self._launch(event.attachment)

    @work(thread=True, group="open")
    def _read_attachment(self, key: str, attachment: Attachment, refresh: bool) -> None:
        """Read a text delivery off the UI thread.

        Not ``exclusive``: a poll can ask for a re-read while an earlier one is
        still waiting on the sync client, and cancelling the earlier one would
        leave whichever pane asked first showing text it has been told is
        stale. Both land; the pane ignores the one it no longer wants.
        """
        try:
            text, truncated = attach.read_text(attachment)
        except attach.CannotOpen as exc:
            self.call_from_thread(self.notify, str(exc), severity="error", timeout=20)
            return
        self.call_from_thread(self._previewed, key, attachment, text, truncated, refresh)

    def _previewed(
        self, key: str, attachment: Attachment, text: str, truncated: bool, refresh: bool
    ) -> None:
        pane = self._pane_for(key)
        if pane is None:
            return  # the tab went away while the volume was thinking
        pane.show_preview(attachment, text, truncated, focus=not refresh)

    @work(thread=True, group="open")
    def _launch(self, attachment: Attachment) -> None:
        """Hand the file to the desktop off the UI thread.

        The launcher usually returns at once, but it is pointed at a path on a
        synced volume: macOS ``open`` on a file iCloud has evicted waits for
        the download before it hands anything over.
        """
        try:
            command = attach.launch(attachment)
        except attach.CannotOpen as exc:
            self.call_from_thread(self.notify, str(exc), severity="error", timeout=20)
            return
        self.call_from_thread(self._opened, attachment, command)

    def _opened(self, attachment: Attachment, command: list[str]) -> None:
        verb = "Opening" if attachment.disposition == "view" else "Showing"
        where = "" if attachment.disposition == "view" else " in its folder"
        self.notify(f"{verb} {attachment.name}{where}")
        self.log(f"attachment: {' '.join(command)}")

    def action_mark_all_read(self) -> None:
        channel = self.current_channel()
        if channel is None:
            for other in self.channels:
                if (snapshot := self.snapshots.get(other.key)) is not None:
                    self.state.mark_all_read(other.key, snapshot)
        elif (snapshot := self.snapshots.get(channel.key)) is not None:
            self.state.mark_all_read(channel.key, snapshot)
        self.state.save()
        self._refresh_widgets(datetime.now().astimezone())

    # -- sending ----------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide the cleanup key unless this channel actually has conflicts.

        Textual reads ``False`` as "hide this binding" and ``None`` as "show it
        dimmed"; a key that is nearly always irrelevant should not sit greyed
        out in the footer forever, so this returns ``False``.
        """
        if action == "clean_conflicts":
            return bool(self._conflicts())
        if action == "chord":
            # These are priority bindings, so they are asked about before
            # anything else in the chain can answer -- including a text box,
            # where a letter has to stay a letter. Only the key that actually
            # finishes the armed sequence gets through; every other key is
            # refused here and carries on to whatever it usually does.
            key = str(parameters[0]) if parameters else ""
            return key in CHORDS.get(self._pending or "", {})
        return True

    def _conflicts(self) -> list:
        channel = self.current_channel()
        snapshot = self.snapshots.get(channel.key) if channel else None
        if channel is None or snapshot is None or channel.read_only:
            return []
        return snapshot.conflicts

    def action_clean_conflicts(self) -> None:
        channel = self.current_channel()
        conflicts = self._conflicts()
        if channel is None or not conflicts:
            return
        count = len(conflicts)
        listing = "\n".join(path.name for path in conflicts[:6])
        if count > 6:
            listing += f"\n… and {count - 6} more"
        self.push_screen(
            ConfirmScreen(
                f"Delete {count} sync-conflict {'copy' if count == 1 else 'copies'}"
                f" from {channel.name}?",
                listing,
                confirm_label="Delete",
            ),
            lambda ok: self._clean(channel, conflicts) if ok else None,
        )

    @work(thread=True, group="write")
    def _clean(self, channel: Channel, conflicts: list) -> None:
        try:
            removed, problems = remove_conflicts(channel, conflicts)
        except ReadOnlyChannel as exc:
            self.call_from_thread(self.notify, str(exc), severity="warning")
            return
        self.call_from_thread(self._cleaned, removed, problems)

    def _cleaned(self, removed: list, problems: list[str]) -> None:
        if removed:
            self.notify(f"Deleted {len(removed)} conflict {'copy' if len(removed) == 1 else 'copies'}")
        for problem in problems:
            self.notify(problem, severity="error", timeout=20)
        self.rescan()

    def action_compose(self) -> None:
        """Open the draft box beneath the timeline, leaving the reader visible."""
        channel = self.current_channel()
        if channel is None:
            self.notify("Open a channel tab first.", severity="warning")
            return
        if not self._writable(channel):
            return
        pane = self._pane_for(channel.key)
        if pane is None:
            return
        snapshot = self.snapshots.get(channel.key)
        due = "next wakeup unknown"
        if snapshot and snapshot.heartbeat:
            due = fmt.countdown(snapshot.heartbeat.next_by, datetime.now().astimezone())
        pane.open_composer(due)

    @on(ChannelPane.Send)
    def _send_draft(self, event: ChannelPane.Send) -> None:
        channel = next((c for c in self.channels if c.key == event.channel_key), None)
        pane = self._pane_for(event.channel_key)
        if channel is None or pane is None or not self._writable(channel):
            return
        # The composer closes from the worker's callback, so a failed send
        # still keeps the draft to retry.
        self._write(channel, lambda: composer.send(channel, event.text), sent=pane.close_composer)

    def action_quick(self, keyword: str) -> None:
        channel = self.current_channel()
        if channel is None:
            self.notify("Open a channel tab first.", severity="warning")
            return
        if not self._writable(channel):
            return
        action = QUICK_BY_KEYWORD[keyword]
        if action.destructive:
            self.push_screen(
                ConfirmScreen(
                    f"Send {action.keyword} to {channel.name}?",
                    action.description,
                    confirm_label=f"Send {action.keyword}",
                ),
                lambda ok: self._send_quick(channel, action) if ok else None,
            )
        else:
            self._send_quick(channel, action)

    def _send_quick(self, channel: Channel, action: QuickAction) -> None:
        self._write(channel, lambda: composer.send_quick(channel, action))

    @work(thread=True, group="write")
    def _write(self, channel: Channel, write, sent=None) -> None:
        """Perform a write off the UI thread, reporting either way.

        The target is a folder a sync client owns and ``_atomic_write`` fsyncs
        it, so a send can wait on whatever that client is doing. Not a reason
        for the rest of the app to stop redrawing. Never ``exclusive``: two
        messages sent in quick succession must both arrive.
        """
        try:
            path = write()
        except (ReadOnlyChannel, OSError, ValueError) as exc:
            self.call_from_thread(self.notify, str(exc), severity="error", timeout=20)
            return
        self.call_from_thread(self._wrote, channel, path, sent)

    def _wrote(self, channel: Channel, path, sent) -> None:
        self.notify(f"Sent to {channel.name}: {path.name}")
        if sent is not None:
            sent()
        self.rescan()

    def _writable(self, channel: Channel) -> bool:
        if channel.read_only:
            self.notify(f"{channel.name} is read-only.", severity="warning")
            return False
        return True

    # -- helpers ----------------------------------------------------------

    def current_channel(self) -> Channel | None:
        active = self.query_one("#tabs", TabbedContent).active
        for channel in self.channels:
            if self._pane_ids.get(channel.key) == active:
                return channel
        return None

    def _pane_for(self, channel_key: str) -> ChannelPane | None:
        for pane in self.query(ChannelPane):
            if pane.channel.key == channel_key:
                return pane
        return None


def _banner(message: str) -> None:
    """A macOS notification-centre banner, best effort."""
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification {message!r} with title "Inzaghi"'],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass
