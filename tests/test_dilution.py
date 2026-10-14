from datetime import date

import pytest

from pangolin.dilution import dilution_impact, ownership_as_of, simulate_round
from pangolin.models import CapTable, FundingRound, Grant, VestingSchedule

NO_VEST = VestingSchedule(cliff_months=0, total_months=0)  # irrelevant to dilution


def _founders_table() -> CapTable:
    table = CapTable(company_name="Testco")
    table.grants = [
        Grant("G-1", "founder-a", "Common", 5_000_000, date(2023, 1, 1), NO_VEST),
        Grant("G-2", "founder-b", "Common", 5_000_000, date(2023, 1, 1), NO_VEST),
    ]
    return table


def test_two_equal_founders_split_evenly():
    rows = ownership_as_of(_founders_table(), as_of=date(2023, 6, 1))
    pcts = {r.stakeholder_id: r.ownership_pct for r in rows}
    assert pcts["founder-a"] == pytest.approx(0.5)
    assert pcts["founder-b"] == pytest.approx(0.5)


def test_empty_table_has_no_ownership_rows():
    assert ownership_as_of(CapTable(company_name="Empty"), as_of=date.today()) == []


def test_new_round_dilutes_existing_holders_proportionally():
    table = _founders_table()
    seed = FundingRound(
        round_id="R-1", name="Seed", date=date(2024, 1, 1),
        new_investor_id="investor-x", shares_issued=2_000_000,
        price_per_share=1.0, share_class="Preferred",
    )
    rows = simulate_round(table, seed)
    pcts = {r.stakeholder_id: r.ownership_pct for r in rows}

    # 5M / 12M each for the founders, 2M / 12M for the new investor.
    assert pcts["founder-a"] == pytest.approx(5 / 12)
    assert pcts["founder-b"] == pytest.approx(5 / 12)
    assert pcts["investor-x"] == pytest.approx(2 / 12)
    # Dilution preserves relative proportion between existing holders: they
    # were equal before, they stay equal after, even though both dropped.
    assert pcts["founder-a"] == pcts["founder-b"]


def test_simulate_round_does_not_mutate_the_original_table():
    table = _founders_table()
    original_round_count = len(table.funding_rounds)
    seed = FundingRound("R-1", "Seed", date(2024, 1, 1), "investor-x",
                        2_000_000, 1.0, "Preferred")
    simulate_round(table, seed)
    assert len(table.funding_rounds) == original_round_count


def test_dilution_impact_is_negative_for_existing_holders_positive_for_new():
    table = _founders_table()
    seed = FundingRound("R-1", "Seed", date(2024, 1, 1), "investor-x",
                        2_000_000, 1.0, "Preferred")
    impact = dilution_impact(table, seed)

    assert impact["founder-a"] < 0
    assert impact["founder-b"] < 0
    assert impact["investor-x"] > 0
    # Percentage points gained by the new investor equal those lost by
    # everyone else combined - dilution redistributes, it does not create.
    assert impact["founder-a"] + impact["founder-b"] + impact["investor-x"] == pytest.approx(0, abs=1e-9)


def test_sequential_rounds_compound():
    table = _founders_table()
    seed = FundingRound("R-1", "Seed", date(2024, 1, 1), "investor-x",
                        2_000_000, 1.0, "Preferred")
    table.funding_rounds.append(seed)
    series_a = FundingRound("R-2", "Series A", date(2025, 1, 1), "investor-y",
                            3_000_000, 5.0, "Preferred")
    rows = ownership_as_of(table, as_of=date(2025, 6, 1))
    pcts = {r.stakeholder_id: r.ownership_pct for r in rows}
    # Before series A: 12M total, confirms seed round already applied.
    assert pcts["investor-x"] == pytest.approx(2 / 12)

    after_series_a = simulate_round(table, series_a)
    pcts_a = {r.stakeholder_id: r.ownership_pct for r in after_series_a}
    # 15M total now; investor-x's stake is diluted again by the new round
    # even though they did not participate in it.
    assert pcts_a["investor-x"] == pytest.approx(2 / 15)
    assert pcts_a["investor-y"] == pytest.approx(3 / 15)
