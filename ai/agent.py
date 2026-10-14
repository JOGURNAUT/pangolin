"""A tool-calling loop that cannot put a number in its own mouth.

The model's only job is to pick a tool and its arguments from an English
question; it never sees the question's numeric answer before this module
does, and it never gets a turn to restate that answer in its own words after
the real tool returns it. Concretely: `ToolCallingAgent.answer` takes
whatever structured tool call the model produces, calls the matching
function in `ai.tools` (which only ever calls `pangolin.vesting` or
`pangolin.dilution`), and builds the final answer by formatting the tool's
real return value into a template. If the model's response also contains a
free-text claim about the number ("that's about 12,000 shares"), that text
is discarded, not merged in, because the model had no way to compute it
correctly and no way for this code to check that it did.

This mirrors this author's other two AI-layer projects: a text-to-SQL layer
that only ever runs a guarded SELECT against a real warehouse, and a RAG
agent that maps every citation back to a retrieved snippet and strips any
the model invented. Same rule here: the model routes, the engine computes.

Testing without credentials: `ToolCallingAgent` takes its model as an
injected callable, so `tests/test_agent.py` passes a plain Python function
that returns a scripted `ToolCall` and never touches the network. Wiring up
a real provider (matching this author's `aardvark` project, which reads
GROQ_API_KEY or GEMINI_API_KEY from a .env file) is optional and lives
entirely in `GroqChatModel` / `GeminiChatModel` below; neither class is
imported or instantiated by the test suite, so the suite needs zero API
keys and makes zero network calls.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ai.tools import tool_ownership, tool_simulate_round, tool_vesting_status
from pangolin.models import CapTable

# Every tool the agent is allowed to call, keyed by the name the model uses
# to request it. Adding a new capability means adding a real function to
# ai/tools.py and a line here, never teaching the model a new way to guess.
AVAILABLE_TOOLS: dict[str, Callable[..., Any]] = {
    "tool_vesting_status": tool_vesting_status,
    "tool_ownership": tool_ownership,
    "tool_simulate_round": tool_simulate_round,
}


@dataclass(frozen=True)
class ToolCall:
    """What the model decided to do: which tool, with which arguments.

    `arguments` excludes `table`, the one argument the model never supplies;
    the agent always injects the real `CapTable` itself, so there is no path
    by which a model response could substitute a different table or a
    fabricated one.

    `model_claim` is whatever free-text number or sentence the model offered
    alongside its tool choice ("that should be about 12,000 shares"). It is
    carried here only so the agent has somewhere to receive it; `answer`
    never reads this field when building `narration`, which is the guardrail
    `tests/test_agent.py` exercises directly.
    """
    tool_name: str
    arguments: dict[str, Any]
    model_claim: str | None = None


@dataclass(frozen=True)
class AgentAnswer:
    """What the agent hands back: the tool's real result, not the model's."""
    tool_name: str
    tool_result: Any
    narration: str


class ChatModel(Protocol):
    """What the agent needs from a model: turn a question into a ToolCall.

    Anything satisfying this (a stub function in a test, a real provider
    client below) can be dropped into `ToolCallingAgent` unchanged.
    """
    def __call__(self, question: str, table: CapTable) -> ToolCall:
        ...


class ToolCallingAgent:
    """Routes an English question to a real tool call and reports its result.

    `model` decides WHICH tool and WHICH arguments. Everything after that
    decision runs through `ai.tools`, so the only way this agent's final
    answer contains a number is if `pangolin.vesting` or `pangolin.dilution`
    produced it.
    """

    def __init__(self, model: ChatModel):
        self._model = model

    def answer(self, question: str, table: CapTable) -> AgentAnswer:
        call = self._model(question, table)

        if call.tool_name not in AVAILABLE_TOOLS:
            raise ValueError(f"model requested unknown tool '{call.tool_name}'")

        tool_fn = AVAILABLE_TOOLS[call.tool_name]
        # table is always the real one this method was called with, never
        # anything the model's arguments could override.
        result = tool_fn(table=table, **call.arguments)

        return AgentAnswer(
            tool_name=call.tool_name,
            tool_result=result,
            narration=_narrate(call.tool_name, result),
        )


def _narrate(tool_name: str, result: Any) -> str:
    """Plain-English sentence built only from fields the tool actually returned.

    No arithmetic happens here, only string formatting, so there is nowhere
    for a wrong number to enter between the tool call and this sentence.
    """
    if tool_name == "tool_vesting_status":
        return (
            f"As of {result['as_of']}, {result['stakeholder_id']} has "
            f"{result['vested_shares']} of {result['total_shares']} shares vested "
            f"({result['unvested_shares']} unvested)."
        )
    if tool_name == "tool_ownership":
        parts = ", ".join(
            f"{row['stakeholder_id']} {row['ownership_pct']:.1%}" for row in result
        )
        return f"Ownership: {parts}." if parts else "No ownership rows as of this date."
    if tool_name == "tool_simulate_round":
        parts = ", ".join(
            f"{sid} {pct:+.1%} pts" for sid, pct in result["dilution_impact_pct_points"].items()
        )
        return f"{result['round_name']} dilution impact: {parts}."
    return json.dumps(result)


# --- Optional real providers. Not imported by AVAILABLE_TOOLS or tests. ---
#
# Matching aardvark's pattern: a real model needs GROQ_API_KEY or
# GEMINI_API_KEY set in the environment (typically loaded from a .env file
# that this repo does not ship). Constructing either class below without the
# matching key raises immediately, by design, rather than silently falling
# back to a stub. Nothing in this module or in tests/test_agent.py
# constructs either class, so the whole test suite runs with zero keys set
# and zero network access.


class GroqChatModel:
    """Wraps a real Groq chat completion as a `ChatModel`. Needs GROQ_API_KEY."""

    def __init__(self, model_name: str = "llama-3.3-70b-versatile"):
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set; this provider needs it to run")
        self._api_key = api_key
        self._model_name = model_name

    def __call__(self, question: str, table: CapTable) -> ToolCall:
        raise NotImplementedError(
            "real Groq tool-calling wiring is out of scope for this portfolio "
            "layer's test suite; ToolCallingAgent only requires this method's "
            "signature, which a real integration would implement here"
        )


class GeminiChatModel:
    """Wraps a real Gemini chat completion as a `ChatModel`. Needs GEMINI_API_KEY."""

    def __init__(self, model_name: str = "gemini-2.0-flash"):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set; this provider needs it to run")
        self._api_key = api_key
        self._model_name = model_name

    def __call__(self, question: str, table: CapTable) -> ToolCall:
        raise NotImplementedError(
            "real Gemini tool-calling wiring is out of scope for this portfolio "
            "layer's test suite; ToolCallingAgent only requires this method's "
            "signature, which a real integration would implement here"
        )
