# Seven channels, two weeks: how the contract was actually used

Three analyses of the live corpus, run 2026-09-18: what sessions wrote, how
their liveness actually behaved, and how different harnesses read the same
skill. Everything here is evidence from running channels — 519 event files,
~3,200 heartbeat writes, 186 delivered messages, two weeks, seven channels —
not design opinion.

Channels are codenames; the mapping is local, for the reason in `CLAUDE.md`.
Read-only throughout: the channels were copied out before being touched, and
nothing was written into any of them.

Where a fact is reconstructed rather than observed it says so. The heartbeat
files are overwritten, so their history came from harness transcripts, the
systemd journal, notification timestamps and the sessions' own reports of
their own lapses.

---

## 1. The population is not one population

The single most useful fact about this corpus, and the one that explains most
of what looks like drift:

| | contract they hold | how they learned it |
|---|---|---|
| ALFA, BRAVO | hand-written v1 README, predating the skill by four days | a user's inbox message, turned into a project `CLAUDE.md` and a writer library |
| CHARLIE | an earlier v2 | `inz init` |
| DELTA, ECHO, FOXTROT, GOLF | the current contract | `inz init` |

ALFA and BRAVO were built to a request that arrived in an inbox message —
*"create a HEARTBEAT.md with the update time and anticipated next update time
every 30 min, should be independent of other processes, so that we know that
the session has not died"* — and BRAVO was explicitly created as a mirror of
ALFA. When the skill arrived four days later they **audited themselves against
it** rather than re-reading it as their instructions, and both READMEs were
deliberately preserved.

So when this document says a channel diverges, the first question is whether it
was ever told. BRAVO's `info` kind, its `## Waiting on you` phrasing, ALFA's
absent front matter on events: all of that is v1 compliance, not drift. Running
`inz init --force` on those two would erase most of the corpus's apparent
non-conformance at a stroke — except that BRAVO's own README documents `info`,
so for that channel conforming means rewriting the README first.

---

## 2. Liveness: what actually happened

This is the section that matters for issue #14, and the short version is that
**the issue names a real cause but not the main one.**

### 2.1 Who writes the heartbeat

| | writer | needs the model? | survives limit / prompt / long turn | survives reboot |
|---|---|---|---|---|
| ALFA, BRAVO | the wakeup loop, via a writer script | yes | no / no / no | no |
| CHARLIE | an in-session cron; since today also a conditional backstop | yes (backstop: no) | no / no / no | no — the cron died at the reboot |
| DELTA | an in-session cron, session-scoped, 7-day expiry | yes | no / no / no | no |
| ECHO | a shell loop bound to the harness process | **no** | yes / yes / yes | no |
| FOXTROT, GOLF | a systemd user timer, `Persistent=true` | no | n/a | **yes** |

Regularity is diagnostic. The timer channels sit at 20.00–20.17 minutes. The
loop channels have a 31-minute median against a 30-minute promise, with a tail
out to 36–41 minutes at p95.

### 2.2 Usage limits stop every model-written heartbeat, exactly as the issue says

Five separate five-hour-window limits between 5 and 14 September stopped all
four loop-driven heartbeats for 45 minutes to 2.5 hours each — simultaneously,
because the sessions share one account. The weekly limit stopped them for
**41 hours** (16 Sep ~17:45 → 18 Sep 10:45). The three timer-driven heartbeats
wrote straight through all of it.

One channel's own words, from its retrospective error notification: *"Every
agent turn after that returned HTTP 429, and the session could not act at all,
including to refresh HEARTBEAT.md."*

There is a worse tail to this. After the weekly limit reset at 23:01 the
harness auto-continued, the model's first request **timed out** four minutes
later, and every loop-driven session then sat silent until a human returned at
10:45 the next morning — roughly twelve hours of stale heartbeat with quota
available the whole time.

### 2.3 But limits are not the main cause of lateness

Of BRAVO's 60 intervals that ran more than three minutes past its promise,
**four were usage limits and 56 were the session simply busy**. ALFA: 69
lapses, four of them limits. Neither channel has a single lapse where the
session was doing nothing.

The reason is structural, not incidental. The heartbeat is written at the
*start* of a wakeup and the next wakeup is scheduled at the *end* of it, so
**every cycle overruns its promise by the length of the turn.** A twenty-minute
turn turns a 30-minute promise into a 50-minute interval. Only 47% (BRAVO) and
58% (ALFA) of intervals land within promise + 1 minute.

That has a direct consequence for the viewer: for a loop-driven channel, *late*
is mostly cycle noise — about half of BRAVO's cycles are 1–6 minutes over — so
a paused state must never be **inferred** from lateness. It has to be a fact
the file carries.

### 2.4 Six causes, one appearance

A stale heartbeat in this fortnight had at least six distinct causes:

1. a five-hour window limit
2. the weekly limit
3. **a permission prompt** — two of the three longest outages, 13 hours and
   12.5 hours, were tool calls blocked on a confirmation dialog; one session
   diagnosed the other's as a usage limit and had to correct itself
4. a long turn (§2.3)
5. **the wakeup mechanism itself being lost** — one channel's session-scoped
   cron expired on schedule and the channel went dark for 48 h 41 m; another's
   cron did not survive a session restart and has had no scheduled wakeup since
6. the machine or the process going down

The viewer shows all six identically: an overdue counter, `◍` then `○`, and a
state line frozen at whatever the session last said. During the 41-hour pause
one channel's strip carried the same sentence about its running jobs and their
progress from beginning to end — a line that was true when written and
meaningless by the time anyone read it.

### 2.5 The model should not be composing timestamps

CHARLIE writes its heartbeat through a model-composed heredoc, and its stamps
drift 10–52 minutes behind the moment of writing. One write today carried a
`next_by` that was **already 15 minutes in the past when it was written**, so
the channel reported itself late while actively writing. The two channels that
compute stamps in a tool do not have this problem.

### 2.6 A backstop that lied in the other direction

CHARLIE built a conditional backstop today — a shell loop that writes only when
the heartbeat has gone quiet, saying plainly that *the session* has not updated
and that the backstop wrote the line. The design is the best idea in the
corpus. Its threshold was set to 25 minutes against a 37-minute promise, so it
fired four times over a **healthy** session, announcing it was "paused,
diverted, or dead".

The lesson is not against backstops, it is a constraint on them: a backstop
must not fire inside the session's own promise, and must not assert more than
it knows.

### 2.7 What survived the reboot

Only `Persistent=true` systemd timers. Every session-bound writer lapsed by the
reboot plus the human's reaction time; the timers were 0–4.5 minutes late.

---

## 3. How the skill was read

### 3.1 The sentence at the centre of #14

> *"Refresh it on a timer of its own — every 30 min or less — independently of
> whatever job is running. If it can only be refreshed by a wakeup that a stuck
> job prevents, it is not a heartbeat."*

Four of seven sessions read "job" as **the compute job** and "a timer of its
own" as **their own wakeup schedule**. Their writer scripts say so in almost
the same words: *"proof that the session is alive, independent of any job.
Written at every wakeup."*

The three that read it as *independent of you* were the two whose harness left
them no choice, and one that worked it out alone. **Nobody derived the right
design from that sentence.** One session spotted the gap the day it adopted the
skill and offered the fix — *"Mine is currently refreshed by the wakeup loop,
so a genuinely stuck session would stop updating it… If you want a true
independent heartbeat I would add a small cron or systemd timer… say the
word"* — and was never answered.

### 3.2 Five designs for one requirement

The wakeup loop; a shell loop bound to the harness process that writes an
epitaph when the process exits; an unconditional external timer that states in
the file what it proves and what it does not; a conditional backstop that
speaks only when the session goes quiet; and nothing beyond the loop, with the
gap declared afterwards. Each is a place the skill left open.

The two that are most honest both **say what they prove**. One carries: *"Written
by a timer every 20 minutes, independently of any agent session. It proves the
broker is answering and that this machine is up; it does not mean an agent is
working."*

### 3.3 What everybody built that the skill never mentions

- **A writer script.** Five of seven channels write through code. The two that
  do not are the two whose event format drifts under load.
- **An inbox watcher, separate from the heartbeat.** Six of seven built one
  after being scolded for pickup latency. The skill sets no latency expectation
  at all. Watchers came with an invention the contract lacks: a
  size-stable-for-N-seconds check before reading, and `mv -n` when moving.
- **A "how to read me" footer in the heartbeat** — *"If the 'next update' time
  has passed, the session is paused (usage limit) or dead; STATUS.md holds the
  last known state."* Four channels carry a variant; it propagated by copying.
  It is a session trying to say what its heartbeat *means*, and **the viewer
  never reads it**, because it is body prose.
- **Naming the liveness mechanism** in the status or heartbeat: which timers and
  watchers are running.
- **A machine-readable record of the channel path**, five different ways.
- **A structured source** for the task overview and the waiting-on-you section,
  kept outside `notifications/`.

### 3.4 What the skill asks for that nobody does

`PAUSE`/`RESUME`/`STOP` — in every `done/` folder, the only keyword ever sent
was `STATUS`. Hard stops with options: eleven in 519 events, all in the two
pre-skill channels, none in the five that were given the current text — the
others put questions in acks and milestones instead. The manual fallback for
copying the reference README by hand: never used. "Ask where it should live":
the path was always dictated first. "Between wakeups, write an event file the
moment something happens": meaningless for a session that has no *between*.

---

## 4. Harnesses

What actually varies is not where the skill file goes — it is what the harness
can *hold*.

| | in-harness scheduler | background process outliving a turn | survives reboot |
|---|---|---|---|
| Claude Code | yes, but **session-scoped**: stops at a usage pause, stops at a permission prompt, expires after 7 days, dies with the session | **yes** — a detached shell loop ran 3+ hours bound to the process | no |
| opencode (one-shot `run`) | none | no — the process exits | via systemd only |
| Antigravity | unknown; not exercised | unknown | unknown |

Two observations follow.

**The installer's harness table records none of this.** It records install
paths, and its `layout` string — the only other field — is identical for both
supported harnesses and is never branched on. Meanwhile `install("opencode")`
raises `UnknownHarness`: the harness the protocol is demonstrably working in is
the one the installer refuses by name. The skill reached it anyway, because
opencode discovers Claude-compatible skill directories and found the symlink.

**The GOLF channel is the interesting proof.** It is written by a stateless
one-shot worker — a fresh process per message, no memory between runs, its
continuity being the channel itself — and it satisfies the contract better than
two of the Claude Code channels: every ack cites its message, every pickup is
prefixed, temporaries are dot-prefixed and renamed, and its acks verify before
they claim. What it cannot satisfy is the skill's *addressee*: "You are the
session end", "at every wakeup", "between wakeups" all presume one long-lived
process with memory. **The protocol is harness-neutral; the skill's prose is
not.**

---

## 5. Where the contract was followed and the reader still failed

These are Inzaghi's bugs, not the sessions':

1. **Every repeated one-word message collides in threading.** `normalise_ref`
   strips all stamps, so every `<stamp>_status.md` keys to `status`, and the
   pairing keeps one ack per key: **17 threads mis-paired, 13 perfectly formed
   acks orphaned**, across four channels. One channel's eight STATUS threads all
   point at the same ack, some showing round trips of days. Any message sent
   twice with the same words does this.
2. **`needs_reply: true` is set on 25 event files and read nowhere but
   `STATUS.md`.** The contract advertises it for "any file". Sessions put it
   exactly where it matters — a hard-stop needing a login, a summary awaiting a
   decision — and it did nothing.
3. **A link whose label wraps across a line is not a link.** Sessions hard-wrap
   prose at ~100 columns; one delivery in 194 wrapped inside the brackets and
   became invisible.
4. **`Status.headline` is garbled on five of seven channels**, because sessions
   open their status with a bold label or a key-value bullet and the parser
   strips bullets but not emphasis. The overview's one-line summary reads
   `updated:** 2026-09-18T13:53…`.
5. **A decision request filed under an undocumented kind never raised the
   flag** — the contract does not say that only `hard-stop` and `error` are
   loud.
6. **Backticks are how these sessions cite files.** Thirty-four references to
   real delivered files are written as code spans and deliberately ignored.
7. **`first_sentence` strips `-*+` bullets but not `1.`**, so a numbered list
   under `## Waiting on you` parses to `1` — currently the right answer by luck.
8. **Three attachments will read "waiting on sync" for the life of the
   channel**: correct behaviour ("absent is late, not gone") producing a
   permanently wrong display.

---

## 6. What this changes

### For issue #14

The plan's Phase 6 was written around usage limits. The evidence says the
requirement should be stated more generally, and one part of the original
proposal is impossible.

**Impossible as proposed:** a session cannot write `paused_until` at the moment
it matters, because the pause *is* the model not getting a turn. Only an
external writer can — and the quota state is readable locally by one, or the
model can write it *pre-emptively* at a wakeup when a limit is near.

**The design the corpus argues for:**

- **Separate the two facts.** The heartbeat answers "is the machinery up",
  written by something that is not the model. Agent progress is already
  `STATUS.md`'s timestamp, which the reader parses. Fresh heartbeat + old
  status = alive but quiet or paused; nothing fresh = dead or unreachable. The
  third viewer state falls out of two files the reader already reads.
- **Every heartbeat says who wrote it and what that proves** — a `writer:`
  field, not the body prose four sessions invented, which the viewer cannot see.
- **Never compose a timestamp in the model** (§2.5).
- **Write the heartbeat last in the wakeup, or promise cadence + turn length.**
  This is the cheapest item here and it removes half the lateness in the corpus
  (§2.3).
- **A backstop must not fire inside the session's own promise**, and must say
  it is the backstop speaking (§2.6).
- **Two worked examples, not one**: a process-bound shell loop with an epitaph
  for a harness that can hold one, and timer + path units for a harness that
  cannot. Nobody derived either from the current sentence.

On requiring versus recommending: four of seven channels would be
non-compliant today, and the objection one of them raised is the right one — a
timer that says "alive" over a wedged agent is a lie. Phrasing the requirement
as *"a writer that does not depend on the model, saying what it proves"* rather
than *"keep the heartbeat alive"* satisfies both kinds of channel honestly.

Also for the viewer: keep late alerts off by default until the ordering fix
lands, because today's `◍` is mostly turn length.

### Candidate work not yet in the plan

Section 5 is eight defects, of which the threading collision and the unread
`needs_reply` are the two with real user-visible cost. None are performance
work and none belong to an existing phase.

### For the standing targets

The harness capability table in §4 is the answer to the question the plan left
open — and it says the axis is the runtime, not the install path. Adding a
harness to the installer is six lines; knowing whether it can hold a background
process is the part that changes what the contract can ask of it.
