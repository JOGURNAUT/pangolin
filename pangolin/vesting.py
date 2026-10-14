"""How many of a grant's shares are actually owned, as of any date.

The one rule that makes this worth writing carefully: before the cliff, vested
shares are exactly zero, not a small linear fraction. A grant one day before
its cliff and one day after differ by 25% of the total grant, not by one day's
worth of vesting. Code that treats vesting as uniformly linear from day one
gets every pre-cliff answer wrong and the error is invisible until someone
checks a termination that happened at month 11.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import Grant


def _months_between(start: date, end: date) -> float:
    """Elapsed months, fractional, calendar-aware rather than a 30-day guess.

    `(end - start).days / 30` drifts: 48 "months" of 30 days each is 1,440
    days, four years is 1,461-1,462. Over a 4-year schedule that gap is most
    of a month -- enough to vest someone a few weeks early or late depending
    on which direction the approximation leans. Counting whole months from
    the day-of-month, then adding the remainder as a day fraction, tracks the
    calendar a vesting schedule is actually written against.
    """
    if end < start:
        return 0.0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    # Remainder: days into the current partial month, as a fraction of it.
    anchor_month = start.month + months
    anchor_year = start.year + (anchor_month - 1) // 12
    anchor_month = (anchor_month - 1) % 12 + 1
    anchor_day = min(start.day, _days_in_month(anchor_year, anchor_month))
    anchor = date(anchor_year, anchor_month, anchor_day)
    next_anchor_month = anchor_month + 1
    next_anchor_year = anchor_year + (next_anchor_month - 1) // 12
    next_anchor_month = (next_anchor_month - 1) % 12 + 1
    next_anchor_day = min(start.day, _days_in_month(next_anchor_year, next_anchor_month))
    next_anchor = date(next_anchor_year, next_anchor_month, next_anchor_day)
    span = (next_anchor - anchor).days
    partial = (end - anchor).days / span if span else 0.0
    return months + max(0.0, min(1.0, partial))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return (date(year + 1, 1, 1) - date(year, 12, 1)).days
    return (date(year, month + 1, 1) - date(year, month, 1)).days


@dataclass(frozen=True)
class VestingResult:
    grant_id: str
    vested_shares: int
    unvested_shares: int
    is_fully_vested: bool
    cliff_reached: bool


def vested_shares(grant: Grant, as_of: date, exit_triggered: bool = False) -> VestingResult:
    """Vested share count for one grant, as of one date.

    `exit_triggered` is a separate argument rather than a field on the grant,
    because whether an exit happened is a fact about the company on a given
    day, not a fact about any individual grant -- encoding it per-grant would
    let one grant disagree with another about whether the company has been
    acquired.
    """
    sched = grant.vesting

    if exit_triggered and sched.accelerates_on_exit:
        return VestingResult(grant.grant_id, grant.total_shares, 0, True, True)

    elapsed = _months_between(grant.grant_date, as_of)

    if elapsed < sched.cliff_months:
        return VestingResult(grant.grant_id, 0, grant.total_shares, False, False)

    if elapsed >= sched.total_months:
        return VestingResult(grant.grant_id, grant.total_shares, 0, True, True)

    # At the cliff instant, the cliff fraction vests as a step, not a ramp.
    # From the cliff to the end, the remaining shares vest linearly per month.
    cliff_fraction = sched.cliff_months / sched.total_months
    post_cliff_elapsed = elapsed - sched.cliff_months
    post_cliff_total = sched.total_months - sched.cliff_months
    post_cliff_fraction = post_cliff_elapsed / post_cliff_total if post_cliff_total else 0.0

    total_fraction = cliff_fraction + (1 - cliff_fraction) * post_cliff_fraction
    vested = int(grant.total_shares * total_fraction)
    # Clamp rather than trust the fraction arithmetic at the boundary: floating
    # point can put total_fraction at 1.0000000002, which would vest one more
    # share than the grant has.
    vested = max(0, min(vested, grant.total_shares))

    return VestingResult(
        grant_id=grant.grant_id,
        vested_shares=vested,
        unvested_shares=grant.total_shares - vested,
        is_fully_vested=False,
        cliff_reached=True,
    )
