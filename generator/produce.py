"""Synthetic cap table data, with the defects a real one accumulates.

A generator that only emits clean data is not testing anything: every
downstream quality gate in this project was written because a real cap table
once had the defect it checks for, so this producer injects those same
failures at configurable rates. A run with every rate at zero would prove
nothing about the gates; it would only prove the generator can describe a
company that never made a single data entry mistake, which no company has.

Injected, each independently tunable:

  --orphan-grant-rate      a grant whose stakeholder_id is not in the
                           stakeholders dict, from an HR system that created
                           the grant before the person record synced over
  --duplicate-grant-rate   the same grant (person, shares, date) entered
                           twice under two grant_ids, from a form submitted
                           twice
  --backdated-rate         a grant dated before the company's own founding
                           date, from a clerical date-entry error
  --mismatched-round-rate  a funding round where shares_issued times
                           price_per_share no longer equals the round's
                           stated raise, from a per-share price corrected
                           after close without anyone recomputing the share
                           count on file
  --zero-share-rate        a grant with total_shares=0, a placeholder draft
                           that got exported before it was ever filled in

Only employee grants carry the grant-level defects (orphan, duplicate,
backdated, zero-share). Founder grants are created directly at incorporation
by the company's own lawyers, not through the HR/grant-request pipeline these
defects model, so there is no realistic path for a founder grant to pick one
up.

    python -m generator.produce --employees 30 --seed 42
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
from collections import Counter
from datetime import date, timedelta
from typing import Any

from pangolin.models import (
    CapTable,
    FundingRound,
    Grant,
    ShareClass,
    ShareClassKind,
    Stakeholder,
    VestingSchedule,
)

FOUNDING_DATE = date(2021, 3, 1)

# Days after founding. Not evenly spaced because real startups don't raise on
# a schedule: Seed follows quickly once the founders have something to show,
# Series A takes much longer because it needs usage or revenue to back it up.
SEED_DAY = 270
SERIES_A_DAY = 720
SERIES_B_DAY = 1350
EMPLOYEE_MAX_DAY = 1500

FOUNDER_SHARES = 5_000_000


def _strike_price(grant_date: date, founding: date) -> float:
    """409A-style fair market value: low at founding, rising as the company matures.

    A real strike price tracks the company's own valuation history, not a
    flat number -- an employee hired after Series A pays more per share than
    one hired at founding, because the common stock is genuinely worth more
    by then.
    """
    days = max(0, (grant_date - founding).days)
    growth = min(days / EMPLOYEE_MAX_DAY, 1.0)
    return round(0.05 + growth * 3.95, 4)


def _make_share_classes() -> dict[str, ShareClass]:
    return {
        "Common": ShareClass("Common", ShareClassKind.COMMON),
        "Series Seed Preferred": ShareClass("Series Seed Preferred", ShareClassKind.PREFERRED),
        "Series A Preferred": ShareClass("Series A Preferred", ShareClassKind.PREFERRED),
        "Series B Preferred": ShareClass("Series B Preferred", ShareClassKind.PREFERRED),
    }


def _make_founders(table: CapTable) -> None:
    # accelerates_on_exit=True is the standard founder term: single-trigger
    # acceleration so a founder isn't left holding unvested stock the moment
    # the company they built gets acquired.
    vesting = VestingSchedule(cliff_months=12, total_months=48, accelerates_on_exit=True)
    for i, name in enumerate(("Founder A", "Founder B"), start=1):
        sid = f"F-{i:04d}"
        table.stakeholders[sid] = Stakeholder(sid, name, "founder")
        table.grants.append(Grant(
            grant_id=f"GF-{i:04d}",
            stakeholder_id=sid,
            share_class="Common",
            total_shares=FOUNDER_SHARES,
            grant_date=FOUNDING_DATE,
            vesting=vesting,
            strike_price=_strike_price(FOUNDING_DATE, FOUNDING_DATE),
        ))


def _make_employees(
    table: CapTable,
    rng: random.Random,
    args: argparse.Namespace,
    grant_defects: dict[str, list[str]],
) -> None:
    vesting = VestingSchedule(cliff_months=12, total_months=48, accelerates_on_exit=False)
    for i in range(1, args.employees + 1):
        sid = f"E-{i:04d}"
        defects: list[str] = []

        # Lognormal, not uniform: most hires get a modest grant and a handful
        # of senior hires get several times the median, which is the actual
        # shape of a startup's grant sizes, not a flat range.
        shares = int(rng.lognormvariate(8.4, 0.7))
        shares = max(500, min(shares, 50_000))
        if rng.random() < args.zero_share_rate:
            # A draft grant created in the cap table tool and never filled in
            # before someone exported the table for a board meeting.
            shares = 0
            defects.append("zero_share")

        grant_date = FOUNDING_DATE + timedelta(days=rng.uniform(45, EMPLOYEE_MAX_DAY))
        if rng.random() < args.backdated_rate:
            grant_date = FOUNDING_DATE - timedelta(days=rng.randint(10, 400))
            defects.append("backdated")

        is_orphan = rng.random() < args.orphan_grant_rate
        if is_orphan:
            # The grant exists; the person record does not, yet. Deliberately
            # left out of table.stakeholders rather than added.
            defects.append("orphan")
        else:
            table.stakeholders[sid] = Stakeholder(sid, f"Employee {i}", "employee")

        grant = Grant(
            grant_id=f"GE-{i:04d}",
            stakeholder_id=sid,
            share_class="Common",
            total_shares=shares,
            grant_date=grant_date,
            vesting=vesting,
            strike_price=_strike_price(grant_date, FOUNDING_DATE),
        )
        table.grants.append(grant)
        if defects:
            grant_defects[grant.grant_id] = defects

        if rng.random() < args.duplicate_grant_rate:
            # Same person, same shares, same date, different grant_id: the
            # signature of one form submitted twice, not of two real grants.
            dup_id = f"{grant.grant_id}-DUP"
            table.grants.append(Grant(
                grant_id=dup_id,
                stakeholder_id=sid,
                share_class="Common",
                total_shares=shares,
                grant_date=grant_date,
                vesting=vesting,
                strike_price=grant.strike_price,
            ))
            grant_defects[dup_id] = ["duplicate"]


def _make_rounds(
    table: CapTable,
    rng: random.Random,
    args: argparse.Namespace,
    round_meta: dict[str, dict[str, Any]],
) -> None:
    # (round_id, name, share_class, day offset from founding, investor id,
    #  investor name, target raise, headline price per share)
    rounds = [
        ("RD-SEED", "Seed", "Series Seed Preferred", SEED_DAY, "INV-SEED",
         "Anchor Seed Capital", 2_000_000.0, 1.00),
        ("RD-A", "Series A", "Series A Preferred", SERIES_A_DAY, "INV-SERIES-A",
         "Bluepeak Ventures", 10_000_000.0, 4.50),
        ("RD-B", "Series B", "Series B Preferred", SERIES_B_DAY, "INV-SERIES-B",
         "Crestline Growth Partners", 25_000_000.0, 12.00),
    ]
    for round_id, name, share_class, day_offset, investor_id, investor_name, base_raise, base_price in rounds:
        table.stakeholders[investor_id] = Stakeholder(investor_id, investor_name, "investor")
        round_date = FOUNDING_DATE + timedelta(days=day_offset)
        # Light jitter so the seed controls the exact numbers rather than
        # every run producing the identical textbook figures.
        target_raise = base_raise * rng.uniform(0.9, 1.1)
        price_per_share = round(base_price * rng.uniform(0.95, 1.05), 4)
        shares_issued = round(target_raise / price_per_share)

        defects: list[str] = []
        if rng.random() < args.mismatched_round_rate:
            # The price was corrected after the round closed (a common
            # true-up once an option pool top-up gets negotiated in) but the
            # share count on file was never recomputed against it.
            price_per_share = round(price_per_share * rng.uniform(1.1, 1.4), 4)
            defects.append("mismatched_round")

        table.funding_rounds.append(FundingRound(
            round_id=round_id,
            name=name,
            date=round_date,
            new_investor_id=investor_id,
            shares_issued=shares_issued,
            price_per_share=price_per_share,
            share_class=share_class,
        ))
        round_meta[round_id] = {"stated_raise": round(target_raise, 2), "defects": defects}


def generate(
    args: argparse.Namespace,
) -> tuple[CapTable, dict[str, list[str]], dict[str, dict[str, Any]]]:
    rng = random.Random(args.seed)
    table = CapTable(company_name="Pangolin Synthetic Co.", share_classes=_make_share_classes())
    grant_defects: dict[str, list[str]] = {}
    round_meta: dict[str, dict[str, Any]] = {}

    _make_founders(table)
    _make_employees(table, rng, args, grant_defects)
    _make_rounds(table, rng, args, round_meta)

    return table, grant_defects, round_meta


def _vesting_to_dict(v: VestingSchedule) -> dict[str, Any]:
    return {
        "cliff_months": v.cliff_months,
        "total_months": v.total_months,
        "accelerates_on_exit": v.accelerates_on_exit,
    }


def _grant_to_dict(g: Grant, defects: list[str] | None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "grant_id": g.grant_id,
        "stakeholder_id": g.stakeholder_id,
        "share_class": g.share_class,
        "total_shares": g.total_shares,
        "grant_date": g.grant_date.isoformat(),
        "vesting": _vesting_to_dict(g.vesting),
        "strike_price": g.strike_price,
    }
    if defects:
        d["_defects"] = defects
    return d


def _round_to_dict(r: FundingRound, meta: dict[str, Any] | None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "round_id": r.round_id,
        "name": r.name,
        "date": r.date.isoformat(),
        "new_investor_id": r.new_investor_id,
        "shares_issued": r.shares_issued,
        "price_per_share": r.price_per_share,
        "share_class": r.share_class,
    }
    if meta:
        d["_stated_raise"] = meta.get("stated_raise")
        if meta.get("defects"):
            d["_defects"] = meta["defects"]
    return d


def serialize_captable(
    table: CapTable,
    grant_defects: dict[str, list[str]] | None = None,
    round_meta: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """CapTable as plain JSON-safe dicts.

    Dataclasses don't serialize through json.dumps on their own: dates and
    enums aren't JSON types. This walks the structure by hand rather than
    reaching for dataclasses.asdict, which would stringify the date and enum
    through repr instead of the ISO string and plain value a loader can
    parse back without guessing the format.
    """
    grant_defects = grant_defects or {}
    round_meta = round_meta or {}
    return {
        "company_name": table.company_name,
        "share_classes": {
            name: {
                "name": sc.name,
                "kind": sc.kind.value,
                "liquidation_multiple": sc.liquidation_multiple,
            }
            for name, sc in table.share_classes.items()
        },
        "stakeholders": {
            sid: {"stakeholder_id": s.stakeholder_id, "name": s.name, "kind": s.kind}
            for sid, s in table.stakeholders.items()
        },
        "grants": [_grant_to_dict(g, grant_defects.get(g.grant_id)) for g in table.grants],
        "funding_rounds": [_round_to_dict(r, round_meta.get(r.round_id)) for r in table.funding_rounds],
    }


def load_captable(path: str | pathlib.Path) -> CapTable:
    """Read a serialized cap table back as real pangolin.models objects.

    Keys prefixed with an underscore (_defects, _stated_raise) are debug
    annotations this generator adds so a human or a quality gate can see
    which rows were seeded as defective without re-deriving it; they are not
    fields of the real dataclasses and are dropped here rather than passed
    through, since Grant and FundingRound are frozen dataclasses that would
    reject an unknown keyword.
    """
    with pathlib.Path(path).open(encoding="utf-8") as handle:
        raw = json.load(handle)

    share_classes = {
        name: ShareClass(sc["name"], ShareClassKind(sc["kind"]), sc.get("liquidation_multiple", 1.0))
        for name, sc in raw["share_classes"].items()
    }
    stakeholders = {
        sid: Stakeholder(s["stakeholder_id"], s["name"], s["kind"])
        for sid, s in raw["stakeholders"].items()
    }
    grants = [
        Grant(
            grant_id=g["grant_id"],
            stakeholder_id=g["stakeholder_id"],
            share_class=g["share_class"],
            total_shares=g["total_shares"],
            grant_date=date.fromisoformat(g["grant_date"]),
            vesting=VestingSchedule(**g["vesting"]),
            strike_price=g.get("strike_price", 0.0),
        )
        for g in raw["grants"]
    ]
    funding_rounds = [
        FundingRound(
            round_id=r["round_id"],
            name=r["name"],
            date=date.fromisoformat(r["date"]),
            new_investor_id=r["new_investor_id"],
            shares_issued=r["shares_issued"],
            price_per_share=r["price_per_share"],
            share_class=r["share_class"],
        )
        for r in raw["funding_rounds"]
    ]
    return CapTable(
        company_name=raw["company_name"],
        share_classes=share_classes,
        stakeholders=stakeholders,
        grants=grants,
        funding_rounds=funding_rounds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--employees", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="data/raw/captable.json")
    parser.add_argument("--orphan-grant-rate", type=float, default=0.03)
    parser.add_argument("--duplicate-grant-rate", type=float, default=0.03)
    parser.add_argument("--backdated-rate", type=float, default=0.02)
    # There are only 2-3 funding rounds total, so a 0.01-0.05 rate (right for
    # the ~30 grant-level rolls) would almost never land on one of them. This
    # flag gets a higher default for the same reason a six-sided die needs a
    # bigger sample than a coin: fewer trials need better odds per trial to
    # show up at all in a default run.
    parser.add_argument("--mismatched-round-rate", type=float, default=0.2)
    parser.add_argument("--zero-share-rate", type=float, default=0.02)
    args = parser.parse_args(argv)

    table, grant_defects, round_meta = generate(args)

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_captable(table, grant_defects, round_meta)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    defect_tags = Counter(tag for tags in grant_defects.values() for tag in tags)
    round_defect_tags = Counter(tag for meta in round_meta.values() for tag in meta["defects"])

    print(f"{table.company_name}: {len(table.stakeholders)} stakeholders, "
          f"{len(table.grants)} grants, {len(table.funding_rounds)} funding rounds -> {out_path}")
    print(f"  orphan grants {defect_tags['orphan']}, duplicate grants {defect_tags['duplicate']}, "
          f"backdated grants {defect_tags['backdated']}, zero-share grants {defect_tags['zero_share']}")
    print(f"  mismatched rounds {round_defect_tags['mismatched_round']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
