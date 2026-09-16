"""Command line entry point.

The TUI is the point of Inzaghi, but a channel is a folder and the shell is often
the shortest path to it: ``inzaghi ls`` in a status bar, ``inzaghi send`` from a
script, ``inzaghi init`` when a new project starts.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from datetime import datetime
from pathlib import Path

from . import compose, fmt
from .channel import Channel
from .compose import QUICK_ACTIONS, QUICK_BY_KEYWORD, ReadOnlyChannel
from .config import Config
from .protocol import init_channel
from .skill import HARNESSES, UnknownHarness, install as install_skill, sync_reference
from .state import ReadState

HEALTH_MARK = {"fresh": "●", "late": "◍", "stale": "○", "unknown": "·"}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    config = Config.load(Path(args.config).expanduser() if args.config else None)
    return args.handler(args, config)


def _prog() -> str:
    """Whichever name the user typed -- ``inzaghi`` or the ``inz`` alias.

    So usage and error text quote a command they can actually retype.
    """
    name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return name if name and not name.startswith("__") else "inzaghi"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=_prog(), description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="path to config.toml (default: ~/.config/inzaghi/config.toml)")
    parser.set_defaults(handler=_cmd_ui)
    sub = parser.add_subparsers()

    ui = sub.add_parser("ui", help="open the terminal interface (default)")
    ui.set_defaults(handler=_cmd_ui)

    listing = sub.add_parser("ls", help="one line per channel")
    listing.set_defaults(handler=_cmd_ls)

    init = sub.add_parser("init", help="create a channel folder and write the contract into it")
    init.add_argument("path")
    init.add_argument("--name", default="", help="display name (default: folder name)")
    init.add_argument("--force", action="store_true", help="rewrite an existing README.md")
    init.set_defaults(handler=_cmd_init)

    send = sub.add_parser("send", help="write a message into a channel's inbox")
    send.add_argument("channel", help="channel name or path")
    send.add_argument("-m", "--message", help="message text (default: read stdin)")
    for action in QUICK_ACTIONS:
        send.add_argument(
            f"--{action.keyword.lower()}",
            dest="quick",
            action="store_const",
            const=action.keyword,
            help=action.description,
        )
    send.set_defaults(handler=_cmd_send, quick=None)

    skill = sub.add_parser("skill", help="install the channel protocol into an agent harness")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)

    skill_install = skill_sub.add_parser("install", help="install the skill for a harness")
    skill_install.add_argument(
        "--harness", default="claude-code", choices=sorted(HARNESSES), help="target harness"
    )
    skill_install.add_argument(
        "--project", help="install for one project instead of every session"
    )
    skill_install.add_argument(
        "--copy", action="store_true", help="copy instead of symlinking the source"
    )
    skill_install.add_argument("--force", action="store_true", help="replace an existing install")
    skill_install.set_defaults(handler=_cmd_skill_install)

    skill_sync = skill_sub.add_parser(
        "sync", help="regenerate the bundled contract from protocol.py (maintenance)"
    )
    skill_sync.set_defaults(handler=_cmd_skill_sync)

    show = sub.add_parser("status", help="print a channel's STATUS.md")
    show.add_argument("channel")
    show.set_defaults(handler=_cmd_status)

    sync = sub.add_parser(
        "sync", help="pull and push a channel over ssh, for a folder no client syncs"
    )
    sync.add_argument(
        "channel", nargs="*", help="names to sync; default is every one with a remote"
    )
    sync.add_argument(
        "--dry-run", action="store_true", help="print the rsync commands without running them"
    )
    sync.set_defaults(handler=_cmd_sync)

    return parser


def _cmd_ui(args, config: Config) -> int:
    from .ui.app import InzaghiApp  # imported late so the CLI works without a TTY stack

    InzaghiApp(config).run()
    return 0


def _cmd_ls(args, config: Config) -> int:
    channels = config.discover()
    if not channels:
        _no_channels(config)
        return 1
    now = datetime.now().astimezone()
    state = ReadState.load()
    for channel in channels:
        snap = channel.scan(now=now)
        unread = len(state.unread(channel.key, snap))
        flags = "".join(
            (
                "!" if snap.waiting else "",
                "*" if unread else "",
                "^" if snap.in_flight else "",
                "r/o" if channel.read_only else "",
            )
        )
        health = snap.health(now)
        state_line = (snap.heartbeat.state if snap.heartbeat else None) or (
            snap.status.headline if snap.status else ""
        )
        print(
            f"{HEALTH_MARK[health]} {channel.name:<18} {fmt.ago(snap.heartbeat.updated if snap.heartbeat else None, now):>9}"
            f"  {fmt.countdown(snap.heartbeat.next_by if snap.heartbeat else None, now):<16}"
            f" {flags:<5} {state_line[:60]}"
        )
    return 0


def _cmd_init(args, config: Config) -> int:
    result = init_channel(args.path, args.name, force=args.force)
    verb = "completed" if result.existed else "created"
    print(f"{verb} channel {result.channel.name} at {result.channel.root}")
    for path in result.created:
        print(f"  + {path}")
    if not any(root.path in result.channel.root.parents for root in config.roots):
        print("\nIt is outside every configured root; add it to config.toml to see it in Inzaghi:")
        print(f'\n[[channels]]\npath = "{result.channel.root}"\n')
    return 0


def _cmd_send(args, config: Config) -> int:
    channel = _resolve(args.channel, config)
    if channel is None:
        return 1
    try:
        if args.quick:
            note = args.message or ""
            path = compose.send_quick(channel, QUICK_BY_KEYWORD[args.quick], note)
        else:
            text = args.message if args.message is not None else sys.stdin.read()
            path = compose.send(channel, text)
    except ReadOnlyChannel as exc:
        print(f"{_prog()}: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"{_prog()}: {exc}", file=sys.stderr)
        return 2
    print(f"sent {path}")
    return 0


def _cmd_skill_install(args, config: Config) -> int:
    try:
        path, how = install_skill(
            args.harness,
            project=args.project,
            link=not args.copy,
            force=args.force,
        )
    except UnknownHarness as exc:
        print(f"{_prog()}: unknown harness {exc.args[0]!r}", file=sys.stderr)
        return 2
    except FileExistsError as exc:
        print(f"{_prog()}: {exc.args[0]} already exists; pass --force to replace it", file=sys.stderr)
        return 2
    scope = f"project {args.project}" if args.project else "every session"
    print(f"{how} skill into {path}\n  harness: {args.harness}  ·  scope: {scope}")
    if not args.copy:
        print("  edits to the repo take effect immediately (symlink)")
    return 0


def _cmd_skill_sync(args, config: Config) -> int:
    print("regenerated" if sync_reference() else "already up to date")
    return 0


def _cmd_status(args, config: Config) -> int:
    channel = _resolve(args.channel, config)
    if channel is None:
        return 1
    snap = channel.scan()
    if not snap.status:
        print(f"{_prog()}: {channel.name} has no STATUS.md", file=sys.stderr)
        return 1
    print(snap.status.doc.body.rstrip())
    return 0


def _cmd_sync(args, config: Config) -> int:
    """One cycle per channel, from cron or by hand.

    Reports per channel and keeps going past a failure: a host that is down
    must not stop the reachable ones from being brought up to date.
    """
    from . import transport

    watched = config.syncable()
    if args.channel:
        wanted = {token.lower() for token in args.channel}
        watched = [c for c in watched if c.name.lower() in wanted or str(c.root) in args.channel]
    if not watched:
        print(
            f"{_prog()}: no channel has a remote to sync with"
            f" — add remote = \"user@host:/path\" to a [[channels]] entry"
            + (f" in {config.source}" if config.source else ""),
            file=sys.stderr,
        )
        return 1

    failed = 0
    for name, outcome in transport.sync_all(watched, dry_run=args.dry_run).items():
        if isinstance(outcome, Exception):
            failed += 1
            print(f"{_prog()}: {name}: {outcome}", file=sys.stderr)
            continue
        if args.dry_run:
            # Quoted, so a line can be lifted out of here and run as it stands.
            print(f"{name}:")
            for command in outcome.commands:
                print(f"  {shlex.join(command)}")
            continue
        retired = f", retired {len(outcome.retired)}" if outcome.retired else ""
        print(f"{name}: synced{retired}")
    return 1 if failed else 0


def _resolve(token: str, config: Config) -> Channel | None:
    """Find a channel by name, by path, or by unambiguous name prefix."""
    path = Path(token).expanduser()
    if (path / "notifications").is_dir():
        known = {c.key: c for c in config.discover()}
        return known.get(str(path.resolve()), Channel(root=path))

    channels = config.discover()
    exact = [c for c in channels if c.name == token]
    if exact:
        return exact[0]
    matches = [c for c in channels if c.name.lower().startswith(token.lower())]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        print(f"{_prog()}: no channel matching {token!r}", file=sys.stderr)
        if channels:
            print("known: " + ", ".join(c.name for c in channels), file=sys.stderr)
        else:
            _no_channels(config)
    else:
        print(
            f"inzaghi: {token!r} matches " + ", ".join(c.name for c in matches),
            file=sys.stderr,
        )
    return None


def _no_channels(config: Config) -> None:
    where = config.source if config.loaded else "no config file"
    print(
        f"{_prog()}: no channels found ({where}).\n"
        f"Add a sync root to config.toml, or run '{_prog()} init <path>' to make one:\n\n"
        '[[roots]]\npath = "~/Dropbox"\n',
        file=sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(main())
