# Inzaghi

```
   .-.
  ( o )>
   \   `--._
    \       `--.___
     \              `--.
      \      .-"-.      `.
       \    /     \       \
        \  |       |       |
         \  \     /       /
          `. `---'      ,'
            `-.______.-'
               ||  ||
              _||__||_
   ~~~~~~~~~~~~~~~~~~~~~~~~
```

[![tests](https://github.com/moodoki/inzaghi/actions/workflows/tests.yml/badge.svg)](https://github.com/moodoki/inzaghi/actions/workflows/tests.yml)

A terminal interface to folder-based channels with unattended agent sessions.

A long-running agent session on another machine writes what it is doing into a
synced folder and reads instructions back out of one. Inzaghi is the other end of
that link: an overview of every channel at once, tabs for the ones you are
following, and a composer for sending the session its next instruction.

A session's heartbeat has to cross a sync client to reach you, so a deadline
that has only just passed usually means the update is in flight. Inzaghi allows
`heartbeat_grace_seconds` (60 by default) before calling a channel late. The
countdown always shows the real time; only the verdict — and the alert — waits.

It is not a chat client. Sessions wake on their own schedule — half an hour is
typical — so the questions Inzaghi is built to answer are *is it alive*, *when will
it speak next*, *does it need me*, and *has it seen what I sent*.

## Installing

Python 3.11 or newer. The only runtime dependency is Textual.

```sh
uv tool install git+https://github.com/moodoki/inzaghi   # from anywhere
uv tool install --editable .                             # from a clone, tracking it
```

Either puts `inz` and `inzaghi` on PATH — the same entry point under two
names, and the short one is used throughout this file. `--editable` is worth
having if you also install the session-side skill, which symlinks into the
harness by default: the client and the contract it teaches then follow the
same checkout, and `inz skill sync` keeps them in step.

Inside a clone, `uv run inz` does everything without installing anything.

## Where things live

```
~/.config/inzaghi/config.toml    what to watch
~/.local/state/inzaghi/          read receipts, and sync markers by convention
```

`XDG_CONFIG_HOME` and `XDG_STATE_HOME` move them, `INZAGHI_CONFIG` and
`INZAGHI_STATE_DIR` override them outright, and `inz --config <path>` changes
it for one run.

There is no wizard: the config is a file you write. A missing one is not an
error — Inzaghi remembers where it would be and picks it up on the next
discovery pass, without a restart — and neither is one being edited, since a
config that will not parse leaves the running one alone rather than dropping
every channel. The smallest one that does something:

```toml
[[channels]]
path = "~/sync/channels/northwind"
```

Then `inz`, which opens the interface; `inz ls` prints a line per channel
instead. For a channel that does not exist yet,
`inz init ~/sync/channels/northwind --name northwind` creates the folder and
writes the contract into it.

Nothing Inzaghi believes is ever written into a channel. Read receipts — which
entries you have seen — live in the state directory above, because a channel is
the session's to write and a file this end put there is a file the session has
to be told to ignore.

## The channel contract

One folder per project, shared with one session:

```
<channel>/
  README.md                     the contract, for the session to follow
  notifications/                session -> you
    STATUS.md                   overwritten each wakeup: what is running
    HEARTBEAT.md                overwritten: last update, next update due
    TASK_OVERVIEW.md            overwritten: progress across the project
    2026-09-04_2325_milestone_<slug>.md
                                append-only log; kinds are milestone,
                                phase-summary, ack, hard-stop, error
    attachments/                files a notification delivers, named by the
                                notification that explains them
  inbox/                        you -> session
    2026-09-05_1130_pause-gpu-jobs.md
    done/                       the session moves messages here once read,
                                prefixed with the time it picked them up
```

The command is `inzaghi`, or `inz` for short — the same entry point under both
names, and usage and error text quote whichever one you typed.

`inzaghi init <path>` writes this structure and a README stating the contract, so a
session can be pointed at the folder and told to follow it.

Files stay plain Markdown. Inzaghi reads structure out of filenames and prose, and
accepts an optional YAML front-matter block (`kind`, `ts`, `next_by`,
`needs_reply`) when a session cares to emit one — a channel that never does
works exactly as well.

## Delivered files

A session with something that does not fit in a notification — a report, a
chart, a tarball of raw output — writes it into `notifications/attachments/`
and points at it from the notification that explains it, as an ordinary
Markdown link. The link text becomes the description. Selecting that entry
lists what it delivered beneath the reader; `v` moves the cursor into the list
and `enter` shows the file. From the reader, `]f` and `[f` step through the
references without leaving it — scrolling the prose to where each was written
— and `gf` opens the one you are on.

Nothing in that folder is read on its own. An attachment exists because a
notification names it, which is what makes the two interesting states
distinguishable: the payload and the prose cross the sync separately, usually
in that order but not reliably, so a referenced file that is not there yet
reads as *waiting on sync* rather than missing. It fills in a size when it
lands, without disturbing whatever you were reading.

What this reader can render, it renders. A delivered `.md` or `.txt` opens in
a pane along the bottom of the reader, under the line that named it, and takes
three quarters of it: asking for a file is asking to read it, and the
notification keeps the quarter above. Drag the divider, or key it with `+` and
`-`, to put the split somewhere else; `esc` closes the file. Nothing leaves
the terminal for a format the terminal is made of. It is read afresh whenever
a scan says the file changed, so a log a session is still writing stays
current under you, and only the first 256 kB is shown — the file at the end of
a reference was written unattended and is whatever it turned out to be.

Everything else means launching something, and a channel is written by an
unattended session and relayed by a sync client. So that rule is narrow: a
known viewable type — `.pdf`, images, `.csv`, `.log` — opens in the system
viewer, and everything else, archives included, only ever gets its containing
folder opened (`open -R` on macOS, `xdg-open` on the folder on Linux, which
has no equivalent). Nothing from a channel is executed, and a reference that
is absolute, climbs out of the folder, or is a symlink is refused and shown as
refused rather than followed — checked again when the file is read, not just
when it was scanned. Every link is decided here too: the reader's Markdown
widgets are built with `open_links=False`, or clicking one would hand its href
to the browser before any of this had a say. Opening is reading, so a
`read_only` channel allows it.

Conflict copies inside `attachments/` are left alone: cleanup deletes text
files it can recognise, and nothing points at a conflicted duplicate anyway.

## Discovery

Roots are optional. A config can list channels and nothing else:

```toml
[[channels]]
path = "~/sync/channels/northwind"

[[channels]]
path = "/Volumes/shared/channels/southwind"
read_only = true
```

A root is a folder that holds channels, scanned rather than listed:

```toml
[[roots]]
path = "~/sync/channels"
depth = 1                  # how far below the root a channel may sit
```

Roots exist so that a channel created later needs no edit here. Channels are
found by scanning the configured roots, and rediscovered every
`discover_seconds` (30 by default), so a folder created while Inzaghi is running
gains a tab without a restart — `r` forces the pass immediately. Keep roots
small and shallow: `depth = 1` against a parent that holds only channels costs
one directory listing.

A channel that disappears loses its tab, but only when its absence can be
believed: its parent directory has to be readable, and its own directory gone.
An unmounted volume looks exactly like a deletion, and a folder that still
exists but has lost its `notifications/` is read as mid-sync rather than
removed.

## Moving a channel yourself

A channel is a folder with a contract, and nothing in the contract says how
the folder gets from one machine to the other. Three ways work, and none of
them is a fallback for the others:

1. **A filesystem both ends can reach** — the session writes, this client
   reads, and there is no transport to go wrong.
2. **A sync client** — Dropbox, iCloud, Syncthing. Store-and-forward, so
   neither end has to be awake when the other writes, which is what makes it
   the convenient default.
3. **rsync over ssh**, below, for when you would rather not put the channel
   through a third party, or there is no sync client on the box.

### Setting up rsync over ssh

Three things have to be true before the config below means anything: `rsync`
exists on both machines (macOS ships openrsync, which is fine); `ssh <host>`
reaches the session's machine without asking you anything, since this will run
from cron; and the channel already exists over there. That end is the
original. The one here is a mirror, and `inz sync` creates it on its first
run.

Then one entry per channel:

```toml
[[channels]]
path = "~/channels/northwind"          # the mirror on this machine
name = "northwind"
remote = "worker:/srv/channels/northwind"
sync_marker = "~/.local/state/inzaghi/northwind.synced"
sync_interval_seconds = 300            # how often you intend to run it
```

`remote` is an ordinary rsync target, so `worker` there is whatever name your
`~/.ssh/config` knows. `sync_marker` is a file each completed cycle stamps,
which is what lets a silent channel be blamed on the link rather than the
session; *When the link is the problem, not the session*, below, is about what
that buys and where the file may not live. `inz sync` writes it, creating its
parent directory if it has to, so nothing needs to exist there beforehand.

```sh
inz sync                # every channel with a remote
inz sync northwind      # or one
inz sync --dry-run      # print the rsync commands, quoted and runnable
```

The first run creates the mirror, so the folder does not have to exist yet.
From cron, on the machine you watch from:

```
*/2 * * * * $HOME/.local/bin/inz sync >>$HOME/.cache/inz-sync.log 2>&1
```

Spelled out, because cron's `PATH` is not yours. `uv tool dir --bin` says where
`uv tool install` put the entry points — `~/.local/bin` by default.

### Which end drives

This drives from **the watching end**, pulling and pushing over ssh, which is
the topology worth having when the session box is always on and the laptop is
not. It needs no reverse channel, the intermittent end initiates so nothing
ever dials a sleeping host, and the ssh key points from your laptop into the
dev box rather than handing an unattended agent a foothold on your laptop.
Confinement therefore belongs on the session box, in `authorized_keys`:

```
restrict,command="rrsync /srv/channels/northwind" ssh-ed25519 AAAA…
```

The cost is send latency: `ctrl+s` writes into the mirror, and the message
leaves on the next cycle rather than at once.

### The cycle, and why it is in that order

    1. pull  notifications/   the session's, ours to overwrite wholesale
    2. pull  inbox/done/       its receipts for what it has picked up
    3. retire the local copy of every message that has turned up in done/
    4. push  inbox/            what is left: messages not yet picked up

The two ends never write the same thing — `notifications/` and `inbox/done/`
are the session's, `inbox/` is written here and consumed there — so this is
three one-way copies rather than a merge, with no conflict rules to get wrong.
Step 3 is the one that decides something, and its position is load-bearing:
push before retiring and you upload a message the session has already acted
on, and it acts on it again.

Neither inbox leg carries `--delete`, in either direction. Downward it would
resurrect what the session just consumed; upward it would delete an
instruction written thirty seconds ago and not yet read. Retirement is by
exact filename instead — `X.md` goes only when `done/` holds a file called
`X.md` with one pickup stamp in front of it — and every path is re-checked at
the moment of unlinking, the same discipline the conflict cleanup follows.

`-a` on every leg, because a message's send time *is* its mtime. No
`--partial`, because rsync's default is to write a dot-prefixed temporary and
rename on completion, which is exactly why a half-transferred file is
invisible to the scanner and an attachment reads as *waiting on sync* until
all of it is here. Cycles lock against each other with `fcntl` — a 600 MB
attachment outlasts a two-minute cron — and a channel marked `read_only`
refuses to sync at all, since pulling writes into the folder.

## When nobody is reading the inbox

A message only works if something reads it, and on this side that is out of
your hands: the session decides when it looks. Every mechanism a session can
build to notice one — a watcher, a poll, a tick — is *started by the session*,
which means none of them can recover once a turn ends with nothing armed. Over
one night across seven channels that failed three different ways: a watcher
that exited on a quiet timeout meaning to be re-armed and never was; one that
survived but lost its event in the gap between two arms and sat alive and deaf;
and one whose fallback listing was hung off the watcher's own notification, so
when the watcher died the safety net could never fire either.

They share a shape. The thing that would notice is downstream of the thing that
broke. So the second layer belongs outside the session entirely:

```sh
inz supervise                  # resident, every 30s
inz supervise --once           # one pass, for cron
inz supervise --dry-run        # say what would be poked, poke nothing
```

It lists each configured inbox, and when one has held a message longer than
`nudge_after_seconds` it pokes the session that owns it. It also pokes a session that has **stopped at a usage limit** — one whose
heartbeat is past the `next update expected by` it published, *and* whose pane
says why — whether or not anything is waiting: a limit kills nothing — the process, the watchers and the folder are
all fine — it just means nothing will happen in that session again, and
nothing tells it when the limit has reset. The poke is what restarts it. Sent
before the reset it costs one turn that ends the way the last one did; sent
after, the session picks up where it stopped. A stall is poked **once**, on the pass it appears, and not again while it
lasts: a session that is coming back answers the first message, and one that is
genuinely wedged will not answer the twentieth either — but twenty pokes cost
twenty wake-ups spent reading the same sentence. The stall has to clear and
return before another is sent. Unread mail is a separate question and is still
poked on its own interval while a stall is held.

```toml
[[channels]]
path = "~/sync/channels/northwind"
name = "northwind"
tmux = "work:1.0"                  # the pane that session runs in
```

`tmux` is anything `tmux send-keys -t` accepts. For a session that is not in a
pane, `nudge` is an argv list run instead — no shell — with `{name}`, `{path}`,
`{count}` and `{message}` substituted:

```toml
nudge = ["/usr/local/bin/wake-session", "--channel", "{name}", "--say", "{message}"]
```

A channel with neither is reported as **unsupervised** rather than passed over,
because "nothing to report" and "no way to report it" must not look the same.

Two numbers shape it. `nudge_after_seconds` (180) is how long a message may
sit before this steps in — long enough that a session mid-turn reaches its own
inbox first, since poking one that was about to look anyway is just noise.
`nudge_every_seconds` (900) is how long before the same channel is poked again:
a session that is busy, wedged or waiting on a human does not become less so
for being told twice, and a supervisor that repeats every pass is one you turn
off.

**Nothing it wrote is evidence.** A poke lands in the pane it was typed into
and stays there, so a pattern matched against the whole pane matches the
supervisor's own message on the next pass — a supervisor detecting itself, once
per interval, for as long as the scrollback holds. That is not hypothetical: it
sent 105 stall pokes across five channels that way, four of which had never
stalled at all. Three things stop it now:

- the message says nothing a stall is detected by, so it cannot report itself;
- what it did say is removed from the capture before anything is matched, found
  character by character because a terminal wraps mid-word as readily as
  between two;
- and a stall is poked on the edge — once, when it appears — so even a false
  one costs a single message.

**And the pane is only ever the reason, never the test.** That is
`next update expected by`, which every heartbeat publishes for exactly this
question. A session declares its own cadence — thirty minutes while it idles
holding a decision, five mid-build, a hundred and fifty through a long
rehearsal — and no threshold here has to serve all three. Judging silence off
a terminal instead was wrong three times in one evening, against sessions that
were minutes inside a window they had published; a detector that cries wolf is
worse than none, because the next real outage reads like the last false one.
A channel that promises nothing is never stalled: absence of a promise is not
a broken one.

**It never answers a question.** A poke is keystrokes, and the last line of a
pane decides what they mean — typing into a session that is asking its user
something puts the text where the answer goes, and the Enter behind it submits
one. So the pane is read first, and one that looks like it is asking anything
is skipped and said to be skipped. A pane that cannot be read at all is skipped
too: if we cannot see what we are typing into, we do not type. Neither case is
recorded as a poke, so the next pass tries again once the question has been
answered by the person it was put to. A limit notice does not license an
Enter either: a session can be stalled *and* holding a question, and the
refusal wins while the stall is still what gets reported.

**What it does not cover.** A watcher inside a session dies when that session
exits, because it is a child of it — so the supervisor is the layer that
survives, and it should be started by something that outlives the sessions
too: a systemd user service, or a shell detached with `setsid`. A usage limit
does not kill a watcher, only the session's ability to act on what it sees.

And it never writes into a channel. A supervisor that did would be one more
thing racing the session it is supervising; the record of what it poked and
when lives in the state directory with the read receipts.

## When the link is the problem, not the session

A silent folder means a dead session only if the folder is still arriving.
When the session writes straight into a filesystem this client can read, that
is a given. On a sync client it is usually safe to assume. On rsync over ssh
it is not — an unreachable host, or a laptop that slept through the last ten
cron ticks, looks exactly like a session that died, because in both cases
nothing new turns up.

The marker below is worth setting for the third of those and pointless for the
first: with no transport in the way, there is nothing that could stop
arriving.

Point a channel at a marker file and Inzaghi can tell them apart — the two
keys in the rsync config above, which are worth setting even when something
other than `inz sync` is moving the folder:

```toml
[[channels]]
path = "~/channels/northwind"
sync_marker = "~/.local/state/inzaghi/northwind.synced"
sync_interval_seconds = 300
```

Whatever moves the folder writes that file on success — `inz sync` does, and
so can a script of your own, with `date -Iseconds > "$marker"`. It records the
last time this end heard anything at all, which is the one fact the channel
cannot report about itself.

Keep it **outside** the channel. `notifications/` belongs to the session and
the pull owns it with `--delete`, so a marker there is deleted on every cycle
and rewritten at the end of the ones that get far enough — which reads as
fresh forever and can never report a problem. `inz sync` refuses such a path
rather than letting it look like it works.

The time is read out of the file's contents, with its mtime as a fallback, and
the file is *opened* rather than stat-ed. That is deliberate: a marker is
overwritten in place, and on a File Provider mount — iCloud, Dropbox,
Nextcloud on macOS — a stat describes the placeholder the provider last wrote
down rather than the file. It is the same trap `channel._doc` avoids by
reading every singleton on every scan. A marker left by a plain `touch` still
works, on its mtime.

The marker is judged against `sync_interval_seconds` the same way a heartbeat
is judged against its own promise, with the same grace. What it buys:

- A **late heartbeat over a healthy link** still reads as `late` or `stale`.
  The folder is arriving, so the silence is the session's, and the alert says
  so.
- A **late heartbeat with an overdue link** reads as `⇅ offline` instead. The
  staleness is unexplained rather than damning, the strip says `no sync for
  22m`, and the alert names the sync rather than sending you to look at a
  session that may be fine.
- A **fresh heartbeat with an overdue link** is left alone. It was true when
  it was written, and a link that broke a minute ago has not made it false.

Watching nothing changes nothing: without `sync_marker` every verdict is
exactly what it was, and a marker that has never been touched — a sync not
wired up yet — is treated as no promise rather than a broken one.

## Finding things in a long log

`/` asks one of two questions, depending on where the keyboard is: *which
entries mention this*, from the timeline, or *where in this one does it say
that*, from the reader or an opened file. A phase summary of eighty lines
mostly wants the second.

From the timeline it searches the channel — titles and whole document bodies,
case-insensitively — narrowing as you type. Enter hides the box and keeps the
search; the query stays visible in the filter bar and `/` reopens it with the
text still there.

From the reader it searches the document, in a one-row box at the foot of the
column, with the match count beside it. `n` and `N` step through the matches
and go on working after the box has closed, the way `hlsearch` does; `esc`
clears the search before it clears anything else. A rendered document is not
text on screen but a column of blocks, so a match is shown by scrolling to the
block that holds it and tinting that — the innermost one, so a list item
lights up rather than the whole list. A delivered `.txt` is laid out by
Inzaghi itself, so there every match is underlined and the current one
reversed. The query survives moving to another entry, as vim's does across
buffers, but nothing jumps until `n` asks it to.

`f` cycles the kind filter (`F` goes back), and the two compose: `milestone` +
"gate" asks for milestones mentioning gate. The bar doubles as a histogram of
what the search found, so the counts tell you where the matches are before you
pick a kind. `esc` clears both.

A `──── 3 new above ────` line marks what has arrived since you last read the
channel, and retires itself once you have. It sits below the *last* unread
entry, not the first read one, so a rewritten old file — which becomes unread
again in its own chronological place — cannot leave anything new underneath it.
Your own messages and the pinned panels are never unread, so they never make a
boundary on their own.

## Moving about

The arrows and `tab` work, and so do vim's keys, on the grounds that this is a
window somebody leaves open beside their editor all day:

| key | where it goes |
|---|---|
| `j` `k` | down and up, in whichever pane holds the keyboard: a list moves its cursor, a document scrolls |
| `gg` `G` | the top and the bottom of it |
| `ctrl+d` `ctrl+u` | half a screen either way |
| `h` `l` | the previous and next channel — the only horizontal axis here — same as `←` `→` and `tab` |
| `ctrl+w` `h` `j` `k` `l` | the pane to the left, below, above, to the right |
| `/` `n` `N` | search, and step the matches — the channel or the document, see below |
| `]f` `[f` `gf` | the next and previous file this entry delivered, and open it |
| `i` | write a message, as in insert |
| `;` | the command palette |

`ctrl+w` is vim's own window prefix rather than the bare `ctrl+h/j/k/l` a
vim-tmux-navigator setup uses, for two reasons: tmux binds those four at its
root table and forwards them only to a pane running vim, and `ctrl+h` and
`ctrl+j` are the same bytes as Backspace and Enter unless the terminal is
speaking the kitty keyboard protocol. The prefix has neither problem.

In a draft or the search box the letters are letters, and `ctrl+w` deletes a
word — the way insert mode behaves in vim. `esc` is how you leave.

## Writing a message

`c` opens a draft box in the bottom half of the timeline column, not over the
screen — writing to a session is mostly an act of reading, and quoting a number
out of one notification while checking what another said is impossible from
behind a modal.

`esc` hands the keyboard back to the list, leaving the draft standing, so you
can move through entries and read them; `c` returns to it with the text intact.
`ctrl+s` sends, `ctrl+e` opens `$EDITOR`, `ctrl+g` discards. A send that fails
keeps the draft to retry.

A message is staged at the top of the channel and renamed into `inbox/`, so
the folder a session watches never holds a half-written instruction — atomic
is not the same as invisible, and the instant a temporary appears is the
instant a session woken by the create event looks. The contract tells the
session the other half of that rule: read only `*.md` entries whose names do
not begin with a dot, because a sync client stages its own downloads there and
no care on this side prevents it.

## Sync-conflict copies

A sync client that cannot merge an overwritten file leaves a duplicate beside
it — `STATUS (conflicted copy 2026-09-05).md`. These are never shown as events.
They are specific to that kind of transport: a channel moved by rsync over ssh
has none, because rsync overwrites rather than duplicating, and the strip and
the `K` key simply stay quiet.
When a channel has some, the strip says so and `K` offers to delete them, after
a confirmation listing exactly what will go. The key is hidden otherwise, and on
a `read_only` channel: read-only means untouched, not merely unwritten-to.

It is the only deletion Inzaghi performs, so each path is re-checked against the
conflict pattern *and* the channel's own directories at the moment of unlinking,
rather than trusting a snapshot that may be seconds stale.

## Teaching a session the protocol

The other end of the link is an agent session that has to know how to operate
the channel. That knowledge ships as a skill:

```sh
inz skill install                      # for every session you start (default: claude-code)
inz skill install --harness antigravity # for Antigravity sessions
inz skill install --project ~/work/northwind # for one project
inz skill install --copy               # detached, for a machine without this repo
```

It symlinks by default, so editing the skill in this repo updates every harness
pointing at it. A session then picks it up by name (`/inzaghi` in Claude Code or Antigravity)
or from its description when a run is about to go unattended.

`skills/inzaghi/SKILL.md` is harness-neutral Markdown — its standard YAML front
matter is supported by both Claude Code and Antigravity — and `reference/channel-README.md` beside it is
generated from `protocol.py`, so the contract a session reads and the contract
`inz init` writes cannot drift (a test enforces it; `inz skill sync`
regenerates). Supporting another harness is one entry in `skill.HARNESSES`.

## Status

Working: overview, per-channel tabs, timeline, reader, composer, quick actions,
search and kind filtering, live discovery, and the `inzaghi ls | init | send |
status | sync | supervise` commands, the since-last-read divider,
sync-conflict cleanup, delivered files, ssh transport with a link-health
marker, the inbox supervisor, and the session-side skill.
