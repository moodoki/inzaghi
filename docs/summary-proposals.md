# Summarising a long channel — seven proposals

For issue #26. A brainstorm, deliberately not a recommendation: seven models
were given the same brief and the same measured corpus, each pushed toward a
different angle, and what follows is what came back. Channels are named by
codename throughout.

## The corpus they were reasoning from

Measured 2026-09-25 across ten live channels: **805 events**, the busiest
channel 236 over 21 days, mean body 2.4 kB — so the largest log is ~630 kB of
prose accumulating at ~11 entries a day.

| kind | count | share |
|---|---|---|
| ack | 339 | 42% |
| milestone | 317 | 39% |
| error | 58 | 7% |
| info | 45 | 6% |
| phase-summary | 26 | 3% |
| hard-stop | 19 | 2% |
| retraction | 1 | — |

Two things fall out of the table before any design does. **Nearly half the log
is receipts for messages the watcher itself sent.** And `info` and `retraction`
are not contract kinds — sessions invented them, which is its own finding.

## The result worth leading with

**Five of the seven refused the premise.** Given "summarise the log", they
independently argued that the log is not too long, it is padded: it is full of
entries the watcher can already predict, and the fix is to stop showing them
rather than to compress them. Three of those five had no way of seeing each
other's answers and still reached the same diagnosis by the same route — the
42% figure.

That is not proof they are right. It is a strong signal that the obvious
reading of the issue — generate prose over the log — is the minority position
among people looking at the data.

## The three axes the proposals actually differ on

- **Who writes it:** the viewer's own code, the session, or a model.
- **What the unit is:** the event, the thread, a work span, a day, or the gap
  since you last looked.
- **What happens to what it hides:** filtered, folded, ranked, or replaced.

## A. Fold what the watcher already knows — four variants, no model

*Shared claim: the structure needed is already in `model.py`; an LLM is
unnecessary and possibly harmful.*

**A1 · Fold the log, don't summarise it** *(Opus 5)* — Three mechanical rules
in a pure `fold.py` beside `rows.py`. An acked thread folds to one row, because
`_weave` already pairs each sent message with the ack citing it: that is the
42%, free. Runs of one kind within one day fold to `◆ milestone ×7 ·
09:12–11:40 · <first> … <last>`. A `phase-summary` is a *boundary*, not a
foldee — everything between two of them folds beneath the later one, preferring
a summary someone wrote to one we invent. Nothing loud, unread, in-flight or
pinned ever folds, so the four questions the UI exists to answer are untouched
by construction. Plus a since-line: `since 09:12 yesterday · 34 new · 3
milestones · 1 error · 18 acks · 2 of your 3 answered`.
*Smallest ship:* ack-folding alone, ~40 lines.

**A2 · Collapsed threads: hide what you already know** *(Haiku 4.5)* — The same
insight as a single stateless toggle: **Show News / Show All**, computed fresh
on every scan, no stored state to go stale. Acks are validated against the
inbox files they cite before being hidden, so an ack that references nothing
stays visible — a session that drifted is not silently suppressed. Explicitly
filtering, not summarising: everything hidden is still searchable and citable.

**A3 · Catch-up, not digest** *(Grok 4.6)* — The most aggressive: catch-up mode
becomes the *default* whenever a channel has unread events, with the full
timeline behind a key "for archaeology". Two-line census, then an outline of
the gap. **Acks are not rows at all** — they badge the inbox files they cite:

```
since Thu 18:02 · 23 events · 4 milestones · 2 errors · 11 acks · 1 phase
inbox: [x] retry-deploy.md  [x] bump-timeout.md  [ ] please-stop.md
```

That checklist answers "has it seen what I sent" without opening eleven 2.7 kB
receipts. Errors pin above everything and never collapse; date becomes a
right-column label rather than a grouping axis.

**A4 · Unread-first triage with kind-weighted salience** *(DeepSeek V4 Pro)* —
The only one that reorders rather than hides. An `All`/`Unread` toggle; in
`Unread`, rows sort by a salience integer from kind priority (`hard-stop` 4,
`error` 3, milestone 2, ack 0), recency, and a colour bar down the left edge.
Zero extra screen space. Treats this as retrieval, not summarisation: the
person has a question, rank for it.

## B. Make the session write it — *The session writes the digest* *(Fable 5.1)*

*Claim: the watcher cannot summarise this well, because it sees prose where the
session saw the run.* Which of thirty milestones mattered, and why an error was
a dead end, exists in the session's own context and nowhere else.

The contract already has the right object and the corpus shows it starved: 26
phase-summaries in 805 events. So make that kind *do* something and ask for it
harder. A `phase-summary` may carry `since:` in its front matter, and then
*stands for* every entry between that moment and its own `ts` — becoming a fold
row in the timeline. The skill names the triggers: a phase closing, `STOP`, and
a new inbox keyword **`SUMMARISE`**, whose reply is a `phase-summary` rather
than an ack, because the file is the receipt. Return to forty new rows, send
one word, get a page back written by the party that knows.

Channels that ignore the new rule look exactly as they do today — which matters,
since two channels still run hand-written v1 READMEs.

## C. Make the structure explicit — *Work spans, not message digests* *(GPT-6 Astra)*

*Claim: the problem is scattered evidence, not insufficient compression.* A
`Timeline | Work` switch, where the unit is a **span**: a question and its
replies, a phase and its checkpoints, an error and its resolution.

```
● OPEN       Repair export failure       4 updates
● REPLIED    Can we resume the import?   2 updates
  CLOSED     Backfill customer records   9 updates
```

Spans are recognised from *explicit relationships only* — never keyword
similarity or time proximity. An ack joins by the exact filename it cites; for
everything else, two optional contract lines, `Work-Item:` naming the
initiating event and `Work-State: open|closed`. Sharp detail: an ack proves
receipt, not completion, so it shows `REPLIED` and never `CLOSED`. Selecting a
span does not mark its members read.

## D. Generate it, carefully — *The Catch-Up Line* *(Sonnet 5)*

The one proposal that takes the issue at face value, and spends its effort on
making that safe. An opt-in **one-line** strip above the timeline, only when
unread reaches 8, generated once per unread-fingerprint change by Haiku 4.5
over at most the 40 newest unread events. ~$0.008–0.03 per call, under $3/month
across all ten channels. Cached in the state directory, never in the channel.

Its central concern is the one that matters most here: **a generated sentence
must be impossible to mistake for something a session wrote**, because the
entire product is about trusting what a channel says. So it is a distinct
widget with a distinct label, never selectable, never quotable by an ack, and
absent entirely if you inspect the raw folder. It degrades to silence with no
key, to a stale-tagged cached sentence on failure, and it never influences
alerts — those stay wired to parsed STATUS and HEARTBEAT fields only.

## Two findings that fell out of the exercise

- **`TASK_OVERVIEW.md` is carried as a pinned `Doc` and its progress table is
  never parsed.** The repo's own convention says every key the contract
  advertises has to be read somewhere. This is a documented file whose content
  reaches no widget.
- **26 phase-summaries in 805 events.** Whatever is built in the viewer, the
  cheapest real improvement for a 236-event channel may be upstream, in what
  the skill asks sessions to write.

## What is not settled

Nobody addressed what a summary means for a channel being read *hours late* —
the disclosure-ageing problem found separately this week, where "back after a
6h55m pause" is true when written and false an hour later. A folded or
generated summary has the same failure mode and none of these proposals names
it.
