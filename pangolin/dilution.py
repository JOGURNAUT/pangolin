"""Ownership percentage: whose, and how a new round moves it.

Ownership here is fully diluted -- every granted share counts toward the
denominator whether it has vested or not. That is the convention term sheets
use ("20% fully diluted"), and the alternative (counting only vested shares)
would make a stakeholder's percentage drift upward every month as their own
unvested shares vest, with no transaction having happened. A percentage that
moves without an event is not safe to read off a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import CapTable, FundingRound


@dataclass(frozen=True)
class OwnershipRow:
    stakeholder_id: str
    shares: int
    ownership_pct: float


def _shares_as_of(table: CapTable, as_of: date) -> dict[str, int]:
    """Total granted shares per stakeholder, from grants issued by this date.

    Not vested shares -- granted shares. Dilution is about who holds how much
    of the company, which happens at grant time; vesting governs when a
    stakeholder can keep those shares if they leave, a different question this
    function is not answering.
    """
    totals: dict[str, int] = {}
    for grant in table.grants:
        if grant.grant_date <= as_of:
            totals[grant.stakeholder_id] = totals.get(grant.stakeholder_id, 0) + grant.total_shares
    for round_ in table.funding_rounds:
        if round_.date <= as_of:
            totals[round_.new_investor_id] = (
                totals.get(round_.new_investor_id, 0) + round_.shares_issued)
    return totals


def ownership_as_of(table: CapTable, as_of: date) -> list[OwnershipRow]:
    """Fully diluted ownership for every stakeholder who holds shares by this date."""
    totals = _shares_as_of(table, as_of)
    total_shares = sum(totals.values())
    if total_shares == 0:
        return []
    rows = [
        OwnershipRow(sid, shares, shares / total_shares)
        for sid, shares in totals.items()
    ]
    return sorted(rows, key=lambda r: r.ownership_pct, reverse=True)


def simulate_round(table: CapTable, hypothetical_round: FundingRound) -> list[OwnershipRow]:
    """What ownership looks like if this round happened, without mutating the table.

    Takes a round dated after every existing grant and round, or the ordering
    `_shares_as_of` relies on (issue first, dilute after) stops meaning
    anything. The caller is responsible for a sane date; this function does
    not guess one on their behalf, because a guessed date is a wrong answer
    that looks like a right one.
    """
    scratch = CapTable(
        company_name=table.company_name,
        share_classes=dict(table.share_classes),
        stakeholders=dict(table.stakeholders),
        grants=list(table.grants),
        funding_rounds=[*table.funding_rounds, hypothetical_round],
    )
    return ownership_as_of(scratch, as_of=hypothetical_round.date)


def dilution_impact(table: CapTable, hypothetical_round: FundingRound) -> dict[str, float]:
    """Percentage-point change for each existing stakeholder from one new round.

    Negative for everyone who isn't the new investor -- that is the point of
    the function. A pitch that shows the post-round table alone invites
    reading "my shares are safe" into a number that only ever talks about
    percentage, never share count, which does not change.
    """
    before = {r.stakeholder_id: r.ownership_pct
             for r in ownership_as_of(table, as_of=hypothetical_round.date)}
    after = {r.stakeholder_id: r.ownership_pct
             for r in simulate_round(table, hypothetical_round)}
    return {
        sid: after.get(sid, 0.0) - before.get(sid, 0.0)
        for sid in set(before) | set(after)
    }
