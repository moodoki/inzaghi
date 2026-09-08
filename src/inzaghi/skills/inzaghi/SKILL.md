---
name: inzaghi
description: Set up and operate an Inzaghi channel — a synced folder through which an unattended agent session reports what it is doing and receives instructions back. Use when starting a long-running or unattended run that someone will watch from another machine, when asked to "set up a channel", "report to Inzaghi", "write status to the synced folder", or when the working directory is already attached to one.
---

# Operating an Inzaghi channel

You are the **session** end of a folder-based link. A person watches the other
end in a terminal UI, from a different machine, often hours later. The folder is
synced, so either end can be offline when the other writes.

The single fact that shapes everything below: **your notifications are read out
of context, long after you wrote them.** Nobody is watching the terminal you are
running in. Every file you write has to stand on its own.

## Setting the channel up

If `inz` (or `inzaghi`) is on PATH:

```bash
inz init <channel-path> --name <project>
```

That creates `notifications/`, `notifications/attachments/`, `inbox/`,
`inbox/done/` and writes the contract into `README.md`. Otherwise create those
four directories yourself and copy `reference/channel-README.md` (beside this
file) to `<channel-path>/README.md`, replacing `<project>` with the project
name.

The channel path belongs in a synced folder, not in the repository. Ask where it
should live if it is not already obvious or specified.

## At every wakeup, in this order

1. **Read `inbox/`.** Every file there is an instruction addressed to you.
2. **Act on it**, then write an `ack` notification saying what you actually did.
3. **Move the file to `inbox/done/`**, prefixing the pickup time onto its name:
   `2026-09-05_1130_<original-name>`. Do not touch its mtime — that is the only
   record of when the message was written.
4. **Rewrite `STATUS.md`** — overwritten in place, never appended to.
5. **Rewrite `HEARTBEAT.md`** with a fresh `next update expected by`.
6. **Delete any `… (conflicted copy …)` files.** They are your own files,
   duplicated by the sync client.

Between wakeups, write an event file the moment something happens that the
watcher would want to know about — do not save it up for the next wakeup.

## The three overwritten files

**`HEARTBEAT.md`** is the one that matters most, because it is the only way a
dead session is distinguishable from a quiet one. Refresh it on a timer of its
own — every 30 minutes or less — **independently of whatever job is running**.
If it can only be refreshed by a wakeup that a stuck job prevents, it is not a
heartbeat. Always state when the next update is due.

**`STATUS.md`** is where the run is right now: what is running, which phase, an
ETA, the last commit. Keep it short and current. It must contain a
`## Waiting on you` section — `Nothing.` when nothing is blocked, and the exact
question, with the options you see, when something is. This is the field the
watcher's overview panel surfaces first.

**`TASK_OVERVIEW.md`** is progress across the whole project, one row per task,
for the person who has not looked in three days. Optional, but valuable on a
project of any size.

## Event files

Append-only, one file per event, named
`YYYY-MM-DD_HHMM_<kind>_<short-slug>.md`, opening with a `# [kind] Title`
heading:

| kind | write one when |
|------|----------------|
| `milestone` | a gate passed or failed |
| `phase-summary` | a phase closed — usually a copy of the phase summary |
| `ack` | you picked up an inbox message; say what you did |
| `hard-stop` | you cannot continue without an answer |
| `error` | something you could not recover from |

An `ack` must cite the message it answers as `# [ack] re: <inbox filename>` and
quote it back, so the two can be threaded together at the other end.

Write each one so it makes sense to someone who has not read the others.
Numbers, not adjectives: what ran, what came out, what it means, what is next.

## Delivering a file

Anything that is not Markdown — a report, a chart, a tarball of raw output —
goes in `notifications/attachments/`, and the notification that explains it
points at it:

```markdown
# [phase-summary] Bench sweep closed

p95 down 18% on the reordered index. Numbers behind that:
[raw criterion output, 12 runs](attachments/bench-2026-09-08.tar.gz), and
![the three latency charts](attachments/regression.png).
```

The link text is the description the watcher reads before deciding whether to
open the file, so make it say what the file *is* — not "attachment" or
"see here". A flat `attachments: bench.tar.gz, regression.png` front-matter key
works for a file the prose has no natural place to mention.

- **Point at everything you deliver.** A file nobody references is ignored
  entirely. That is how a payload still crossing the sync is told apart from
  one that has arrived — and how last week's leftovers stay out of the way.
- **Write the payload first**, then the notification naming it. Expect the
  watcher to receive them in the other order anyway; until the bytes land the
  attachment reads as waiting on sync, which costs nothing.
- **Names, not paths.** A reference that climbs out of the folder, or is
  absolute, or is a symlink, is refused and shown as refused.
- **Keep it small enough to finish syncing.** A 400 MB tarball still uploading
  when the run ends never arrives. Prefer a summary you wrote yourself over
  raw output the watcher would have to unpack.
- **Markdown and `.txt` are read in place**, in a pane beneath the notification
  rather than in some other application. So a report too long for a
  notification body is better delivered as `report.md` than trimmed down to
  fit; anything else opens outside the terminal, or only has its folder shown.
- Do not deliver anything as a substitute for saying what happened. The
  notification still has to stand on its own if the file never turns up.

## Hard stops

When you hit a decision that is genuinely the watcher's to make, write a
`hard-stop` and put the exact question in `STATUS.md` under
`## Waiting on you`. State the options and what you would choose. Then keep
working on anything that does not depend on the answer — a hard stop is not a
reason to stop everything.

Do not guess at a decision that changes what the run means, and do not silently
narrow the work to avoid asking.

## Instruction keywords

Recognise these on sight in an inbox file:

- `PAUSE` — finish the current step, start no new jobs
- `RESUME` — resume normal work
- `STOP` — finish the current step, write a summary, end the loop
- `STATUS` — write a fresh `STATUS.md` now

Anything else is a free-form instruction to be read and acted on.

## Writing rules

- **Write atomically**: to a temporary name in the same directory, then rename
  into place. A sync client will happily upload half a file, and a truncated
  status is worse than a stale one.
- Plain UTF-8 Markdown. ISO-8601 timestamps with an offset.
- Never edit or delete a file in `inbox/` other than by moving it to `done/`.
- Optionally open any file with a flat YAML front-matter block (`kind`, `ts`,
  `next_by`, `needs_reply`); it takes precedence over the filename and prose.

The full contract, as written into every channel, is in
`reference/channel-README.md` beside this file.
