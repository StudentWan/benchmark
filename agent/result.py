"""Agent result dataclass for benchmark evaluation.

Immutable result capturing all execution metrics, step traces,
and metadata needed by the judge pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentResult:
    """Immutable result of a single task execution via the Agent SDK.

    Designed for compatibility with the existing judge pipeline
    (judge.py / judge_llm.py).
    """

    success: bool
    """Whether the agent completed without hitting error limits."""

    answer: str | None
    """The agent's final answer or result text."""

    failure_reason: str | None
    """Why the agent failed (max turns, budget, timeout, etc.)."""

    steps: tuple[dict[str, Any], ...]
    """Execution trace — tuple of step dicts for immutability.
    Each dict: {step, command, output, is_error, screenshots}."""

    screenshots: tuple[str, ...]
    """File paths to all captured screenshots."""

    captcha_encountered: bool
    """Whether the agent encountered a CAPTCHA during execution."""

    task_impossible: bool
    """Whether the agent determined the task is impossible."""

    token_usage: dict[str, int]
    """Token counts: {input_tokens, output_tokens}."""

    cost_usd: float
    """Total API cost in USD."""

    duration_ms: float
    """Total execution time in milliseconds."""

    num_turns: int
    """Number of agentic turns taken."""

    # ------------------------------------------------------------------
    # Judge compatibility methods
    # ------------------------------------------------------------------

    def agent_steps_for_judge(self) -> list[str]:
        """Generate step descriptions in the format expected by judge.py.

        judge.py's construct_judge_messages() expects ``list[str]``
        where each entry is a human-readable step description.
        Includes command output so the judge can verify what happened.
        """
        result: list[str] = []
        for s in self.steps:
            step_num = s.get("step", "?")
            command = s.get("command", "")
            output = s.get("output", "")
            is_error = s.get("is_error", False)

            # Truncate for judge context window, but keep enough to be useful
            cmd_display = command[:500] + "..." if len(command) > 500 else command
            out_display = output[:2000] + "..." if len(output) > 2000 else output

            parts = [f"Step {step_num}: {cmd_display}"]
            if out_display:
                parts.append(f"  Output: {out_display}")
            if is_error:
                parts.append("  [ERROR]")
            result.append("\n".join(parts))
        return result

    def number_of_steps(self) -> int:
        return len(self.steps)

    def total_duration_seconds(self) -> float:
        return self.duration_ms / 1000.0

    def final_result(self) -> str:
        return self.answer or "Agent did not return a result"

    def screenshot_paths(self) -> list[str]:
        return list(self.screenshots)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for run_data storage."""
        return {
            "success": self.success,
            "answer": self.answer,
            "failure_reason": self.failure_reason,
            "steps": list(self.steps),
            "num_steps": len(self.steps),
            "screenshots": list(self.screenshots),
            "captcha_encountered": self.captcha_encountered,
            "task_impossible": self.task_impossible,
            "token_usage": self.token_usage,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "num_turns": self.num_turns,
        }
