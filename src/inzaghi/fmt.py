"""Human-scale formatting of the two quantities a channel deals in: time, and
the size of a file it delivered.

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


def next_change(when: datetime | None, now: datetime) -> datetime | None:
    """When a label showing ``when`` relative to ``now`` would first read differently.

    The rows carry relative times, so they go stale on their own schedule
    rather than when anything happened: "4m ago" is wrong a minute later and
    right until then.  This says when that moment is, so a poll that found no
    new files can leave the rows alone instead of rebuilding them to print the
    same words -- which is most polls, since a label changes once a minute and
    the poll runs every two seconds.

    Mirrors ``ago`` and ``duration`` bucket for bucket; get one wrong and a row
    freezes.  ``None`` when there is nothing to go stale.
    """
    if when is None:
        return None
    age = (now - when).total_seconds()
    if age < 0:  # in the future: counts down every second
        return now + timedelta(seconds=1)
    if age < 45:
        return when + timedelta(seconds=45)  # "now" until then
    if age < 60:
        return now + timedelta(seconds=1)  # seconds, ticking
    # The same buckets ``duration`` picks from, and the one it is in decides
    # when the number it prints goes up.
    for size, until in ((60, 3600), (3600, 86400), (86400, None)):
        if until is None or age < until:
            return when + timedelta(seconds=(int(age // size) + 1) * size)
    return None


def next_midnight(now: datetime) -> datetime:
    """Local midnight after ``now``: when ``clock`` starts dating a timestamp."""
    local = now.astimezone()
    return (local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


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


_SCALES = ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "kB"))


def size(byte_count: int | None) -> str:
    """``2.4 MB`` -- one decimal place, because that is all anyone reads."""
    if byte_count is None:
        return "—"
    for scale, suffix in _SCALES:
        if byte_count >= scale:
            return f"{byte_count / scale:.1f} {suffix}"
    return f"{byte_count} B"
