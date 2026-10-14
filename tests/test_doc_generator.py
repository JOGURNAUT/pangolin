import re
from datetime import date

import pytest

from ai.doc_generator import generate_grant_letter
from pangolin.models import Grant, Stakeholder, VestingSchedule
from pangolin.vesting import vested_shares

STANDARD = VestingSchedule(cliff_months=12, total_months=48)


def _grant(strike_price: float = 0.0) -> Grant:
    return Grant(
        grant_id="G-1", stakeholder_id="founder-a", share_class="Common",
        total_shares=48_000, grant_date=date(2023, 1, 1), vesting=STANDARD,
        strike_price=strike_price,
    )


def _stakeholder() -> Stakeholder:
    return Stakeholder("founder-a", "Alice Founder", "founder")


def _parse_field(text: str, label: str) -> str:
    match = re.search(rf"^\s*{re.escape(label)}:\s*(.+)$", text, re.MULTILINE)
    assert match, f"field '{label}' not found in generated document"
    return match.group(1).strip()


def test_document_vested_and_unvested_numbers_match_vested_shares_directly():
    grant = _grant()
    as_of = date(2025, 1, 1)
    direct = vested_shares(grant, as_of)

    text = generate_grant_letter(grant, _stakeholder(), as_of)

    assert int(_parse_field(text, "Vested shares")) == direct.vested_shares
    assert int(_parse_field(text, "Unvested shares")) == direct.unvested_shares
    assert _parse_field(text, "Fully vested") == str(direct.is_fully_vested)


def test_document_before_cliff_shows_zero_vested():
    grant = _grant()
    as_of = date(2023, 6, 1)
    direct = vested_shares(grant, as_of)
    assert direct.vested_shares == 0

    text = generate_grant_letter(grant, _stakeholder(), as_of)
    assert int(_parse_field(text, "Vested shares")) == 0


def test_document_includes_grant_identity_fields():
    grant = _grant()
    text = generate_grant_letter(grant, _stakeholder(), date(2024, 1, 1))

    assert _parse_field(text, "Grant ID") == "G-1"
    assert _parse_field(text, "Stakeholder") == "Alice Founder"
    assert int(_parse_field(text, "Total shares granted")) == 48_000


def test_document_shows_restricted_stock_when_strike_price_is_zero():
    grant = _grant(strike_price=0.0)
    text = generate_grant_letter(grant, _stakeholder(), date(2024, 1, 1))
    assert "restricted stock" in _parse_field(text, "Strike price")


def test_document_shows_strike_price_when_nonzero():
    grant = _grant(strike_price=1.25)
    text = generate_grant_letter(grant, _stakeholder(), date(2024, 1, 1))
    assert "1.2500" in _parse_field(text, "Strike price")


def test_mismatched_stakeholder_raises():
    grant = _grant()
    wrong_person = Stakeholder("someone-else", "Not Alice", "employee")
    with pytest.raises(ValueError, match="someone-else"):
        generate_grant_letter(grant, wrong_person, date(2024, 1, 1))


def test_no_em_dash_anywhere_in_generated_document():
    grant = _grant(strike_price=2.0)
    text = generate_grant_letter(grant, _stakeholder(), date(2024, 1, 1))
    assert "\\u2014" not in text
