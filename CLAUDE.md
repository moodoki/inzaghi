# Inzaghi — working notes

A Textual TUI over folder-based channels shared with unattended agent sessions.
`README.md` states the channel contract; treat it as the spec.

## Never write to a live channel while developing

A channel belonging to a running session is off limits: no test messages, no
scratch files, no cleanup. Read one if you need realistic data, but develop
against a private copy.

Copy with `cp -Rp`, never plain `cp -R`: `cp -R` rewrites mtimes, and a sent
message's mtime is how Inzaghi dates it once the session has moved it to
`inbox/done/`.

Keep two sandbox channels — a copy of a real one, and an empty one for the
alert states (stale heartbeat, hard-stop, waiting-on-you).

Channels can also be marked `read_only` in config, which makes the composer and
the conflict cleanup refuse them while the viewer keeps working.

Machine-specific paths — which channel is live here, where the sandboxes are —
belong in `CLAUDE.local.md`, which is not committed.

## Layout

    src/inzaghi/parse.py     pure parsers over filenames and Markdown prose
    src/inzaghi/model.py     Doc, Event, Heartbeat, Status, Thread, Snapshot
    src/inzaghi/channel.py   folder -> Snapshot, with a content cache
    src/inzaghi/config.py    TOML config, root scanning, discovery
    src/inzaghi/state.py     local read receipts (never written into a channel)
    src/inzaghi/compose.py   atomic writes into inbox/
    src/inzaghi/skill.py     installing the protocol into an agent harness
    src/inzaghi/skills/      the session-side skill, shipped as package data
    src/inzaghi/ui/          Textual app

Installed as two console scripts, `inzaghi` and the `inz` alias, both pointing
at `cli:main`; `cli._prog()` reports whichever name was typed.

## Conventions

- Parsers degrade to `None`; a session that drifts from the format makes one
  widget go quiet rather than crashing the app.
- Every write into a channel goes through `compose._atomic_write`.
- `channel.remove_conflicts` is the only code that deletes anything; it
  re-validates each path rather than trusting the snapshot it was given.
- `check_action` returning `False` hides a binding; `None` only dims it.
- The timeline rebuilds only when the *rows* change, never when their labels
  do — labels carry relative times and churn every poll. Restore the cursor by
  option id, not index: the list also holds separators and the divider.
- The skill's `reference/channel-README.md` is generated from
  `protocol.CHANNEL_README`; a test guards the drift, `inz skill sync` fixes it.
- `uv run --with pytest pytest -q` to run the suite.
