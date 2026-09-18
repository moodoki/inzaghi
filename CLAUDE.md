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
    src/inzaghi/transport.py rsync-over-ssh cycle for a folder no client syncs
    src/inzaghi/config.py    TOML config, root scanning, discovery
    src/inzaghi/state.py     local read receipts (never written into a channel)
    src/inzaghi/compose.py   atomic writes into inbox/
    src/inzaghi/skill.py     installing the protocol into an agent harness
    src/inzaghi/skills/      the session-side skill, shipped as package data
    src/inzaghi/ui/          Textual app (composer.py is the inline draft box,
                             preview.py the reader's bottom pane for a
                             delivered text file, vim.py what a motion means
                             to the focused pane, find.py the in-document
                             search, mounting.py guards the timed refreshes,
                             volume.py the threads I/O runs on)

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
- Every write into a channel goes through `compose._atomic_write`, and its
  temporary is staged at the top of the channel rather than in the folder
  being written to. Atomic is not invisible: the file a rename comes *from* is
  a directory entry like any other, and a session woken by the create event
  lists `inbox/` at exactly that moment. The contract carries the other half
  -- read only `*.md` entries that do not begin with a dot -- because a sync
  client leaves temporaries there too and nothing on this side stops it.
- Nothing that touches a channel's volume runs on the UI thread -- scanning,
  deciding an absence, sending, deleting conflicts. That volume belongs to a
  sync client and answers when it likes; a call that waits on it stops the app
  redrawing. `tests/test_ui.py` asserts the thread, not the timing.
- Anything that re-fires on the poll needs an in-flight guard, not just the
  scan. The preview's re-read did not have one: the stamp it compares against
  is written only by a read that *landed*, so a read wedged inside a sync
  client took a new thread every two seconds until the pool was full and
  sending stopped. `app._reading` bounds it to one read per open file, and the
  poll is the retry -- dropped rather than remembered, because the comparison
  that asked will ask again. A read someone asked for by hand is never dropped.
- The scan and the discovery sweep get a pool of their own (`VOLUME_THREADS`),
  and only one of each is ever in flight. A read that has blocked inside a
  sync client cannot be cancelled, only waited for: cancelling the worker
  leaves the thread where it was. So a poll every two seconds at a volume that
  has gone quiet does not queue scans, it accumulates threads -- and on
  Textual's shared pool that ends with sending, deleting and opening dead too,
  until the app is restarted. A request that arrives while a scan is out is
  remembered, not dropped; the rescan after a send has to see the send.
- Both pools are daemon threads of our own (`ui/volume.py`), because quitting
  has to be allowed to abandon a read that has wedged. A `ThreadPoolExecutor`
  never is: `shutdown(wait=False)` declines to wait and then the interpreter
  joins every worker at exit anyway, long after the screen went back. Textual
  runs its own `@work(thread=True)` -- sending, opening, the preview's re-read
  -- on the loop's *default* executor, which `asyncio.run` joins before
  `App.run` even returns, so the app puts a pool of its own there at mount.
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
- The same holds for the delivered file open in `ui/preview.py`, which is
  compared by what a stat said about it: `Attachment.fingerprint` carries the
  ctime and inode, `_follow_preview` runs on every poll rather than only when
  the strip was rebuilt, and anything on screen for longer than
  `CACHE_SECONDS` is read again regardless. When the text that comes back
  disagrees with the stamp describing it, the text wins -- a re-read the clock
  had to ask for is precisely the one whose stat never moved.
- Two places delete: `channel.remove_conflicts` and `transport.retire`. Both
  re-validate every path at the moment of unlinking rather than trusting the
  listing they started from, and neither takes its decision from a fuzzy key --
  retirement matches a filename outright, where threading is allowed to be
  approximate. Add a third only with the same care.
- The sync cycle pulls `done/` *before* it pushes `inbox/`, and retires in
  between. Reordered, the push uploads a message the session already acted on
  and it gets acted on twice. Neither inbox leg may ever carry `--delete`.
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
- One `lstat` answers everything a reference needs: whether it is a link,
  whether it is a regular file, and how big and when. `is_symlink()` then
  `stat()` asked the volume twice about the same inode, and `_inside` walked
  both the path and the folder to the root on top -- per reference, per scan.
  Containment now comes from reasoning where it can: a name that survived
  `_lexical_refusal` is relative with no `..`, so a *flat* one cannot leave
  the folder unless its leaf is a link, which the `lstat` has just ruled out.
  A name with directories in it still gets the walk, because a symlinked
  component in the middle is the one way out that reasoning cannot close.
- The directory listing hands on the `stat` it already took. `scandir` needs
  it to decide a file is a file, and `_doc` wanted the same four numbers a
  moment later; on a File Provider mount each of those is a round trip.
  Anything that lies to a stat must now lie to the listing too -- which is
  what the tests simulating a frozen stat do.
- The `CACHE_SECONDS` expiry is for files that might still be rewritten: the
  three overwritten singletons, which are re-read unconditionally anyway, and
  any entry touched within `SETTLED_SECONDS`. The rest of the log is
  append-only by contract -- a new entry is a new path, and a new path is
  always read -- so a settled parse is kept while its fingerprint agrees.
  Re-reading all of it every minute cost a cold scan per channel per minute.
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
- And only the first `WINDOW_ROWS` of them are built. A month-old channel runs
  to thousands of entries and nobody reads to the bottom of one, but every
  rebuild, tab switch and search pays for every row that exists. What is built
  is the window stretched to hold the cursor — a key restored from outside it
  would otherwise have no option to sit on, and a cursor with nowhere to sit
  jumps to the top. The foot of the window is an option with no id, because
  everything that restores a cursor or shows a document goes looking by key;
  `G` is the one motion that names the far end out loud, so `Timeline` is the
  one widget that overrides a `ui.vim` motion, and it materialises the rest
  before it goes there. The window closes again when the *filter* changes,
  never when a message arrives: a new entry must not shut a window someone
  opened. A divider that falls past the fold moves to the fold and counts what
  is above it, which is the same sentence about the part of the channel that
  is on screen.
- The rows themselves are only *built* when building them would produce
  something different: the channel's content compared against what they were
  built from, and `fmt.next_change` for the moment a relative label is next
  due to read differently. `Row.relative` is the timestamp a label renders
  relatively, or `None` for one that carries only a clock — that one goes
  stale at midnight. Get a bucket in `next_change` wrong and a row freezes,
  so it mirrors `ago` and `duration` exactly and a test walks every age.
  What a skipped poll must still do is refresh the *reader*: a rewritten
  status file, an attachment that has landed, and the open file's own re-read
  timer all change what the selected entry holds without changing a row.
- Derived work that only the app can invalidate is computed once and kept:
  the unread sets (receipts are written nowhere else, so seven call sites
  forget them) and the attachment names a parse points at (held by the `Doc`
  and compared by identity, so a re-read never answers with the old text's
  names). What is never kept is anything the *volume* can change underneath
  us — whether a referenced file is on disk is asked again every scan.
- The reader is refreshed on every poll regardless, because the selected
  entry's *contents* can change while its row does not: a rewritten status
  file, or an attachment that has finished syncing. `_show` compares before it
  writes, so a poll that found nothing new costs nothing.
- The skill's `reference/channel-README.md` is generated from
  `protocol.CHANNEL_README`; a test guards the drift, `inz skill sync` fixes it.
- Nothing that goes to GitHub names a channel, a project or a person. Commits,
  pull requests, issues and anything under `docs/` are public; the channels are
  someone's work, their names say what that work is, and at least one of them
  will be renamed before it is public. Channels are named by **codename** --
  NATO words, assigned in creation order -- and the mapping to real names and
  paths lives in `CLAUDE.local.md`, which `.gitignore` holds. Deliberately
  meaningless words: a codename that hints at the subject is a codename that
  leaks it. The same rule covers host names, dataset and experiment names, and
  the usernames of people sharing a machine. Evidence from a live channel is
  welcome; the identity of the channel is not the evidence, and a codename
  keeps a finding traceable without publishing what it was traced from.
- `uv run --with pytest pytest -q` to run the suite. CI runs the same one on
  the floor `pyproject.toml` promises and on the version development happens
  on, plus macOS -- which ships openrsync rather than rsync, and is the side
  `transport` and `attach` branch for.
