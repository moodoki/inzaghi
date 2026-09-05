"""The Inzaghi application.

Structure: an overview tab, then one tab per channel.  A background thread
re-reads the folders on a timer -- a sync client's writes arrive without any
local filesystem event, and the volume can block, so scanning never runs on the
UI thread.  A separate one-second tick refreshes only the countdowns.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from itertools import count

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from .. import compose as composer
from ..channel import Channel, absence_is_real, remove_conflicts
from ..compose import QUICK_ACTIONS, QUICK_BY_KEYWORD, QuickAction, ReadOnlyChannel
from ..config import Config
from ..model import Snapshot
from ..state import ReadState
from .channel_view import ChannelPane
from .modals import ComposeScreen, ConfirmScreen, Draft
from .overview import OverviewPane
from .rows import HEALTH_STYLE

OVERVIEW_ID = "overview"
#: Kinds that are worth interrupting someone for the moment they appear.
LOUD_KINDS = frozenset({"hard-stop", "error"})


class InzaghiApp(App):
    CSS_PATH = "inzaghi.tcss"
    TITLE = "Inzaghi"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("o", "overview", "Overview"),
        Binding("tab", "next_channel", "Next", show=False),
        Binding("]", "next_channel", "Next channel", show=False),
        Binding("[", "prev_channel", "Prev channel", show=False),
        Binding("c", "compose", "Compose"),
        Binding("s", "quick('STATUS')", "Status"),
        Binding("p", "quick('PAUSE')", "Pause"),
        Binding("u", "quick('RESUME')", "Resume", show=False),
        Binding("x", "quick('STOP')", "Stop"),
        Binding("a", "mark_all_read", "Mark read"),
        Binding("k", "clean_conflicts", "Clean"),
        Binding("r", "refresh_all", "Refresh"),
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
        self.rescan()
        self.set_interval(self.config.poll_seconds, self.rescan)
        self.set_interval(self.config.discover_seconds, self.rediscover)
        self.set_interval(1.0, self._tick)

    def _new_pane_id(self, channel: Channel) -> str:
        pane_id = f"ch{next(self._pane_counter)}"
        self._pane_ids[channel.key] = pane_id
        return pane_id

    def on_unmount(self) -> None:
        self.state.save()

    # -- scanning ---------------------------------------------------------

    def action_refresh_all(self) -> None:
        """Re-read the config, look for new channels, and rescan every folder."""
        self.rediscover()

    @work(thread=True, exclusive=True, group="scan")
    def rescan(self) -> None:
        """Re-read every channel off the UI thread."""
        now = datetime.now().astimezone()
        scanned: dict[str, Snapshot] = {}
        for channel in self.channels:
            try:
                scanned[channel.key] = channel.scan(now=now)
            except OSError:
                continue  # a mount that went away; keep the last good snapshot
        self.call_from_thread(self._apply, scanned, now)

    @work(thread=True, exclusive=True, group="discover")
    def rediscover(self) -> None:
        """Look for channels that have appeared or gone since the last pass."""
        config = self.config.reload()
        self.call_from_thread(self._sync_channels, config, config.discover())

    async def _sync_channels(self, config: Config, found: list[Channel]) -> None:
        self.config = config
        by_key = {channel.key: channel for channel in found}
        tabs = self.query_one("#tabs", TabbedContent)

        # An absent channel means "deleted" only when its absence can be
        # believed; an unmounted volume must not take the tabs with it.
        for channel in [c for c in self.channels if c.key not in by_key]:
            if absence_is_real(channel.root):
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

        self.rescan()

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
        unread_counts: dict[str, int] = {}
        for channel in self.channels:
            snapshot = self.snapshots.get(channel.key)
            if snapshot is None:
                continue
            unread = {str(event.path) for event in self.state.unread(channel.key, snapshot)}
            unread_counts[channel.key] = len(unread)
            pane = self._pane_for(channel.key)
            if pane is not None:
                pane.update(snapshot, unread, now)
            self._badge(channel, snapshot, len(unread), now)
        self.query_one(OverviewPane).update(self.channels, self.snapshots, unread_counts, now)
        # check_action() results are cached, so the footer would keep offering
        # "k Clean" after the last conflict copy was deleted.
        self.refresh_bindings()

    def _tick(self) -> None:
        """Cheap per-second refresh: countdowns only, no disk access."""
        now = datetime.now().astimezone()
        for pane in self.query(ChannelPane):
            pane.update_strip(now)
        unread = {
            channel.key: len(self.state.unread(channel.key, snapshot))
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
        if was in {"fresh", None} and health in {"late", "stale"} and self.config.alerts.heartbeat_late:
            self._shout(f"{snapshot.name}: no heartbeat since its deadline", severity="warning")
        self._known_health[key] = health

    def _shout(self, message: str, severity: str = "information") -> None:
        self.notify(message, severity=severity, timeout=30)
        if self.config.alerts.bell:
            self.bell()
        if self.config.alerts.banner:
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

    def _clean(self, channel: Channel, conflicts: list) -> None:
        try:
            removed, problems = remove_conflicts(channel, conflicts)
        except ReadOnlyChannel as exc:
            self.notify(str(exc), severity="warning")
            return
        if removed:
            self.notify(f"Deleted {len(removed)} conflict {'copy' if len(removed) == 1 else 'copies'}")
        for problem in problems:
            self.notify(problem, severity="error", timeout=20)
        self.rescan()

    def action_compose(self) -> None:
        channel = self.current_channel()
        if channel is None:
            self.notify("Open a channel tab first.", severity="warning")
            return
        if not self._writable(channel):
            return
        snapshot = self.snapshots.get(channel.key)
        due = "next wakeup unknown"
        if snapshot and snapshot.heartbeat:
            from .. import fmt

            due = fmt.countdown(snapshot.heartbeat.next_by, datetime.now().astimezone())
        self.push_screen(ComposeScreen(channel.name, due), self._send_draft)

    def _send_draft(self, draft: Draft | None) -> None:
        channel = self.current_channel()
        if draft is None or channel is None:
            return
        self._write(channel, lambda: composer.send(channel, draft.text))

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

    def _write(self, channel: Channel, write) -> None:
        try:
            path = write()
        except (ReadOnlyChannel, OSError, ValueError) as exc:
            self.notify(str(exc), severity="error", timeout=20)
            return
        self.notify(f"Sent to {channel.name}: {path.name}")
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
