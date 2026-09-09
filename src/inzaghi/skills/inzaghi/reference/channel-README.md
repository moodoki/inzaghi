# <project> — unattended-run channel

This folder is the link between the session running the **<project>** project and
whoever is watching it from elsewhere. It is synced, so either end can be
offline when the other writes. Protocol v2 (Inzaghi).

Everything is plain UTF-8 Markdown. Timestamps are ISO-8601 with an offset.
Write every file atomically — to a temporary name in the same directory, then
rename into place — so the sync client never uploads half a file.

## notifications/ (session → watcher)

Everything the session writes lives in this folder — the three files below and
every event file. `README.md`, at the top of the channel, is the only exception,
and it is written once. A status or heartbeat put anywhere else is not read at
all: the watcher looks here and nowhere else.

Three files are **overwritten** at every wakeup:

* `STATUS.md` — what is running, where it is, ETA, last commit. Include a
  `## Waiting on you` section; put `Nothing.` there when nothing is blocked, and
  the exact question when something is.
* `HEARTBEAT.md` — liveness, independent of any job, so a dead session is
  visible. Key lines, as bullets:
  `- **updated:** <now>`, `- **next update expected by:** <now + interval>`,
  `- **state:** <one line>`.
* `TASK_OVERVIEW.md` — progress across the whole project, one row per task.

Everything else is an **append-only log**, one file per event, named
`YYYY-MM-DD_HHMM_<kind>_<slug>.md`, where `<kind>` is one of:

| kind | when |
|------|------|
| `milestone` | a gate passed or failed |
| `phase-summary` | a phase closed; usually a copy of the phase summary |
| `ack` | receipt of an inbox message, and what was done about it |
| `hard-stop` | work stopped and cannot continue without an answer |
| `error` | something the session could not recover from |

Start each with a `# [kind] Title` heading. An `ack` must cite the message it
answers as `# [ack] re: <inbox filename>` and quote it, so the two can be
threaded back together.

## notifications/attachments/ (session → watcher)

Anything that does not belong in the body of a notification — a PDF, a tarball,
a chart, a note too long to read inline — goes here, and the notification that
explains it points at it:

```
Numbers behind this: [raw criterion output, 12 runs](attachments/bench.tar.gz)
and ![the three charts](attachments/regression.png).
```

The link text is the description the watcher sees, so make it say what the file
is. A flat `attachments: bench.tar.gz, regression.png` front-matter key works
too, for a file the prose has no natural place to mention; names there are
comma-separated, so one containing a comma has to be linked from the prose
instead.

Rules, all of them consequences of the folder being synced and read later:

* **Reference every file you deliver.** A file nobody points at is ignored
  completely — not shown, not counted. That is deliberate: it is how a payload
  still crossing the sync is told apart from one that has arrived, and how a
  leftover from three runs ago stays out of the way.
* **Write the payload before the notification that names it**, and expect the
  watcher to receive them in the other order anyway. Until the bytes land, the
  attachment shows as waiting on sync. Nothing is lost by being early.
* **Name files, not paths.** `attachments/report.pdf`, or `report.pdf` in the
  header. A reference that climbs out of the folder, or is absolute, or is a
  symlink, is refused and shown as refused.
* **Keep them small enough to sync.** A payload the client is still uploading
  when the run ends never arrives.

What the watcher's reader can render, it renders: a delivered `.md` or `.txt`
opens in a pane beneath the notification, without leaving the terminal. So a
long report is fine as a file — it does not have to be squeezed into the prose.
Everything else is handed to the desktop, where a known viewable type (`.pdf`,
images, `.csv`, `.log`) opens in a viewer and the rest — archives included —
only ever gets its folder opened. Nothing from a channel is ever executed.

## inbox/ (watcher → session)

One instruction per file, named descriptively; the content is read as
instructions. Read new files at every wakeup, act, write an `ack` notification,
then move the file to `inbox/done/`, prefixing the pickup time
(`YYYY-MM-DD_HHMM_`) onto its name. Leave the file's mtime alone — it is the
only record of when the message was written.

Keywords recognised on sight: `PAUSE` (finish the current step, start no new
jobs), `RESUME`, `STOP` (finish the current step, write a summary, end the
loop), `STATUS` (write a fresh `STATUS.md` now). Anything else is free-form.

## Optional front matter

Any file may open with a flat YAML block, which takes precedence over what the
filename and prose imply:

```
---
kind: hard-stop
ts: 2026-09-05T06:27:46+08:00
next_by: 2026-09-05T06:57:46+08:00
needs_reply: true
---
```

It is entirely optional; a channel that never emits it works the same. On the
three overwritten files, `ts` is read as the time of that update — the same
fact as an `updated:` bullet in the prose, since those files are rewritten
whole every time.

## Housekeeping

The sync client may leave `… (conflicted copy …)` duplicates when an
overwritten file is edited mid-sync. They are copies of the session's own
files; the session deletes them at wakeups.
