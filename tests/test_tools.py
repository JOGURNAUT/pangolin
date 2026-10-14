from dataclasses import asdict
from datetime import date

import pytest

from ai.tools import tool_ownership, tool_simulate_round, tool_vesting_status
from pangolin.dilution import dilution_impact, ownership_as_of, simulate_round
from pangolin.models import CapTable, FundingRound, Grant, Stakeholder, VestingSchedule
from pangolin.vesting import vested_shares

STANDARD = VestingSchedule(cliff_months=12, total_months=48)


def _table() -> CapTable:
    table = CapTable(company_name="Testco")
    table.stakeholders = {
        "founder-a": Stakeholder("founder-a", "Alice Founder", "founder"),
        "employee-b": Stakeholder("employee-b", "Bob Employee", "employee"),
    }
    table.grants = [
        Grant("G-1", "founder-a", "Common", 4_000_000, date(2022, 1, 1), STANDARD),
        Grant("G-2", "employee-b", "Common", 40_000, date(2023, 1, 1), STANDARD),
        # A refresh grant: same stakeholder, second grant.
        Grant("G-3", "employee-b", "Common", 20_000, date(2024, 1, 1), STANDARD),
    ]
    return table


def test_tool_vesting_status_matches_vested_shares_directly():
    table = _table()
    as_of = date(2024, 6, 1)
    grant = table.grants[0]

    direct = vested_shares(grant, as_of)
    result = tool_vesting_status(table, "founder-a", as_of)

    assert result["vested_shares"] == direct.vested_shares
    assert result["unvested_shares"] == direct.unvested_shares
    assert result["total_shares"] == grant.total_shares
    assert len(result["grants"]) == 1
    assert result["grants"][0]["vested_shares"] == direct.vested_shares


def test_tool_vesting_status_sums_multiple_grants():
    table = _table()
    as_of = date(2025, 6, 1)

    direct_total_vested = sum(
        vested_shares(g, as_of).vested_shares
        for g in table.grants if g.stakeholder_id == "employee-b"
    )
    result = tool_vesting_status(table, "employee-b", as_of)

    assert len(result["grants"]) == 2
    assert result["vested_shares"] == direct_total_vested


def test_tool_vesting_status_unknown_stakeholder_raises():
    table = _table()
    with pytest.raises(ValueError, match="no-such-id"):
        tool_vesting_status(table, "no-such-id", date(2024, 1, 1))


def test_tool_ownership_matches_ownership_as_of_directly():
    table = _table()
    as_of = date(2025, 1, 1)

    direct = [asdict(row) for row in ownership_as_of(table, as_of)]
    result = tool_ownership(table, as_of)

    assert result == direct


def test_tool_simulate_round_matches_direct_calls():
    table = _table()
    as_of = date(2025, 6, 1)

    hypothetical = FundingRound(
        round_id="SIM-Seed", name="Seed", date=as_of,
        new_investor_id="investor-x", shares_issued=1_000_000,
        price_per_share=2.0, share_class="Preferred",
    )
    direct_post_round = [asdict(row) for row in simulate_round(table, hypothetical)]
    direct_impact = dilution_impact(table, hypothetical)

    result = tool_simulate_round(
        table, round_name="Seed", date=as_of, new_investor_id="investor-x",
        shares_issued=1_000_000, price_per_share=2.0, share_class="Preferred",
    )

    assert result["post_round_ownership"] == direct_post_round
    assert result["dilution_impact_pct_points"] == direct_impact
