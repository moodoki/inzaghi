# Improvement plan

Phases 0 to 5 come from a performance review; 6 and 7 from the issue tracker.

**Where it stands.** Phase 0 merged as #6, phase 1 as #7, phase 2 as #15.
Phases 3 to 7 are open as #17 to #21, one branch each. Only the two standing
targets at the end are left, and neither has a finish line. Phases 6 and 7 are written up here
for the first time and need the decisions each one names. Two standing targets —
more harnesses, and the effect of the model behind them — sit at the end; they
are lenses on the phases rather than work with an end.

## The review

Reviewed on branch `rsync-transport` at `b9abed5` (main plus the then-unmerged
transport work), full suite green at 375 passed. Three reviews ran in parallel against
synthetic fixtures on local ext4 — filesystem I/O, UI-thread cost, and the
concurrency model. The live channels under `~/sync` were never read.

The workload every number below is measured against is the real one: four
channels, `poll_seconds = 2.0`, a one-second countdown tick, largest channel 183
notifications + 78 inbox/done + 88 attachments + 31 MB.

---

## The finding

**Almost everything expensive in this app is recomputed from data that did not
change.** The three reviews started in different places and arrived at the same
sentence:

| | recomputed every poll | cost |
|---|---|---|
| `attach._inside` resolves the attachments folder from `/`, once per reference | 88 identical answers per scan | **69 % of all syscalls** |
| `attach.refs` re-runs the fence/span/link regexes over every document body | 184 unchanged bodies per scan | 18 ms of a 36 ms scan |
| `build_rows` rebuilds every row: title parsed twice, timestamp formatted, whole body lowercased | 333 rows × 4 channels | **55–76 % of UI-thread work** |
| `state.unread` walks every event | 5× per two seconds per channel | 1.7 ms/tick now, 36 ms at N=5000 |
| `refresh_bindings()` recomposes the Footer | every poll, for a key that changes rarely | ~30 ms UI CPU per poll |

None of it is a cliff. All of it is linear in the number of messages, and the
knee is at **N ≈ 500–700 notifications per channel**: below that the app is
janky, above it every poll is a ≥100 ms freeze. BRAVO, the largest channel
watched here, is at 183 and growing.

Two things make it feel worse than the arithmetic suggests.

**The scan is CPU-bound, not I/O-bound.** On local disk a warm scan of four
channels is 105–131 ms of *Python CPU* — under a third of it is I/O at all. It
runs correctly off the UI thread, but Python CPU holds the GIL, so the UI thread
loses slices to it: `_apply` measured 32 ms in isolation and 51–61 ms alongside a
real scan. At N ≥ 2000 the scan is longer than the poll interval and the UI
contends essentially all the time (40–47 % blocked). This is why the I/O work and
the redraw work are not separable problems: cutting the scan's CPU is a UI fix.

**The costs you feel are the event-driven ones.** A tab switch is 138–159 ms at
the live size, because the appearing scrollbar resizes the list and every option's
height is walked two to three times. Typing `shard` into the search is five full
rebuilds, 29 ms each. A new notification rebuilds the whole timeline.

---

## One real bug

`_follow_preview` (`ui/channel_view.py:438-462`) posts `Open(refresh=True)` on
every poll once an open file is older than `CACHE_SECONDS`, and
`_read_attachment` (`ui/app.py:600-614`) is a thread worker with no in-flight
guard — nothing resets the stamp until a read *lands*. While a read is wedged,
every poll starts another.

Demonstrated with the pool bound forced to 3: three wedged reads filled it and **a
send queued behind them never ran**. At the real bound (`cpu + 4`) that is 24–70
seconds of a wedged volume with a delivered `.md` or `.txt` open, after which
sending, cleaning and opening are dead until restart.

This is precisely the failure `d075d7c` removed from the scan, reintroduced in the
preview. The docstring's argument for *non-exclusive* is right; non-exclusive and
unbounded are different properties, and the scan already has the pattern.

---

## Measured baseline

Medians, milliseconds, at the live size unless stated. UI figures are headless
(Textual `run_test`, 220×60) and exclude the terminal's own repaint cost.

| | live (183) | 500 | 2000 | 5000 |
|---|---|---|---|---|
| syscalls per poll, 4 channels | 9,719 | — | — | — |
| scan wall, 4 channels | 146 | — | ~1,200 | ~3,600 |
| `_apply` on the UI thread | 32 | 76 | 279 | 691 |
| — of which `build_rows` | 17.5 | 50 | 224 | 526 |
| footer recompose, every poll | ~30 | ~30 | ~30 | ~30 |
| tick | 7–12 | 12–19 | 29–35 | 55–62 |
| tab switch | 138–159 | — | 554–600 | 475–1070 |
| rebuild on a new notification | 52 | 140 | 513 | 1,935 |
| search keystroke, many matches | 29 | 63 | 247 | 648 |
| UI thread blocked, real timers | 9–11 % | — | 40–47 % | — |

Of the 9,719 syscalls in a poll, 98 % are `stat`/`lstat` and only 36 KB is read.
The 31 MB of attachments is never read by a scan, only stat-ed.

**What the design deliberately pays for is not the problem.** The unconditional
re-read of the three in-place singletons that `c3c4cac` introduced — the fix for
File Provider mounts lying about placeholders — is **under 1 % of syscalls**. None
of the proposals below weakens it.

---

## The plan

Ordered so that each phase is worth doing on its own, and nothing later depends on
a judgement call made earlier. Each lands as its own branch and pull request, so
the bug fix could merge without waiting for the performance work — and so the two
issue-driven phases at the end need not wait for any of it.

### Phase 0 — the bug (half a day)

**0.1 Guard the preview re-read.** A per-pane "read in flight" flag with a
remembered "read again" bit, mirroring `_scanning`/`_scan_again`; or refuse to post
`Open(refresh=True)` while one is outstanding for the same attachment.
*Buys:* sending survives a wedged volume with a file open. *Risk:* a payload
rewritten mid-read shows the older text until the next poll — already true.
*Test:* the pile-up experiment — `read_text` called once while wedged, and a
`_write` still runs.
*Convention to add:* anything that re-fires on the poll needs an in-flight guard,
not just the scan.

### Phase 1 — stop recomputing unchanged derived data (1–2 days)

Nothing here caches file *contents* or trusts a `stat`; it caches work derived
from objects the existing cache already guarantees are identical.

**1.1 Reuse rows when the snapshot is unchanged.** Keep the previous
`(pinned, events, threads, attachments)` and `unread`; if all compare equal — Docs
are cached objects, so `==` is an identity shortcut — skip `build_rows` and
`_apply_filter`, and run only `update_strip`.
*Buys:* steady poll 32 → ~8 ms at live size; 279 → ~40 at N=2000.
*Wrong if* a label depends on `now` at sub-minute granularity: `fmt.ago` counts
seconds between 45 and 60, so force a rebuild while any pinned doc or picked-up
thread is under a minute old.
*Test:* same snapshot twice → `build_rows` once; new event → rebuilt; `now + 61 s`
→ labels refreshed.

**1.2 Cache the unread sets on the app**, invalidated by `_mark_read`,
`action_mark_all_read`, `prune_receipts` and a new snapshot; the tick reads the
cache. Receipts are only ever written by the app, so no external invalidation
exists. *Buys:* −35 ms per poll and most of the tick at large N.

**1.3 Cache `attach.refs(doc)` beside the parse.** Extraction is a pure function
of text already in memory; *resolution* stays per scan, as the convention
requires. *Buys:* 18 ms of a 36 ms channel scan — ~72 ms of the 146 ms poll, and
GIL pressure with it.

**1.4 Call `refresh_bindings()` only when `bool(self._conflicts())` changed.**
*Buys:* ~30 ms UI CPU per poll at every N. *Test:* an unchanged poll remounts no
`FooterKey`; a conflict appearing still shows `K`.

**1.5 Tick only what is on screen** — `update_strip` for the active pane, and the
overview only when its tab is active. Removes three full-screen relayouts per
second. Keep the `composed` guards.

**1.6 Memoise `Event.title` and the search haystack per `Doc`.** `Doc` is frozen
and `channel._doc` returns the same object across polls; today the title is parsed
twice per row and the whole body lowercased, every poll.

### Phase 2 — cut the syscall storm (1 day)

Most valuable on a mount where each stat is an IPC round trip, which is the
environment this app exists for.

**2.1 Stop resolving from `/` per attachment reference** (`attach.py:227-236`).
`_lexical_refusal` already rejects `..`, absolute paths and `~`; `is_symlink()`
rejects a symlinked leaf. What `resolve()` adds is a symlinked *ancestor* — the
same answer for every reference, so check it once per scan — and a symlinked
intermediate component inside a nested name.
*Buys:* **69 % of the syscalls in a poll**, ~5 ms per channel here and far more on
a synced mount. *Risk:* none to freshness; the security property must be kept
exactly. *Tests:* the existing refusal tests still apply, plus a symlinked
subdirectory inside `attachments/`, `attachments/` itself symlinked out of the
channel, and a budget test asserting ≤2 lstats per flat reference.

**2.2 One stat per file, not three.** `_text_files` stats every entry via
`is_file()`, `_doc` stats it again, `Doc.load` a third time. Use `os.scandir` +
`DirEntry.is_file()` (no syscall where `d_type` is known, falling back to stat
where it is not) and pass the entry's stat down.
*Buys:* a further 11 % of syscalls. *Test:* a patched `os.stat` counter asserting
≤ files + singletons per scan.

### Phase 3 — the interactions you feel (half a day)

**3.1 `scrollbar-gutter: stable` on `#timeline` and `#attachments`.** The list is
measured once per show instead of two or three times.
*Buys:* tab switch 140–160 → ~60–80 ms at live size; 550 → ~250 at N=2000.
*Cost:* one blank column when nothing overflows. One CSS line.

**3.2 Debounce the channel search** — rebuild ~100 ms after the last keystroke.
Typing `shard` is five rebuilds today.

**3.3 Build prompts with `Content.from_markup` instead of `Text.from_markup` +
`visualize`**: ~50 µs → ~15 µs per row on every rebuild, switch and search.
*Moderate risk:* Rich `escape()` output and colour names must survive Textual's
markup dialect — the titles genuinely contain `[milestone]`, so the escaping test
matters.

### Phase 4 — scheduling hygiene (half a day)

**4.1 Don't re-fire the remembered rescan instantly.** Once a sweep outruns the
poll, the pending flag re-fires immediately and the volume is scanned
continuously — on a folder that is slow *because* it is busy, this is the worst
client behaviour available. Schedule the follow-up at `poll_seconds - elapsed`,
except for a request that came from a write, which must stay immediate so the
rescan still sees the send.

**4.2 Backstop the sweep against non-`OSError`.** `_read_channels` catches only
`OSError` and `@work` defaults to `exit_on_error=True`, so a `ValueError` in one
channel's scan takes the whole app down. Catch `Exception` per channel, keep the
last snapshot, surface it in `problems`. The "one widget goes quiet rather than
crashing the app" convention currently rests on parser discipline alone.

**4.3 Filter `_apply` to channels still present.** A channel dropped by discovery
mid-sweep has its Snapshot resurrected and retained for the life of the process.
One line.

**2.3 Narrow the `CACHE_SECONDS` expiry to recent files.** Today every cached
parse older than a minute is re-read, including the append-only log: 1,048 opens
and 2.4 MB per four-channel minute, landing in a single poll. The log is immutable
by contract and an atomic replace changes the inode the fingerprint already
carries, so the expiry buys protection only against a session that rewrites a log
entry in place *and* a stat that froze. Keep it for entries whose mtime is recent —
the ones a session might still be fixing — and for the three overwritten
singletons, which keep re-reading unconditionally as `c3c4cac` requires. Drop it
for the rest.
*Decided:* narrow rather than remove, keeping the spirit of the convention. This
edits the convention text in CLAUDE.md and narrows
`tests/test_channel.py::test_no_parse_is_trusted_for_ever` to singletons and
recent entries rather than deleting it.

### Phase 5 — window the timeline (1 day)

Materialise the newest ~400 rows plus an "N older…" option that extends the
window — also extended by `G`, by a selected key outside it, and by a filter
match. This is the only proposal that changes the shape of the problem rather
than its constant: rebuild at 5000 messages goes from 1.9 s to ~0.1 s, and the
tab switch and search costs fall with it, since both scale with the number of
materialised options.

Touches `_render_rows`, `_row_for`, `_retitle` and `_index_of`; the unread divider
needs a clipped position with a count when the boundary falls outside the window.
Cursor restore by option id is unchanged.
*Tests:* a 3000-row fixture materialises `K + chrome` options; selecting the last
option extends the window; a filter matching an old row shows it; the divider
reports the right count when clipped.

## From the issue tracker

Two open issues, folded in here so the work sits in one order. Neither is
performance: #14 is about a channel telling the truth when the session behind it
stops, #12 is a missing half of the contract. They are sequenced after the
measured phases because those are in flight, not because they matter less — #14
in particular is the kind of fault that makes every other signal untrustworthy.

### Phase 6 — a heartbeat that outlives the agent's turn (issue #14)

**What the issue says.** Under Claude Code the heartbeat stops when a usage limit
is reached, and a stopped heartbeat is indistinguishable from a dead session. It
asks for the heartbeat to keep going, and for the reason and the reset time to be
said out loud. It also notes this does not always happen: some sessions run a
monitor that does not use the model at all.

**Evidence from this machine, 2026-09-18 14:20.** BRAVO went six minutes past
its deadline while its session was healthy and mid-turn — editing run scripts,
auto mode on, three monitors up. Two messages sat unread in its inbox for the
same reason: a session reads its inbox *between* turns, and writes its heartbeat
there too, so one long turn stops both. ECHO never lapses, because its heartbeat
is written by a shell loop bound to the harness process, needing no model at all.

Codenames throughout: the mapping is local, for the reason in `CLAUDE.md`. The same divide will be visible in any harness: a heartbeat
driven by the agent's loop is a heartbeat that reports on the agent's loop, not
on the session.

So the fault is not "the model stopped" but "the only writer of liveness was the
thing that stopped". Three parts, and the first is most of the value:

**6.1 The skill says to run the heartbeat off something that is not the model.**
A timer, a cron line, a `while` loop — anything that keeps writing while the
agent is thinking, paused, rate-limited or waiting on a human. The contract
already asks for a heartbeat "independent of whatever job is running"; it has to
say independent of the *agent* too, and give a worked example, because the
sessions that get this right today are the ones that happened to build a timer.

**6.2 A paused session can say so, and Inzaghi can show it.** Add a front-matter
key the contract advertises — `paused_until`, with the reason in `state:` — so a
session that hits a limit writes one line before it goes quiet. The viewer then
has a third state between *fresh* and *stale*: **paused, resumes 15:40**, which is
not an alarm. Today the same silence means three different things — dead, wedged,
and rate-limited — and the watcher cannot separate them.

**6.3 The timer writes it, not the agent.** If 6.1 lands, the timer is the writer
and the agent only supplies `state:` when it has something to say. That also
fixes BRAVO's case above, which has nothing to do with usage limits: a long turn
stops being a liveness event at all.

*Decisions this needs:* whether the viewer gains a paused state (it changes what
the `◍`/`○` marks mean and what `attention()` counts), and whether the contract
*requires* an independent writer or merely recommends one. Requiring it makes
every existing session non-compliant until it is updated.

### Phase 7 — the inbox carries attachments (issue #12)

**What the issue says.** A session can deliver files with a notification; we
cannot send any back. The composer takes text and nothing else, so a log excerpt
too long to paste, a config to apply, or a screenshot of what went wrong has no
way across. It proposes `inbox/attachments/` on the same terms as the outbound
folder, filled by the composer, with the path **copied and the link rewritten**
rather than merely mentioned — because a path that means something here means
nothing on the machine that reads it, and the session cannot tell a reference
that was never going to resolve from a payload that has not synced yet.

**Why this is mostly assembly.** The inbound half is built and none of it is
inbound-specific except the folder it is pointed at: `attach.refs` pulls
references out of a `Doc`, `attach.resolve` checks them against a folder, and a
referenced file that has not landed reads as *syncing*. `compose._unique` already
disambiguates a name that exists. `compose._atomic_write` already stages outside
the folder being written to, which a copied payload needs as much as a message
does. The work is the composer, the contract, and the ordering.

**7.1 Attaching from the composer.** A paste whose text is a path, which is what
a terminal gives you when a file is dropped on it, and a key that asks for a path
for when there is nothing to drag. Both end in the same place, and both need the
same answers for a path that is not real, is a directory, or cannot be read.

**7.2 Copy, then rewrite.** Copy into `inbox/attachments/`, rewrite the link to
point at the copy relative to the folder, leave the rest of the prose alone.

**7.3 The contract has to say it exists**, in `protocol.CHANNEL_README` and the
skill, or a session will never look. The drift test and `inz skill sync` keep the
two in step.

**7.4 Ordering, and the payload's afterlife.** The payload must land before the
message that names it — the mirror of the inbound *syncing* state, except the
session is not Inzaghi and will not wait politely, so the contract should say
what it must do when a named file is missing. And `transport.retire` deletes a
local message once it appears in `done/`; nothing currently retires the payload
it named.

*Decided:* a session waits briefly — finish the inbox, look again a few minutes
later — and then acts and notes the absence in the `ack`, never in a loop. The
ceiling is 25 MB, in the region of the whole inbound corpus across four
channels. A payload is retired with its message, unless a message still in
`inbox/` names it.

---

## Standing targets

Not phases: these have no finish line, and each is a lens to judge the phases
above by rather than a thing to build once.

### More harnesses than the one it grew up in

Today's spread is six Claude Code sessions and one opencode session, against a
skill installer that knows two harnesses:

| harness | installs to | exercised by |
|---|---|---|
| `claude-code` | `~/.claude/skills`, `.claude/skills` | six live channels |
| `antigravity` | `~/.gemini/config/skills`, `.agents/skills` | nothing yet |
| opencode | — not in `skill.py` | GOLF, reached some other way |
| codex | — not supported | — |

Two of those rows are the interesting ones. **Antigravity is supported and
unexercised**, so we know the install path works and nothing else. **opencode is
unsupported and working**, which is the more useful fact: a channel written by a
harness the installer has never heard of still satisfies the contract, which is
evidence the protocol is genuinely harness-neutral rather than Claude Code's
conventions with a folder around them. Adding **codex** is the test of whether
that holds for a third shape of instruction file.

Supporting a harness is two different sizes of job, and `skill.py` only does the
small one. The small one is a `HARNESSES` entry: where the skill goes and what
wrapper the front matter needs. The large one is what the harness can *do* — and
that is where this meets issue #14. A harness that can hold a background timer
can keep a heartbeat alive while the model is rate-limited, paused or thinking;
one that can only act inside a turn cannot, and the contract must then ask it for
something else. Whether that distinction is real, and which harness falls on
which side, is a question in front of the analysis now running.

So the target is not "support more harnesses" but: **for each harness, what can
it promise?** A table of that is worth more than another installer entry.

### Different models behind the same instructions

Everything in this protocol is prose. It is read by whatever model the session is
running, and the same paragraph does not land the same way twice — the sessions
here have already interpreted the same skill differently, which is what the
current analysis is measuring.

Worth tracking deliberately rather than discovering:

- **Whether a weaker or cheaper model can run a channel.** A channel costs a
  wakeup's worth of reading and writing every thirty minutes, forever. If that
  only works on a frontier model, the protocol is more expensive than it looks;
  if a small model can keep a channel honest, an unattended run gets much
  cheaper to watch.
- **Which instructions survive a weaker reader.** The parts that need judgement —
  "say what the file *is*, not 'see attached'", deciding when something is a
  `hard-stop` — are the parts that will degrade first. The mechanical parts, like
  the filename shape, should not degrade at all. Where a rule turns out to need
  judgement, that is an argument for making it mechanical.
- **What a model does when the contract is silent.** The gaps are filled
  differently by different readers, and every gap the analysis finds is a place
  where the model, not the protocol, is deciding what the watcher sees.

Neither of these has an owner or a date. They belong here so that a phase which
assumes one harness, or one model's reading, is caught while it is still a plan.

---

### Considered and deferred

**Building rows on the scan thread, and per-channel scans.** Both are real
architectural options and both are premature. Row-building on the worker is Python
CPU under the same GIL — it caps the contiguous stall without removing the work.
Per-channel scans only pay off when channels wedge *independently*, and on a
single Nextcloud mount they will not.

---

## Expected result at the live workload

| | now | after phases 0–3 |
|---|---|---|
| syscalls per poll | 9,719 | ~2,000 |
| scan wall, 4 channels | 146 ms | ~50 ms |
| UI-thread work per poll | ~90 ms | ~25 ms |
| tick | 7–12 ms | ~4 ms |
| tab switch | 138–159 ms | ~70 ms |

The knee moves from N ≈ 500–700 to somewhere past 1500, at which point windowing
is the next thing to do rather than the first.

---

## What was not measured

- **Terminal output.** Headless, `App._display` is a no-op. A 220×60 repaint four
  times per poll plus the emulator's own cost is unmeasured — which means phases
  1.4 and 1.5, both of which cut frames, may matter more than their figures show.
  This is the most likely explanation for a gap between "janky" here and
  "unresponsive" in real use.
- **Per-syscall latency on a File Provider mount.** Nextcloud on Linux syncs into
  a plain ext4 directory; the syscall *counts* are the transferable number, and on
  such a mount they are what hurts — which is why 2.1 ranks where it does.
- **Real document sizes.** The fixture averages 625-character bodies; real ones
  are larger, and `build_rows` scales with body size at roughly 2.5 µs per KB per
  row.
- **A genuinely wedged sync read.** Simulated with `threading.Event`; faithful to
  the thread behaviour, not to how the client actually fails.
