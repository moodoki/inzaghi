# <project> — unattended-run channel

This folder is the link between the session running the **<project>** project and
whoever is watching it from elsewhere. It is synced, so either end can be
offline when the other writes. Protocol v1 (Inzaghi).

Everything is plain UTF-8 Markdown. Timestamps are ISO-8601 with an offset.
Write every file atomically — to a temporary name in the same directory, then
rename into place — so the sync client never uploads half a file.

## notifications/ (session → watcher)

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

It is entirely optional; a channel that never emits it works the same.

## Housekeeping

The sync client may leave `… (conflicted copy …)` duplicates when an
overwritten file is edited mid-sync. They are copies of the session's own
files; the session deletes them at wakeups.
