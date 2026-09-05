"""Human-scale time formatting.

Everything in a channel is minutes-to-days old, and the questions are always
relative ones -- how long ago, how long until -- so absolute timestamps are the
fallback rather than the default.
"""

from __future__ import annotations

from datetime import datetime, timedelta

_UNITS = ((86400, "d"), (3600, "h"), (60, "m"))


def duration(delta: timedelta, *, precise: bool = False) -> str:
    """``2h 05m``-style duration; ``precise`` keeps the second unit."""
    seconds = int(abs(delta).total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    for index, (size, suffix) in enumerate(_UNITS):
        if seconds >= size:
            whole, rest = divmod(seconds, size)
            if precise and index + 1 < len(_UNITS):
                nxt_size, nxt_suffix = _UNITS[index + 1]
                if rest >= nxt_size:
                    return f"{whole}{suffix} {rest // nxt_size:02d}{nxt_suffix}"
            return f"{whole}{suffix}"
    return f"{seconds}s"


def ago(when: datetime | None, now: datetime) -> str:
    """``3m ago`` / ``in 12m`` / ``now``, tolerant of a missing timestamp."""
    if when is None:
        return "—"
    delta = now - when
    if abs(delta) < timedelta(seconds=45):
        return "now"
    return f"{duration(delta)} ago" if delta > timedelta(0) else f"in {duration(-delta)}"


def clock(when: datetime | None, now: datetime | None = None) -> str:
    """``06:27`` for today, ``Sep 04 23:25`` once it is not today."""
    if when is None:
        return "—"
    local = when.astimezone()
    if now and local.date() == now.astimezone().date():
        return f"{local:%H:%M}"
    return f"{local:%b %d %H:%M}"


def countdown(deadline: datetime | None, now: datetime) -> str:
    """Time left before a promised update, or how far past it we are."""
    if deadline is None:
        return "—"
    remaining = deadline - now
    if remaining > timedelta(0):
        return f"due in {duration(remaining, precise=True)}"
    return f"overdue {duration(remaining, precise=True)}"
