"""A fake Anthropic client that replays canned turns.

The agent loop is the riskiest code in the project and the most expensive to
exercise for real. This lets every branch of it -- tool dispatch, unknown
tools, tool errors, invented citations, the turn cap -- be tested offline,
deterministically, with no API key.

It mimics only what `agent.LLMClient` uses: `.create(...)` returning an object
with `.content`, `.stop_reason` and `.usage`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    name: str
    input: dict
    id: str = "toolu_fake"
    type: str = "tool_use"


@dataclass
class Usage:
    input_tokens: int = 800
    output_tokens: int = 400


@dataclass
class FakeResponse:
    content: list[Any]
    stop_reason: str = "tool_use"
    usage: Usage = field(default_factory=Usage)


def tool_call(name: str, **kwargs) -> FakeResponse:
    """One turn in which the model calls a single tool."""
    return FakeResponse(content=[ToolUseBlock(name=name, input=kwargs)])


def text(message: str, stop_reason: str = "end_turn") -> FakeResponse:
    """One turn in which the model just talks -- which for us is a failure."""
    return FakeResponse(content=[TextBlock(text=message)], stop_reason=stop_reason)


class FakeLLM:
    """Replays a scripted list of responses, recording what it was asked.

    `calls` holds every kwargs dict passed to create(), so a test can assert
    what the subagent was actually given -- which is the only way to prove the
    lane separation holds.
    """

    def __init__(self, script: list[FakeResponse]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs) -> FakeResponse:
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError(
                "FakeLLM ran out of scripted responses -- the agent made more "
                "calls than the test expected."
            )
        return self._script.pop(0)

    @property
    def exhausted(self) -> bool:
        return not self._script
