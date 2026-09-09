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

## Sync-conflict copies

A sync client that cannot merge an overwritten file leaves a duplicate beside
it — `STATUS (conflicted copy 2026-09-05).md`. These are never shown as events.
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
inz skill install                      # for every session you start
inz skill install --project ~/work/northwind # for one project
inz skill install --copy               # detached, for a machine without this repo
```

It symlinks by default, so editing the skill in this repo updates every harness
pointing at it. A session then picks it up by name (`/inzaghi` in Claude Code)
or from its description when a run is about to go unattended.

`skills/inzaghi/SKILL.md` is harness-neutral Markdown — only its YAML front
matter is Claude Code's format — and `reference/channel-README.md` beside it is
generated from `protocol.py`, so the contract a session reads and the contract
`inz init` writes cannot drift (a test enforces it; `inz skill sync`
regenerates). Supporting another harness is one entry in `skill.HARNESSES`.

## Status

Working: overview, per-channel tabs, timeline, reader, composer, quick actions,
search and kind filtering, live discovery, and the `inzaghi ls | init | send |
status` commands, the since-last-read divider, sync-conflict cleanup, delivered
files, and the session-side skill.
