"""The shapes everything else operates on.

Dataclasses, not an ORM. This package computes from these shapes; it does not
persist them -- a caller owns storage and hands these in, the same contract
Arrival's feature store uses for the same reason: keeping the maths free of
a database import is what makes it unit-testable in milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class ShareClassKind(Enum):
    COMMON = "common"
    PREFERRED = "preferred"


@dataclass(frozen=True)
class ShareClass:
    name: str                       # e.g. "Series A Preferred"
    kind: ShareClassKind
    # Liquidation preference, as a multiple of money invested. 1.0 is standard;
    # irrelevant to vesting or dilution, kept here because a cap table that
    # cannot answer "what does an investor get back first" is not a cap table,
    # it's a share-count spreadsheet.
    liquidation_multiple: float = 1.0


@dataclass(frozen=True)
class Stakeholder:
    stakeholder_id: str
    name: str
    kind: str                       # "founder" | "employee" | "investor"


@dataclass(frozen=True)
class VestingSchedule:
    """Standard startup vesting: a cliff, then linear monthly vesting.

    cliff_months=12, total_months=48 is the default nearly every term sheet
    uses: nothing vests for a year, then 25% vests at the cliff instant, then
    the remaining 75% vests in equal monthly slices for the next 36 months.
    Encoding the cliff as "zero until month N, then a jump" rather than
    "linear from day one" matters: the two produce the same total at month 48
    but completely different numbers at month 11 versus month 13, and that
    boundary is exactly where a dispute happens.
    """
    cliff_months: int = 12
    total_months: int = 48
    # Single-trigger acceleration: 100% vests immediately on a qualifying exit,
    # regardless of elapsed time. Double-trigger (exit AND termination) is the
    # more common real-world clause but needs a termination date this model
    # does not track; single-trigger is the honest subset to implement.
    accelerates_on_exit: bool = False


@dataclass(frozen=True)
class Grant:
    grant_id: str
    stakeholder_id: str
    share_class: str                # ShareClass.name
    total_shares: int
    grant_date: date
    vesting: VestingSchedule
    # Carried, never used by the vesting maths. A quality gate checks it
    # against total_shares instead, because a strike price that silently
    # disagreed with the share count is the kind of row that passes every
    # other check and is wrong anyway.
    strike_price: float = 0.0


@dataclass(frozen=True)
class FundingRound:
    round_id: str
    name: str                       # "Seed", "Series A", ...
    date: date
    new_investor_id: str
    shares_issued: int
    price_per_share: float
    share_class: str


@dataclass
class CapTable:
    """A company at a point in time: who holds what, and the rounds behind it."""
    company_name: str
    share_classes: dict[str, ShareClass] = field(default_factory=dict)
    stakeholders: dict[str, Stakeholder] = field(default_factory=dict)
    grants: list[Grant] = field(default_factory=list)
    funding_rounds: list[FundingRound] = field(default_factory=list)
