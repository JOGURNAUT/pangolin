"""Data-integrity gates a cap table must clear before anything downstream trusts it.

vesting.py and dilution.py both assume the CapTable handed to them is already
internally consistent: grants point at real stakeholders, share counts are
positive, the ownership percentages that come out actually add up to the whole
company. None of that is enforced by the dataclasses in models.py, a dataclass
will happily hold a grant for a stakeholder_id that does not exist, or a
total_shares of 0. These gates are the one place that consistency gets checked,
so a caller runs them once, right after loading or generating a table and
before handing it to the AI layer or a report, instead of trusting every
downstream function to notice a bad row on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .dilution import ownership_as_of
from .models import CapTable


@dataclass(frozen=True)
class GateRecord:
    check_name: str
    passed: bool
    detail: str
    # Non-blocking records still show up in the report, they just do not fail
    # run_all_gates: see check_funding_rounds_chronological for why a down
    # round earns a warning instead of a hard failure.
    blocking: bool = True


class GateReport:
    """Collects pass/fail records from one run of the gates below."""

    def __init__(self) -> None:
        self.records: list[GateRecord] = []

    def add(self, check_name: str, passed: bool, detail: str, blocking: bool = True) -> None:
        self.records.append(GateRecord(check_name, passed, detail, blocking))

    @property
    def failures(self) -> list[GateRecord]:
        return [r for r in self.records if not r.passed and r.blocking]

    @property
    def warnings(self) -> list[GateRecord]:
        return [r for r in self.records if not r.passed and not r.blocking]

    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        lines = []
        for r in self.records:
            if r.passed:
                status = "PASS"
            elif r.blocking:
                status = "FAIL"
            else:
                status = "WARN"
            lines.append(f"[{status}] {r.check_name}: {r.detail}")
        return "\n".join(lines)

    def raise_if_failed(self) -> None:
        if self.failures:
            raise GateFailure(self)


class GateFailure(Exception):
    """Raised by GateReport.raise_if_failed() when a blocking check failed."""

    def __init__(self, report: GateReport) -> None:
        self.report = report
        failures = report.failures
        message = "; ".join(f"{r.check_name}: {r.detail}" for r in failures)
        super().__init__(f"{len(failures)} quality gate(s) failed: {message}")


def check_no_orphan_grants(table: CapTable, report: GateReport) -> None:
    orphans = [g.grant_id for g in table.grants if g.stakeholder_id not in table.stakeholders]
    if orphans:
        report.add("no_orphan_grants", False,
                    f"grants reference unknown stakeholders: {orphans}")
    else:
        report.add("no_orphan_grants", True, "every grant's stakeholder exists")


def check_no_duplicate_grant_ids(table: CapTable, report: GateReport) -> None:
    seen: set[str] = set()
    dupes: set[str] = set()
    for g in table.grants:
        if g.grant_id in seen:
            dupes.add(g.grant_id)
        seen.add(g.grant_id)
    if dupes:
        report.add("no_duplicate_grant_ids", False,
                    f"grant_id repeated: {sorted(dupes)}")
    else:
        report.add("no_duplicate_grant_ids", True, "every grant_id is unique")


def check_no_negative_or_zero_shares(table: CapTable, report: GateReport) -> None:
    bad = [g.grant_id for g in table.grants if g.total_shares <= 0]
    if bad:
        report.add("no_negative_or_zero_shares", False,
                    f"grants with non-positive total_shares: {bad}")
    else:
        report.add("no_negative_or_zero_shares", True, "every grant has positive total_shares")


def check_vesting_schedule_sane(table: CapTable, report: GateReport) -> None:
    bad = [
        g.grant_id for g in table.grants
        if g.vesting.cliff_months < 0
        or g.vesting.total_months < 0
        or g.vesting.cliff_months > g.vesting.total_months
    ]
    if bad:
        report.add("vesting_schedule_sane", False,
                    f"grants with an impossible cliff/total_months pair: {bad}")
    else:
        report.add("vesting_schedule_sane", True,
                    "every vesting schedule has a non-negative cliff no longer than its total")


def check_ownership_sums_to_one(table: CapTable, as_of: date, report: GateReport) -> None:
    rows = ownership_as_of(table, as_of)
    total = sum(r.ownership_pct for r in rows)
    # A table with no shares issued yet has nothing to sum: that is a
    # different situation from a table with shares whose percentages fail to
    # add up, so it passes instead of failing on a sum of 0.
    if not rows:
        report.add("ownership_sums_to_one", True,
                    "no shares issued as of this date, nothing to check")
        return
    if abs(total - 1.0) > 1e-6:
        report.add("ownership_sums_to_one", False,
                    f"ownership percentages sum to {total}, not 1.0")
    else:
        report.add("ownership_sums_to_one", True,
                    f"ownership percentages sum to {total}")


def check_no_grant_before_founding(table: CapTable, founding_date: date, report: GateReport) -> None:
    bad = [g.grant_id for g in table.grants if g.grant_date < founding_date]
    if bad:
        report.add("no_grant_before_founding", False,
                    f"grants dated before the company's founding date: {bad}")
    else:
        report.add("no_grant_before_founding", True, "no grant predates the founding date")


def check_funding_rounds_chronological(table: CapTable, report: GateReport) -> None:
    rounds = sorted(table.funding_rounds, key=lambda r: r.date)
    down_rounds = [
        curr.round_id for prev, curr in zip(rounds, rounds[1:])
        if curr.price_per_share <= prev.price_per_share
    ]
    # A down round (price flat or lower than the prior round) is a real, legal
    # event, not a data error: hard-failing the pipeline over one would block
    # a cap table from ever being read again after a genuine down round. A
    # non-blocking warning keeps it visible without treating a true fact about
    # the company as if it were corrupt input.
    if down_rounds:
        report.add("funding_rounds_chronological", False,
                    f"price_per_share did not increase for rounds: {down_rounds}",
                    blocking=False)
    else:
        report.add("funding_rounds_chronological", True,
                    "price_per_share strictly increases round over round", blocking=False)


def run_all_gates(table: CapTable, as_of: date, founding_date: date) -> GateReport:
    """Run every gate in sequence and return the completed report.

    What a caller (the generator's own summary, or a future orchestration
    script) calls once a table is loaded, instead of reaching for each check
    individually.
    """
    report = GateReport()
    check_no_orphan_grants(table, report)
    check_no_duplicate_grant_ids(table, report)
    check_no_negative_or_zero_shares(table, report)
    check_vesting_schedule_sane(table, report)
    check_ownership_sums_to_one(table, as_of, report)
    check_no_grant_before_founding(table, founding_date, report)
    check_funding_rounds_chronological(table, report)
    return report
