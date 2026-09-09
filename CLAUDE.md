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
    src/inzaghi/attach.py    files a notification delivers: what one is,
                             reading a text one, launching the rest
    src/inzaghi/config.py    TOML config, root scanning, discovery
    src/inzaghi/state.py     local read receipts (never written into a channel)
    src/inzaghi/compose.py   atomic writes into inbox/
    src/inzaghi/skill.py     installing the protocol into an agent harness
    src/inzaghi/skills/      the session-side skill, shipped as package data
    src/inzaghi/ui/          Textual app (composer.py is the inline draft box,
                             preview.py the reader's bottom pane for a
                             delivered text file, vim.py what a motion means
                             to the focused pane, find.py the in-document
                             search, mounting.py guards the timed refreshes)

Installed as two console scripts, `inzaghi` and the `inz` alias, both pointing
at `cli:main`; `cli._prog()` reports whichever name was typed.

## Conventions

- Parsers degrade to `None`; a session that drifts from the format makes one
  widget go quiet rather than crashing the app.
- Every front-matter key the contract advertises has to be *read* somewhere.
  `ts` on `STATUS.md` and `HEARTBEAT.md` is the time of that update, not only
  an event's timestamp: those files are rewritten whole, so it is the same
  fact. A documented key the parser ignores is worse than one nobody
  documented -- the session did as it was told and still went unread.
- Every write into a channel goes through `compose._atomic_write`.
- Nothing that touches a channel's volume runs on the UI thread -- scanning,
  deciding an absence, sending, deleting conflicts. That volume belongs to a
  sync client and answers when it likes; a call that waits on it stops the app
  redrawing. `tests/test_ui.py` asserts the thread, not the timing.
- A file that is overwritten in place is never trusted to a `stat`. On a File
  Provider mount -- iCloud, Dropbox, Nextcloud on macOS -- `stat` describes the
  placeholder, not the file: the contents are on a server until something opens
  them, and a provider nobody has asked keeps answering about the ones it last
  wrote down. A cache that believes it stops opening the file, and a file
  nobody opens is never fetched, so the heartbeat freezes until the process
  restarts. `channel._doc` therefore reads every singleton on every scan,
  fingerprints the rest by ctime and inode as well as mtime and size (a
  heartbeat rewritten with a new timestamp is the same length as the last one),
  and trusts nothing for longer than `CACHE_SECONDS`.
- `channel.remove_conflicts` is the only code that deletes anything; it
  re-validates each path rather than trusting the snapshot it was given.
- Read receipts are forgotten a whole channel at a time, and only on evidence
  from outside the channel: the config no longer watching it, or an absence
  `absence_is_real` will vouch for. Never per file -- a notification missing
  from one scan of a synced folder is late at least as often as it is gone.
- `check_action` returning `False` hides a binding; `None` only dims it. It is
  also consulted on every dispatch, before the action runs, which is what
  makes the two-key sequences work: `ctrl+w` and `g` arm a prefix, and the
  keys that complete one are `priority=True` bindings -- checked ahead of the
  whole focus chain -- that `check_action` refuses unless that exact prefix is
  armed. Refused, they fall through to their own meanings, so `h` still
  changes channel and `j` is still a letter in a draft. Anything else about
  the ordering and both halves collapse: see `ui/vim.py` and the app's
  `action_chord`.
- A timed refresh -- the poll, the one-second tick -- can land before the
  widgets it writes into exist, or after they have gone: a dozen channels take
  longer than a second to mount, and removing a tab frees a pane's children
  before the pane. Guard every one with `ui.mounting.composed`, in the app as
  well as in each pane; do not query and hope.
- An attachment exists only because a notification references it; a bare file
  in `notifications/attachments/` is invisible on purpose. Resolution happens
  per scan, never in the `Doc` cache: the prose does not change when the
  payload lands. A referenced file that is absent is `syncing`, never missing.
- `attach.READABLE` types (`.md`, `.txt`) are rendered in `ui/preview.py`, the
  resizable pane along the bottom of the reader, which an opened file gets
  three quarters of; only `attach.VIEWABLE` types are handed to the system
  viewer, and everything else gets its folder revealed. Both are whitelists so
  that a type nobody considered lands on the safe side -- a channel is written
  by an unattended session. `read_text` re-validates the path it was given for
  the same reason `remove_conflicts` does: the snapshot is a poll old.
- Every `Markdown` widget is constructed `open_links=False`. Left on, the
  widget answers a clicked link itself by calling `app.open_url` -- the
  browser, or `xdg-open` -- and it does that before the click bubbles this
  far, so the whitelist above never gets asked. A test posts `LinkClicked` at
  the widget, not at the pane, because posting it at the pane skips the
  handler that used to be wrong.
- Two searches share `/`, and which one runs is decided by focus alone:
  `ChannelPane._reading()` names the two panes that hold a document, and
  everywhere else `/` is the channel filter it always was. They keep separate
  state -- `self.filter` against the rows, `self._find` against the text -- so
  `esc` can undo the nearer one first.
- A match in the reader is shown by tinting the `MarkdownBlock` that holds it,
  because a rendered document is a column of widgets and not text on screen:
  `find.block_for` picks the innermost block whose `source_range` covers the
  line. A delivered `.txt` is laid out here, so that one is highlighted to the
  character with `Content` spans. Matching runs on the *source* and offsets
  come from a regex over the original string -- casefolding is not
  length-preserving, and a shifted offset lights the wrong words.
- The timeline rebuilds only when the *rows* change, never when their labels
  do — labels carry relative times and churn every poll. Restore the cursor by
  option id, not index: the list also holds separators and the divider.
- The reader is refreshed on every poll regardless, because the selected
  entry's *contents* can change while its row does not: a rewritten status
  file, or an attachment that has finished syncing. `_show` compares before it
  writes, so a poll that found nothing new costs nothing.
- The skill's `reference/channel-README.md` is generated from
  `protocol.CHANNEL_README`; a test guards the drift, `inz skill sync` fixes it.
- `uv run --with pytest pytest -q` to run the suite.
