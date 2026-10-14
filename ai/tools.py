"""The only functions an LLM is allowed to call for a number.

Every function here is a thin, honest wrapper around `pangolin.vesting` or
`pangolin.dilution`: it builds whatever input shape the real engine wants,
calls the real engine, and reshapes the real result into a plain dict (so it
serializes straight into a tool-call result for a model). None of these
functions do arithmetic of their own beyond counting and summing numbers the
engine already produced. That restriction is the entire point of this module:
an LLM deciding "call tool_vesting_status with stakeholder_id=X" can still
only ever report what `vested_shares` actually computed, because nothing in
this file gives it another number to report instead.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from pangolin.dilution import dilution_impact, ownership_as_of, simulate_round
from pangolin.models import CapTable, FundingRound
from pangolin.vesting import vested_shares


def tool_vesting_status(table: CapTable, stakeholder_id: str, as_of: date) -> dict:
    """Vested/unvested/total shares for one stakeholder, as of one date.

    Sums `vested_shares` across every grant that stakeholder holds; a founder
    or employee can have more than one grant (a refresh grant alongside the
    original, for instance), and a status report that only looked at the
    first one would understate their real position.
    """
    if stakeholder_id not in table.stakeholders:
        raise ValueError(
            f"no stakeholder '{stakeholder_id}' in this cap table"
        )

    grants = [g for g in table.grants if g.stakeholder_id == stakeholder_id]

    breakdown = []
    total_vested = 0
    total_unvested = 0
    for grant in grants:
        result = vested_shares(grant, as_of)
        breakdown.append({
            "grant_id": result.grant_id,
            "share_class": grant.share_class,
            "total_shares": grant.total_shares,
            "vested_shares": result.vested_shares,
            "unvested_shares": result.unvested_shares,
            "is_fully_vested": result.is_fully_vested,
            "cliff_reached": result.cliff_reached,
        })
        total_vested += result.vested_shares
        total_unvested += result.unvested_shares

    return {
        "stakeholder_id": stakeholder_id,
        "as_of": as_of.isoformat(),
        "vested_shares": total_vested,
        "unvested_shares": total_unvested,
        "total_shares": total_vested + total_unvested,
        "grants": breakdown,
    }


def tool_ownership(table: CapTable, as_of: date) -> list[dict]:
    """Fully diluted ownership for every stakeholder, as of one date.

    A direct pass-through of `ownership_as_of`: the rows are already the
    answer, this just turns frozen dataclasses into plain dicts.
    """
    return [asdict(row) for row in ownership_as_of(table, as_of)]


def tool_simulate_round(
    table: CapTable,
    round_name: str,
    date: date,
    new_investor_id: str,
    shares_issued: int,
    price_per_share: float,
    share_class: str,
) -> dict:
    """Post-round ownership and per-stakeholder dilution for a hypothetical round.

    Builds the `FundingRound` the engine needs from plain arguments (an LLM
    deciding to call this tool hands over scalars, not a dataclass) and calls
    both `simulate_round` and `dilution_impact` on it, so the answer carries
    the new cap table AND the percentage-point move that caused it, since a
    round's effect is usually "who gets diluted and by how much", not just
    "what does the table look like now".
    """
    # round_id is synthetic and scoped to this simulation; it never gets
    # written back to the real table, so it only needs to be unique within
    # this one hypothetical, not globally.
    hypothetical = FundingRound(
        round_id=f"SIM-{round_name}",
        name=round_name,
        date=date,
        new_investor_id=new_investor_id,
        shares_issued=shares_issued,
        price_per_share=price_per_share,
        share_class=share_class,
    )

    post_round = [asdict(row) for row in simulate_round(table, hypothetical)]
    impact = dilution_impact(table, hypothetical)

    return {
        "round_name": round_name,
        "as_of": date.isoformat(),
        "post_round_ownership": post_round,
        "dilution_impact_pct_points": impact,
    }
