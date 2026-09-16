# Performance review and improvement plan

Reviewed on branch `rsync-transport` at `b9abed5` (main plus the unmerged transport
work), full suite green at 375 passed. Three reviews ran in parallel against
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
janky, above it every poll is a ≥100 ms freeze. udang is at 183 and growing.

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
a judgement call made earlier. All five phases are approved; each lands as its own
branch and pull request, so the bug fix can merge without waiting for the
performance work.

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
