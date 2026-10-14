"""Fills an option-grant letter with numbers the vesting engine computed.

This is the "document processing" half of the AI layer's story: a grant
letter has to state vested shares as of some date, and that number has to be
the same one a stakeholder would get from any other part of this system. So
this module never computes vesting itself. It calls `vested_shares` once,
for the real grant and the real date, and renders the result into a plain
text template. If the template's wording changes, the numbers in it still
trace back to the one function that is allowed to produce them.
"""

from __future__ import annotations

from datetime import date

from pangolin.models import Grant, Stakeholder
from pangolin.vesting import vested_shares

_TEMPLATE = """\
OPTION GRANT STATEMENT

Grant ID: {grant_id}
Stakeholder: {stakeholder_name}
Share Class: {share_class}

Total shares granted: {total_shares}
Grant date: {grant_date}
Strike price: {strike_line}

As of {as_of}:
  Vested shares: {vested_shares}
  Unvested shares: {unvested_shares}
  Fully vested: {is_fully_vested}
"""


def generate_grant_letter(grant: Grant, stakeholder: Stakeholder, as_of: date) -> str:
    """Plain-text grant letter for one grant, with vesting as of one date.

    Raises `ValueError` if the stakeholder passed in does not match the
    grant's own `stakeholder_id`: a letter addressed to the wrong person is
    worse than no letter, and the mismatch is cheap to catch before it is
    ever rendered into text.
    """
    if stakeholder.stakeholder_id != grant.stakeholder_id:
        raise ValueError(
            f"grant '{grant.grant_id}' belongs to stakeholder "
            f"'{grant.stakeholder_id}', not '{stakeholder.stakeholder_id}'"
        )

    result = vested_shares(grant, as_of)

    # A strike price of 0.0 means this grant is restricted stock, not an
    # option, so the letter says so instead of printing a dollar amount that
    # would misleadingly imply there is an exercise cost.
    strike_line = (
        f"${grant.strike_price:,.4f} per share" if grant.strike_price
        else "N/A (restricted stock, no exercise price)"
    )

    return _TEMPLATE.format(
        grant_id=grant.grant_id,
        stakeholder_name=stakeholder.name,
        share_class=grant.share_class,
        total_shares=grant.total_shares,
        grant_date=grant.grant_date.isoformat(),
        strike_line=strike_line,
        as_of=as_of.isoformat(),
        vested_shares=result.vested_shares,
        unvested_shares=result.unvested_shares,
        is_fully_vested=result.is_fully_vested,
    )
