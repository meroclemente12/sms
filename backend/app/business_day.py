"""Business days: which date a moment belongs to at a branch.

A branch open 1 PM to 1 AM sells on one "day" that crosses midnight. Each branch has a
``day_starts_at`` time, during its closed hours: everything from that time until the
same time the next morning belongs to one business date (the day the branch opened).
For a branch that closes before midnight this is the same as the calendar date.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models import Branch

DEFAULT_DAY_START = time(6, 0)
DAY = timedelta(days=1)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _gap(a: time, b: time) -> int:
    """Minutes from a to b going forward on the clock (0 to 1439)."""
    return (_minutes(b) - _minutes(a)) % (24 * 60)


def is_open_all_day(opens_at: time, closes_at: time) -> bool:
    return _minutes(opens_at) == _minutes(closes_at)


def fits_closed_hours(day_start: time, opens_at: time, closes_at: time) -> bool:
    """Is day_start between closing and the next opening (either end allowed)?"""
    if is_open_all_day(opens_at, closes_at):
        return _minutes(day_start) == _minutes(opens_at)
    return _gap(closes_at, day_start) <= _gap(closes_at, opens_at)


def suggest_day_start(opens_at: time, closes_at: time) -> time:
    """6 AM when the branch is closed then, otherwise the middle of the closed hours."""
    if fits_closed_hours(DEFAULT_DAY_START, opens_at, closes_at):
        return DEFAULT_DAY_START
    if is_open_all_day(opens_at, closes_at):
        return time(opens_at.hour, opens_at.minute)
    mid = (_minutes(closes_at) + _gap(closes_at, opens_at) // 2) % (24 * 60)
    return time(mid // 60, (mid % 60) // 15 * 15)


def _tz(branch: Branch) -> ZoneInfo:
    return ZoneInfo(branch.timezone)


def _day_start(branch: Branch) -> time:
    return branch.day_starts_at or time(0)


def business_date(branch: Branch, moment: datetime) -> date:
    """The business date a moment belongs to at this branch."""
    local = moment.astimezone(_tz(branch)).replace(tzinfo=None)
    start = _day_start(branch)
    return (local - timedelta(hours=start.hour, minutes=start.minute)).date()


def day_bounds(branch: Branch, day: date) -> tuple[datetime, datetime]:
    """[start, end) of one business day: day_starts_at until the same time next day."""
    tz = _tz(branch)
    start = _day_start(branch)
    return (
        datetime.combine(day, start, tzinfo=tz),
        datetime.combine(day + DAY, start, tzinfo=tz),
    )


def days_bounds(branch: Branch, first: date, last: date) -> tuple[datetime, datetime]:
    """[start, end) covering the business days first..last."""
    return day_bounds(branch, first)[0], day_bounds(branch, last)[1]


def opening_hours(branch: Branch, day: date) -> tuple[datetime, datetime]:
    """When the branch opens and closes on a business day (closing may be after midnight)."""
    tz = _tz(branch)
    start = _day_start(branch)
    begin = datetime.combine(day, start)
    opens = begin + timedelta(minutes=_gap(start, branch.opens_at))
    length = _gap(branch.opens_at, branch.closes_at) or 24 * 60
    closes = opens + timedelta(minutes=length)
    return opens.replace(tzinfo=tz), closes.replace(tzinfo=tz)
