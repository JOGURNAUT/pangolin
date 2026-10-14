from datetime import date

import pytest

from pangolin.models import Grant, VestingSchedule
from pangolin.vesting import vested_shares

STANDARD = VestingSchedule(cliff_months=12, total_months=48)


def _grant(grant_date: date, schedule: VestingSchedule = STANDARD,
           total_shares: int = 48_000) -> Grant:
    return Grant(
        grant_id="G-1", stakeholder_id="S-1", share_class="Common",
        total_shares=total_shares, grant_date=grant_date, vesting=schedule,
    )


def test_nothing_vests_before_the_cliff():
    g = _grant(date(2023, 1, 1))
    r = vested_shares(g, as_of=date(2023, 12, 31))
    assert r.vested_shares == 0
    assert r.cliff_reached is False


def test_one_day_before_cliff_is_zero_one_day_after_is_a_quarter():
    g = _grant(date(2023, 1, 1))
    before = vested_shares(g, as_of=date(2023, 12, 31))
    at_cliff = vested_shares(g, as_of=date(2024, 1, 1))
    assert before.vested_shares == 0
    # 12/48 of the grant vests as a step at the cliff instant.
    assert at_cliff.vested_shares == 12_000
    assert at_cliff.cliff_reached is True


def test_linear_between_cliff_and_end():
    g = _grant(date(2023, 1, 1), total_shares=48_000)
    # At 24 months: cliff fraction (0.25) + half of the remaining 0.75.
    r = vested_shares(g, as_of=date(2025, 1, 1))
    assert r.vested_shares == 24_000


def test_fully_vested_at_schedule_end():
    g = _grant(date(2023, 1, 1))
    r = vested_shares(g, as_of=date(2027, 1, 1))
    assert r.vested_shares == 48_000
    assert r.is_fully_vested is True


def test_fully_vested_stays_fully_vested_well_past_the_end():
    g = _grant(date(2023, 1, 1))
    r = vested_shares(g, as_of=date(2030, 6, 15))
    assert r.vested_shares == 48_000
    assert r.unvested_shares == 0


def test_single_trigger_acceleration_on_exit_vests_everything():
    g = _grant(date(2024, 6, 1), schedule=VestingSchedule(
        cliff_months=12, total_months=48, accelerates_on_exit=True))
    # Only 3 months elapsed - nowhere near the cliff - but an exit happened.
    r = vested_shares(g, as_of=date(2024, 9, 1), exit_triggered=True)
    assert r.vested_shares == g.total_shares
    assert r.is_fully_vested is True


def test_exit_without_acceleration_clause_is_ignored():
    g = _grant(date(2024, 6, 1), schedule=VestingSchedule(
        cliff_months=12, total_months=48, accelerates_on_exit=False))
    r = vested_shares(g, as_of=date(2024, 9, 1), exit_triggered=True)
    assert r.vested_shares == 0


def test_month_end_grant_date_does_not_overflow_shorter_months():
    # Granted on Jan 31. One month later there is no Feb 31 - the calendar
    # rolls to the last day of February instead of overflowing into March.
    g = _grant(date(2023, 1, 31), schedule=VestingSchedule(
        cliff_months=1, total_months=2))
    r = vested_shares(g, as_of=date(2023, 2, 28))
    assert r.cliff_reached is True
    assert r.vested_shares == g.total_shares // 2


@pytest.mark.parametrize("months_elapsed,expected_fraction", [
    (0, 0.0), (11, 0.0), (12, 0.25), (30, 0.625), (48, 1.0),
])
def test_fraction_vested_matches_hand_computed_values(months_elapsed, expected_fraction):
    g = _grant(date(2023, 1, 1), total_shares=48_000)
    as_of = date(2023 + months_elapsed // 12, 1 + months_elapsed % 12, 1)
    r = vested_shares(g, as_of=as_of)
    assert r.vested_shares == pytest.approx(48_000 * expected_fraction, abs=1)
