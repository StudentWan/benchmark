"""Agent result dataclass compatible with browser-use AgentHistory interface."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UsageInfo:
    """Token usage and cost information."""

    total_cost: float
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class AgentResult:
    """Result from CliAgent, compatible with browser-use AgentHistory interface.

    All fields are immutable after creation.
    """

    _steps: list[str]
    _duration: float
    _cost: float
    _result: str
    _screenshots: list[str]
    _input_tokens: int
    _output_tokens: int

    @property
    def usage(self) -> UsageInfo:
        return UsageInfo(
            total_cost=self._cost,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )

    def number_of_steps(self) -> int:
        return len(self._steps)

    def total_duration_seconds(self) -> float:
        return self._duration

    def final_result(self) -> str:
        return self._result

    def agent_steps(self) -> list[str]:
        return list(self._steps)

    def screenshot_paths(self) -> list[str]:
        return list(self._screenshots)
