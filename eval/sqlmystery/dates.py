"""Dates are integers YYYYMMDD; times are integers HHMM."""
from __future__ import annotations

import datetime as _dt
import random


def to_int(d: _dt.date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def from_int(i: int) -> _dt.date:
    return _dt.date(i // 10000, (i // 100) % 100, i % 100)


def add_days(i: int, n: int) -> int:
    return to_int(from_int(i) + _dt.timedelta(days=n))


def days_between(a: int, b: int) -> int:
    return (from_int(b) - from_int(a)).days


def rand_date(rng: random.Random, start: int, end: int) -> int:
    return add_days(start, rng.randint(0, days_between(start, end)))


def month_bounds(i: int) -> tuple[int, int]:
    d = from_int(i)
    first = d.replace(day=1)
    nxt = (first + _dt.timedelta(days=32)).replace(day=1)
    return to_int(first), to_int(nxt - _dt.timedelta(days=1))


def iso(i: int) -> str:
    return from_int(i).isoformat()


def human(i: int) -> str:
    d = from_int(i)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def month_name(i: int) -> str:
    d = from_int(i)
    return f"{d.strftime('%B')} {d.year}"


def rand_time(rng: random.Random, lo: int = 600, hi: int = 2300) -> int:
    h = rng.randint(lo // 100, hi // 100)
    m = rng.randint(0, 59)
    return h * 100 + m


def add_minutes(t: int, n: int) -> int:
    total = (t // 100) * 60 + (t % 100) + n
    total = max(0, min(total, 23 * 60 + 59))
    return (total // 60) * 100 + total % 60


def human_time(t: int) -> str:
    h, m = t // 100, t % 100
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}{suffix}"
