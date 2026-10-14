"""Run the pipeline end to end: generate, gate, and report.

    python scripts/build_report.py
    python scripts/build_report.py --as-of 2025-06-01

Three stages, in the order a real system would run them: load the cap table,
run the quality gates against it (and stop before reporting anything if a
blocking gate fails, the same "nothing moves on a bad batch" rule the author's
other pipelines use), then compute ownership and a few AI-layer answers
against data that has already been declared sane.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai.tools import tool_ownership, tool_vesting_status  # noqa: E402
from generator.produce import load_captable  # noqa: E402
from pangolin.quality import GateFailure, run_all_gates  # noqa: E402

FOUNDING_DATE = date(2021, 3, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="data/raw/captable.json")
    parser.add_argument("--as-of", default="2025-06-01",
                        help="ISO date the report reasons as-of. Chosen inside the "
                             "generator's own employee-hire window on purpose, so the "
                             "report shows a real mix of vested, partially vested and "
                             "unvested grants rather than everything settled at 100%%.")
    args = parser.parse_args(argv)

    source = ROOT / args.source
    if not source.exists():
        print(f"no source at {source}, run `python -m generator.produce` first", file=sys.stderr)
        return 1

    as_of = date.fromisoformat(args.as_of)
    table = load_captable(source)

    report = run_all_gates(table, as_of=as_of, founding_date=FOUNDING_DATE)
    print(report.summary())
    print()
    try:
        report.raise_if_failed()
    except GateFailure as exc:
        # Stop here, deliberately: a report built on a table that failed a
        # blocking gate would show numbers nobody should trust yet, with
        # nothing on the page saying so.
        print(f"blocking gate failure, report not built: {exc}", file=sys.stderr)
        return 1

    rows = tool_ownership(table, as_of=as_of)
    print(f"Ownership as of {as_of} ({len(rows)} holders):")
    for row in rows[:10]:
        holder = table.stakeholders.get(row["stakeholder_id"])
        name = holder.name if holder else f"{row['stakeholder_id']} (unresolved, orphan grant)"
        print(f"  {name:<28} {row['ownership_pct'] * 100:6.2f}%  ({row['shares']:,} shares)")
    if len(rows) > 10:
        print(f"  ... and {len(rows) - 10} more")

    print()
    founders = [sid for sid, s in table.stakeholders.items() if s.kind == "founder"]
    if founders:
        status = tool_vesting_status(table, founders[0], as_of=as_of)
        print(f"Sample vesting check, {table.stakeholders[founders[0]].name}:")
        print(f"  vested {status['vested_shares']:,} / {status['total_shares']:,} shares "
              f"as of {as_of}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
