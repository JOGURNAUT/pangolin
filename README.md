# Pangolin

An equity cap-table engine: vesting, dilution, and an AI layer that answers
questions about them without ever inventing a number.

**[Source →](https://github.com/JOGURNAUT/pangolin)**

A pangolin's defining feature is its scales. This project's is the same
overlapping-shares idea applied to equity: who holds how much of a company,
and how that changes as grants vest and new rounds dilute everyone else.

## The finding

Fully diluted ownership is deterministic. Vesting is deterministic. Dilution
from a new round is deterministic. None of that needs an LLM, and none of it
should ever be asked of one. The AI layer here only decides *which*
deterministic function answers a question and *what arguments* to call it
with; the number that comes back is always the real engine's number, never
the model's guess. `tests/test_agent.py` proves this directly: a stub model
is made to claim a vested-share count of 999,999,999, and the test asserts
that number never appears anywhere in the agent's final answer, only the real
one does.

That is the same discipline this author's other projects use for the same
reason: a text-to-SQL layer that only ever runs a guarded, read-only query
against a real warehouse, and a research agent that maps every citation back
to a retrieved source and strips any marker the model invented. A chat
interface over financial data is only trustworthy if it is architecturally
incapable of making a number up, not merely prompted not to.

## How it is built

```
pangolin/            pure Python: the vesting and dilution maths, no LLM or
                      engine imports, unit-tested in milliseconds
  models.py             the shapes: ShareClass, Stakeholder, Grant,
                        VestingSchedule, FundingRound, CapTable
  vesting.py            vested shares for one grant as of one date, including
                        the cliff step and single-trigger acceleration
  dilution.py           fully diluted ownership, and what a hypothetical new
                        round does to everyone else's percentage
  quality.py            seven gates, checked before a report is built, not
                        after
generator/            synthetic cap tables with real defects injected on
                      purpose, same philosophy as this author's other
                      generators: a generator that only emits clean data
                      tests nothing
ai/                   the layer an LLM is allowed to touch
  tools.py              plain functions wrapping the real engine; this is
                        the entire surface a model can call
  agent.py              a tool-calling loop, fully testable with zero API
                        keys via an injected stub model
  doc_generator.py       fills an option-grant-letter template with numbers
                        the engine computed, never numbers it computed itself
scripts/
  build_report.py       generate (or load) a cap table, run the gates, print
                        ownership and a sample vesting check
tests/                 53 tests, no network calls, no API keys required
```

## Run it

```bash
pip install -r requirements.txt
python -m generator.produce --employees 40 --seed 42        # writes data/raw/captable.json
python scripts/build_report.py                              # gates, then ownership
python -m pytest tests/ -q                                  # 53 tests, ~0.1s
```

## The gates run before the report, not after

`scripts/build_report.py` calls `run_all_gates` first and stops, printing
nothing about ownership, if a blocking gate fails. The default generator run
seeds orphan grants (a grant whose `stakeholder_id` is not in the
stakeholders dict) at a 3% rate on purpose, so the default output of
`build_report.py` against `data/raw/captable.json` is a gate failure, not a
report. That is intentional: a pipeline that quietly reported ownership
percentages computed from a cap table with unresolved grants would be
reporting a number nobody should trust, with nothing on the page saying so.
`data/raw/captable_clean.json` (generated with every defect rate at zero)
shows the happy path.

## Vesting, specifically

The one rule worth being precise about: before the cliff, vested shares are
exactly zero, not a small linear fraction climbing toward the cliff. A grant
one day before its cliff and one day after differ by 25% of the total grant
(for the standard 12-month cliff on a 48-month schedule), not by one day's
worth of vesting. `tests/test_vesting.py` asserts this directly, alongside a
parametrized check against hand-computed fractions at months 0, 11, 12, 30,
and 48.

Elapsed time is counted calendar-month-aware, not as `days / 30`: over a
four-year schedule that approximation drifts by most of a month, which is
enough to vest someone early or late depending on which way it leans.

## Dilution, specifically

Ownership is always fully diluted (every granted share counts, vested or
not), because that is the convention a real term sheet uses, and a
vested-only percentage would drift upward every month purely from a
stakeholder's own shares vesting, with no transaction having happened.
`simulate_round` never mutates the table it is given; it builds a scratch
copy, so asking "what would a new round do" is safe to call from a chat
interface without side effects. `tests/test_dilution.py` checks a
sequential-round scenario: an investor who sits out a later round still gets
diluted by it, because dilution is a fact about share count versus the new
total, not about who participated.

## The AI layer, honestly

`ai/agent.py` is fully testable with no API key and no network call, the same
way this author's `foodpulse` AI scripts and `aardvark` agent are. An
optional Groq/Gemini-backed model is documented in the module (matching
`aardvark`'s `GROQ_API_KEY` / `GEMINI_API_KEY` pattern) but is never
imported or exercised by the test suite. What is tested, with a stub model
standing in for a real one, is the one guarantee that actually matters: the
agent calls a real tool, and the real tool's return value, not anything the
stub model also said, is what ends up in the answer.

## What is synthetic, and why that is stated plainly here

`generator/produce.py` builds a company from a seed: two founders, a
configurable number of employee grants with lognormal share sizes, and three
funding rounds (Seed, Series A, Series B) at increasing price. Five defect
types are injected at independently tunable rates, each named for the real
failure it simulates (an HR system creating a grant before the person record
syncs, a form submitted twice, a clerical backdating error, a per-share price
corrected after close without the share count being recomputed, a draft
grant exported before it was filled in). Founder grants are excluded from the
defect pool on purpose: they are created directly by the company's lawyers at
incorporation, not through the HR pipeline these defects model, so there is
no realistic path for one to pick up a grant-pipeline defect.

The numbers in this README and in `scripts/build_report.py`'s output come
from this generator, not from a real company's data. The engineering
decisions, the vesting edge cases, the dilution maths, and the "the LLM
never computes a number" guarantee are the same regardless of whose data
runs through them.
