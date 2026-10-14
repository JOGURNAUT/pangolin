from datetime import date

from ai.agent import AgentAnswer, ToolCall, ToolCallingAgent
from ai.tools import tool_vesting_status
from pangolin.models import CapTable, Grant, Stakeholder, VestingSchedule

STANDARD = VestingSchedule(cliff_months=12, total_months=48)


def _table() -> CapTable:
    table = CapTable(company_name="Testco")
    table.stakeholders = {
        "founder-a": Stakeholder("founder-a", "Alice Founder", "founder"),
    }
    table.grants = [
        Grant("G-1", "founder-a", "Common", 4_000_000, date(2022, 1, 1), STANDARD),
    ]
    return table


def test_agent_calls_the_real_tool_and_matches_it_directly():
    as_of = date(2024, 6, 1)
    table = _table()

    # A stub model: no network, no API key, always picks the same tool call.
    def stub_model(question: str, table: CapTable) -> ToolCall:
        return ToolCall(
            tool_name="tool_vesting_status",
            arguments={"stakeholder_id": "founder-a", "as_of": as_of},
        )

    agent = ToolCallingAgent(model=stub_model)
    answer = agent.answer("How much has founder-a vested?", table)

    direct = tool_vesting_status(table, "founder-a", as_of)

    assert isinstance(answer, AgentAnswer)
    assert answer.tool_name == "tool_vesting_status"
    assert answer.tool_result == direct
    assert str(direct["vested_shares"]) in answer.narration


def test_agent_ignores_a_model_claim_that_disagrees_with_the_real_tool():
    as_of = date(2024, 6, 1)
    table = _table()
    real = tool_vesting_status(table, "founder-a", as_of)
    # The real answer at this date is nowhere near this number; a model that
    # hallucinates a confident, wrong figure alongside a correct tool choice
    # is exactly the failure mode this test exists to catch.
    bogus_claim = "The answer is 999999999 vested shares."
    assert "999999999" != str(real["vested_shares"])

    def lying_stub_model(question: str, table: CapTable) -> ToolCall:
        return ToolCall(
            tool_name="tool_vesting_status",
            arguments={"stakeholder_id": "founder-a", "as_of": as_of},
            model_claim=bogus_claim,
        )

    agent = ToolCallingAgent(model=lying_stub_model)
    answer = agent.answer("How much has founder-a vested?", table)

    # The tool's real result is untouched by the model's claim...
    assert answer.tool_result["vested_shares"] == real["vested_shares"]
    # ...and the narration built from that result never repeats the lie.
    assert "999999999" not in answer.narration
    assert str(real["vested_shares"]) in answer.narration


def test_agent_rejects_a_tool_name_the_model_made_up():
    table = _table()

    def confused_stub_model(question: str, table: CapTable) -> ToolCall:
        return ToolCall(tool_name="tool_delete_everything", arguments={})

    agent = ToolCallingAgent(model=confused_stub_model)
    try:
        agent.answer("do something", table)
        assert False, "expected a ValueError for an unknown tool name"
    except ValueError as exc:
        assert "tool_delete_everything" in str(exc)
