from datetime import date

import pytest

from pangolin import quality
from pangolin.dilution import OwnershipRow
from pangolin.models import (
    CapTable,
    FundingRound,
    Grant,
    Stakeholder,
    VestingSchedule,
)
from pangolin.quality import (
    GateFailure,
    GateReport,
    check_funding_rounds_chronological,
    check_no_duplicate_grant_ids,
    check_no_grant_before_founding,
    check_no_negative_or_zero_shares,
    check_no_orphan_grants,
    check_ownership_sums_to_one,
    check_vesting_schedule_sane,
    run_all_gates,
)

STANDARD = VestingSchedule(cliff_months=12, total_months=48)


def _stakeholder(stakeholder_id: str) -> Stakeholder:
    return Stakeholder(stakeholder_id=stakeholder_id, name=stakeholder_id, kind="founder")


def _grant(grant_id: str, stakeholder_id: str, total_shares: int = 1_000_000,
           grant_date: date = date(2023, 1, 1), vesting: VestingSchedule = STANDARD) -> Grant:
    return Grant(grant_id, stakeholder_id, "Common", total_shares, grant_date, vesting)


def _clean_table() -> CapTable:
    table = CapTable(company_name="Testco")
    table.stakeholders = {
        "founder-a": _stakeholder("founder-a"),
        "founder-b": _stakeholder("founder-b"),
    }
    table.grants = [
        _grant("G-1", "founder-a", 5_000_000),
        _grant("G-2", "founder-b", 5_000_000),
    ]
    return table


# --- check_no_orphan_grants ---


def test_no_orphan_grants_passes_when_every_stakeholder_exists():
    report = GateReport()
    check_no_orphan_grants(_clean_table(), report)
    assert report.passed()


def test_no_orphan_grants_catches_unknown_stakeholder_id():
    table = _clean_table()
    table.grants.append(_grant("G-3", "ghost-stakeholder"))
    report = GateReport()
    check_no_orphan_grants(table, report)
    assert not report.passed()
    assert "G-3" in report.failures[0].detail


# --- check_no_duplicate_grant_ids ---


def test_no_duplicate_grant_ids_passes_when_ids_are_unique():
    report = GateReport()
    check_no_duplicate_grant_ids(_clean_table(), report)
    assert report.passed()


def test_no_duplicate_grant_ids_catches_repeated_grant_id():
    table = _clean_table()
    table.grants.append(_grant("G-1", "founder-b"))  # reuses G-1
    report = GateReport()
    check_no_duplicate_grant_ids(table, report)
    assert not report.passed()
    assert "G-1" in report.failures[0].detail


# --- check_no_negative_or_zero_shares ---


def test_no_negative_or_zero_shares_passes_for_positive_shares():
    report = GateReport()
    check_no_negative_or_zero_shares(_clean_table(), report)
    assert report.passed()


def test_no_negative_or_zero_shares_catches_zero_share_grant():
    table = _clean_table()
    table.grants.append(_grant("G-3", "founder-a", total_shares=0))
    report = GateReport()
    check_no_negative_or_zero_shares(table, report)
    assert not report.passed()
    assert "G-3" in report.failures[0].detail


# --- check_vesting_schedule_sane ---


def test_vesting_schedule_sane_passes_for_standard_schedule():
    report = GateReport()
    check_vesting_schedule_sane(_clean_table(), report)
    assert report.passed()


def test_vesting_schedule_sane_catches_cliff_longer_than_total():
    table = _clean_table()
    table.grants.append(_grant("G-3", "founder-a",
                                vesting=VestingSchedule(cliff_months=36, total_months=24)))
    report = GateReport()
    check_vesting_schedule_sane(table, report)
    assert not report.passed()
    assert "G-3" in report.failures[0].detail


# --- check_ownership_sums_to_one ---


def test_ownership_sums_to_one_passes_for_consistent_table():
    report = GateReport()
    check_ownership_sums_to_one(_clean_table(), date(2023, 6, 1), report)
    assert report.passed()


def test_ownership_sums_to_one_catches_percentages_that_do_not_add_up(monkeypatch):
    # ownership_as_of always sums to 1.0 by construction (it divides each
    # holder's shares by the same total), so the only way to exercise the
    # gate's own arithmetic is to hand it a broken result directly rather
    # than try to coerce a correct function into producing a wrong answer.
    monkeypatch.setattr(quality, "ownership_as_of",
                         lambda table, as_of: [OwnershipRow("founder-a", 100, 0.5)])
    report = GateReport()
    check_ownership_sums_to_one(_clean_table(), date(2023, 6, 1), report)
    assert not report.passed()
    assert "0.5" in report.failures[0].detail


def test_ownership_sums_to_one_passes_trivially_on_empty_table():
    report = GateReport()
    check_ownership_sums_to_one(CapTable(company_name="Empty"), date(2023, 6, 1), report)
    assert report.passed()


# --- check_no_grant_before_founding ---


def test_no_grant_before_founding_passes_when_grants_are_after_founding():
    report = GateReport()
    check_no_grant_before_founding(_clean_table(), date(2022, 1, 1), report)
    assert report.passed()


def test_no_grant_before_founding_catches_backdated_grant():
    table = _clean_table()
    table.grants.append(_grant("G-3", "founder-a", grant_date=date(2021, 1, 1)))
    report = GateReport()
    check_no_grant_before_founding(table, date(2022, 1, 1), report)
    assert not report.passed()
    assert "G-3" in report.failures[0].detail


# --- check_funding_rounds_chronological ---


def test_funding_rounds_chronological_passes_for_increasing_prices():
    table = _clean_table()
    table.funding_rounds = [
        FundingRound("R-1", "Seed", date(2023, 6, 1), "investor-x", 1_000_000, 1.0, "Preferred"),
        FundingRound("R-2", "Series A", date(2024, 6, 1), "investor-y", 1_000_000, 5.0, "Preferred"),
    ]
    report = GateReport()
    check_funding_rounds_chronological(table, report)
    assert report.passed()


def test_funding_rounds_chronological_flags_down_round_without_blocking():
    table = _clean_table()
    table.funding_rounds = [
        FundingRound("R-1", "Seed", date(2023, 6, 1), "investor-x", 1_000_000, 5.0, "Preferred"),
        FundingRound("R-2", "Series A", date(2024, 6, 1), "investor-y", 1_000_000, 2.0, "Preferred"),
    ]
    report = GateReport()
    check_funding_rounds_chronological(table, report)
    # A down round is unusual, worth surfacing, but legitimate: it is a
    # warning, so it must not count as a gate failure.
    assert report.passed()
    assert len(report.warnings) == 1
    assert "R-2" in report.warnings[0].detail


# --- GateReport / GateFailure ---


def test_raise_if_failed_raises_gate_failure_with_detail_in_message():
    table = _clean_table()
    table.grants.append(_grant("G-3", "ghost"))
    report = GateReport()
    check_no_orphan_grants(table, report)
    with pytest.raises(GateFailure) as excinfo:
        report.raise_if_failed()
    assert "G-3" in str(excinfo.value)


def test_raise_if_failed_does_not_raise_for_warnings_only():
    table = _clean_table()
    table.funding_rounds = [
        FundingRound("R-1", "Seed", date(2023, 6, 1), "investor-x", 1_000_000, 5.0, "Preferred"),
        FundingRound("R-2", "Series A", date(2024, 6, 1), "investor-y", 1_000_000, 2.0, "Preferred"),
    ]
    report = GateReport()
    check_funding_rounds_chronological(table, report)
    report.raise_if_failed()  # must not raise: the only record is a warning


# --- run_all_gates ---


def test_run_all_gates_passes_on_a_clean_table():
    report = run_all_gates(_clean_table(), as_of=date(2023, 6, 1), founding_date=date(2022, 1, 1))
    assert report.passed()
    assert len(report.records) == 7


def test_run_all_gates_reports_every_defect_in_a_broken_table():
    table = _clean_table()
    table.grants.append(_grant("G-3", "ghost", total_shares=0, grant_date=date(2020, 1, 1)))
    report = run_all_gates(table, as_of=date(2023, 6, 1), founding_date=date(2022, 1, 1))
    assert not report.passed()
    failed_checks = {r.check_name for r in report.failures}
    assert "no_orphan_grants" in failed_checks
    assert "no_negative_or_zero_shares" in failed_checks
    assert "no_grant_before_founding" in failed_checks
