"""CLI-based browser automation agent using Anthropic API tool_use.

Implements the agentic loop:
  Claude decides action → Python executes playwright-cli → result fed back → repeat
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import anthropic

from agent.cost import calculate_cost
from agent.prompts import SYSTEM_PROMPT
from agent.result import AgentResult
from agent.runner import CliRunner
from agent.tools import BROWSER_TOOLS, execute_tool, _describe_tool_call

MAX_API_RETRIES = 3
RETRY_BASE_DELAY = 2.0


class CliAgent:
    """Browser automation agent driven by Claude + a CLI runner.

    Args:
        task: The task instruction to complete.
        model: Anthropic model name (e.g. 'claude-sonnet-4-6').
        runner: A CliRunner instance (PlaywrightCliRunner, etc.).
        output_dir: Directory for screenshots and trace data.
    """

    MAX_ITERATIONS = 50
    MAX_CONSECUTIVE_ERRORS = 3

    def __init__(
        self,
        task: str,
        model: str,
        runner: CliRunner,
        output_dir: Path | None = None,
        task_id: str = "",
    ):
        self.task = task
        self.model = model
        self.runner = runner
        self.output_dir = output_dir or Path(".")
        self.task_id = task_id
        self._client = anthropic.AsyncAnthropic()

    def _log(self, msg: str) -> None:
        """Print a prefixed log line for this task."""
        prefix = f"[{self.task_id}]" if self.task_id else "[agent]"
        print(f"{prefix} {msg}", flush=True)

    async def run(self) -> AgentResult:
        """Execute the task via the agentic loop. Returns AgentResult."""
        start_time = time.monotonic()
        steps: list[str] = []
        screenshot_paths: list[Path] = []
        total_input_tokens = 0
        total_output_tokens = 0
        consecutive_errors = 0
        final_result = "Agent did not return a result"
        step_number = 0

        try:
            # Start browser session
            self._log("Starting browser session...")
            initial_output = await self.runner.start()
            self._log("Browser started")

            # Create API client after browser start to avoid proxy timing issues
            client = anthropic.AsyncAnthropic()

            messages: list[dict] = [
                {
                    "role": "user",
                    "content": f"Complete the following task:\n\n{self.task}",
                },
            ]
            self._log(f"Task: {self.task[:120]}...")

            for iteration in range(self.MAX_ITERATIONS):
                # Call Anthropic API with retry
                self._log(f"Iteration {iteration + 1}/{self.MAX_ITERATIONS} — calling LLM...")
                response = await self._api_call_with_retry(
                    client, messages
                )

                # Track token usage
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens
                elapsed = time.monotonic() - start_time
                cost_so_far = calculate_cost(
                    total_input_tokens, total_output_tokens, self.model
                )
                self._log(
                    f"  LLM responded: stop={response.stop_reason}, "
                    f"tokens_in={total_input_tokens}, tokens_out={total_output_tokens}, "
                    f"cost=${cost_so_far:.3f}, elapsed={elapsed:.1f}s"
                )

                # Append assistant response to conversation
                messages.append(
                    {"role": "assistant", "content": response.content}
                )

                # Check if response actually contains tool_use blocks
                has_tool_use = any(
                    getattr(b, "type", None) == "tool_use"
                    for b in response.content
                )

                # No tool calls — agent is done
                # Also treat "tool_use" stop_reason with no actual tool_use
                # blocks as end_turn (proxy conversion edge case)
                if response.stop_reason == "end_turn" or (
                    response.stop_reason == "tool_use" and not has_tool_use
                ):
                    final_result = self._extract_text(response.content)
                    step_number += 1
                    steps.append(
                        f"Step {step_number}: Agent concluded — "
                        f"{final_result[:200]}"
                    )
                    self._log(f"  Agent concluded: {final_result[:150]}")
                    break

                # Process tool calls
                if response.stop_reason == "tool_use":
                    tool_results: list[dict] = []
                    task_complete = False

                    for block in response.content:
                        if block.type != "tool_use":
                            continue

                        step_number += 1
                        step_desc = _describe_tool_call(
                            block.name, block.input
                        )
                        steps.append(f"Step {step_number}: {step_desc}")
                        self._log(f"  Step {step_number}: {step_desc}")

                        try:
                            result_text, is_complete = await execute_tool(
                                self.runner,
                                block.name,
                                block.input,
                                screenshot_paths,
                            )
                            consecutive_errors = 0

                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result_text,
                                }
                            )

                            if is_complete:
                                final_result = result_text
                                task_complete = True
                                self._log(f"  Task complete: {result_text[:120]}")

                        except Exception as e:
                            consecutive_errors += 1
                            self._log(f"  ERROR ({consecutive_errors}/{self.MAX_CONSECUTIVE_ERRORS}): {e}")
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": f"Error: {e}",
                                    "is_error": True,
                                }
                            )

                    if tool_results:
                        messages.append({"role": "user", "content": tool_results})

                    if task_complete:
                        break

                    if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                        final_result = (
                            "Agent aborted after "
                            f"{self.MAX_CONSECUTIVE_ERRORS} consecutive errors"
                        )
                        step_number += 1
                        steps.append(
                            f"Step {step_number}: {final_result}"
                        )
                        self._log(f"  ABORTED: {final_result}")
                        break

            else:
                # Loop exhausted without breaking — max iterations reached
                final_result = (
                    f"Agent reached maximum iteration limit "
                    f"({self.MAX_ITERATIONS} steps)"
                )
                step_number += 1
                steps.append(f"Step {step_number}: {final_result}")
                self._log(f"  MAX ITERATIONS REACHED ({self.MAX_ITERATIONS})")
                # Try to capture final state
                try:
                    path = await self.runner.screenshot(
                        filename="final_timeout.png"
                    )
                    if path.exists():
                        screenshot_paths.append(path)
                except Exception:
                    pass

        finally:
            await self.runner.close()

        duration = time.monotonic() - start_time
        cost = calculate_cost(total_input_tokens, total_output_tokens, self.model)
        self._log(
            f"Done: {step_number} steps, {duration:.1f}s, "
            f"${cost:.3f}, tokens={total_input_tokens}+{total_output_tokens}"
        )

        return AgentResult(
            _steps=steps,
            _duration=duration,
            _cost=cost,
            _result=final_result,
            _screenshots=[str(p) for p in screenshot_paths if p.exists()],
            _input_tokens=total_input_tokens,
            _output_tokens=total_output_tokens,
        )

    async def _api_call_with_retry(self, client, messages):
        """Call Anthropic API with exponential backoff retry.

        Uses streaming to work around proxy non-streaming tool_use issues.
        """
        for attempt in range(MAX_API_RETRIES):
            try:
                async with client.messages.stream(
                    model=self.model,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=BROWSER_TOOLS,
                    messages=messages,
                ) as stream:
                    return await stream.get_final_message()
            except (anthropic.APIConnectionError, anthropic.InternalServerError) as e:
                if attempt == MAX_API_RETRIES - 1:
                    raise
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"API call failed (attempt {attempt + 1}): {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)

    @staticmethod
    def _extract_text(content: list) -> str:
        """Extract text from Anthropic API response content blocks."""
        parts = []
        for block in content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else "No text in response"
