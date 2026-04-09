"""Agent SDK-based browser automation executor for benchmarks.

Replaces the custom CliAgent agentic loop with Claude Code Agent SDK's
managed execution. Each CLI tool is isolated via dual enforcement:
1. System prompt constraints (soft)
2. PreToolUse hook command validation (hard)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    query,
)

from agent.cli_registry import CliTool, get_cli_tool
from agent.hooks import (
    CAPTCHA_PATTERNS,
    StepTracker,
    create_post_tool_use_hook,
    create_pre_tool_use_hook,
)
from agent.prompts import build_system_prompt
from agent.result import AgentResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutorConfig:
    """Immutable configuration for a single agent run."""

    cli_tool_name: str
    """Name of the CLI tool to use (must be in CLI_REGISTRY)."""

    max_turns: int = 50
    """Maximum number of agentic turns (equivalent to old MAX_ITERATIONS)."""

    max_budget_usd: float = 5.0
    """Maximum API cost in USD before aborting."""

    timeout_seconds: int = 1800
    """Per-task timeout in seconds (default: 30 minutes)."""

    model: str = "sonnet"
    """Claude model to use (alias or full name)."""

    screenshot_dir: Path = Path("./screenshots")
    """Directory where CLI tools save screenshots."""

    anthropic_base_url: str | None = None
    """Proxy URL for agent maestro desktop. If set, passed via env to SDK."""

    cli_path: str | None = None
    """Path to Claude Code CLI binary. If None, SDK auto-detects."""

    headless: bool = True
    """Whether to run the browser in headless mode."""


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class AgentSDKExecutor:
    """Execute a browser automation task using the Claude Code Agent SDK.

    Replaces the old CliAgent. Each instance handles one task execution.
    """

    def __init__(self, config: ExecutorConfig):
        self._config = config
        self._cli_tool: CliTool = get_cli_tool(config.cli_tool_name)
        self._tracker = StepTracker()
        self._task_id = ""

    def _log(self, msg: str) -> None:
        """Print a prefixed log line for this task."""
        prefix = f"[{self._task_id}]" if self._task_id else "[agent]"
        print(f"{prefix} {msg}", flush=True)

    async def execute(self, task_description: str) -> AgentResult:
        """Execute a single task. Returns AgentResult for the judge pipeline.

        This method:
        1. Sets up screenshot directory
        2. Invokes the Agent SDK query loop
        3. Collects results, steps, and screenshots
        4. Returns structured AgentResult
        """
        self._config.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._tracker.set_screenshot_dir(self._config.screenshot_dir)

        # Register CLI tool's default screenshot directories as fallback
        for extra_dir in self._cli_tool.default_screenshot_dirs:
            self._tracker.add_screenshot_dir(extra_dir)

        # Mark all existing screenshots as "already known" so we only
        # collect screenshots that are new from THIS task run.
        self._tracker.snapshot_existing_files()

        # Kill any existing browser daemon so headed/headless mode takes
        # effect on the next launch. Daemon-based CLIs (like agent-browser)
        # ignore --headed if a headless daemon is already running.
        await self._close_existing_browser()

        start_time = time.monotonic()

        try:
            result = await asyncio.wait_for(
                self._run_agent(task_description),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Task timed out after %d seconds", self._config.timeout_seconds
            )
            result = self._build_timeout_result(start_time)

        return result

    async def _run_agent(self, task_description: str) -> AgentResult:
        """Core agent execution using the SDK."""
        system_prompt_text = build_system_prompt(
            self._cli_tool, headless=self._config.headless
        )

        # Write instructions as CLAUDE.md in a temp working directory.
        # Claude Code prioritizes CLAUDE.md over --system-prompt, which
        # gets overridden by Claude Code's own built-in system prompt.
        work_dir = tempfile.mkdtemp(prefix="benchmark_task_")
        claude_md_path = Path(work_dir) / "CLAUDE.md"

        try:
            claude_md_path.write_text(system_prompt_text, encoding="utf-8")

            # Create hooks bound to this execution's tracker
            pre_hook = create_pre_tool_use_hook(self._cli_tool)
            post_hook = create_post_tool_use_hook(
                self._cli_tool, self._tracker, self._config.screenshot_dir
            )

            # Build environment variables for the SDK session
            env: dict[str, str] = {}
            if self._config.anthropic_base_url:
                env["ANTHROPIC_BASE_URL"] = self._config.anthropic_base_url

            # Headed/headless mode (if CLI supports env var config)
            if not self._config.headless and self._cli_tool.headed_env_var:
                env[self._cli_tool.headed_env_var] = "true"

            # Build allowed_tools with CLI-specific Bash pattern
            cli_binary = self._cli_tool.binary
            options = ClaudeAgentOptions(
                cwd=work_dir,
                allowed_tools=[
                    f"Bash({cli_binary}:*)",  # Only allow this CLI's commands
                    "Bash(echo:*)",            # Completion protocol
                    "Bash(cat:*)",             # Read reference files
                    "Bash(ls:*)",              # List directories
                    "Bash(pwd:*)",             # Check working directory
                    "Bash(mkdir:*)",           # Create directories
                ],
                # Explicitly disable Claude Code built-in tools that would
                # bypass the CLI under test. The benchmark must measure the
                # CLI tool's capability, not Claude Code's built-in web tools.
                disallowed_tools=[
                    "WebFetch",
                    "WebSearch",
                    "Agent",
                    "Read",
                    "Write",
                    "Edit",
                    "Glob",
                    "Grep",
                    "Skill",
                    "NotebookEdit",
                ],
                permission_mode="bypassPermissions",
                max_turns=self._config.max_turns,
                max_budget_usd=self._config.max_budget_usd,
                model=self._config.model,
                cli_path=self._config.cli_path,
                env=env,
                # Disable project settings to prevent Claude Code's own skill
                # system from interfering — we inject the CLI skill directly
                # via system_prompt.
                setting_sources=[],
                hooks={
                    "PreToolUse": [
                        HookMatcher(matcher="Bash", hooks=[pre_hook]),
                    ],
                    "PostToolUse": [
                        HookMatcher(matcher="Bash", hooks=[post_hook]),
                    ],
                },
            )

            start_time = time.monotonic()
            final_message: ResultMessage | None = None
            total_input_tokens = 0
            total_output_tokens = 0
            turn_counter = 0

            task_prompt = f"Complete the following task:\n\n{task_description}"
            self._log(f"Task: {task_description}")
            self._log(f"CLI tool: {self._cli_tool.name}, model: {self._config.model}")

            async for message in query(prompt=task_prompt, options=options):
                msg_type = type(message).__name__

                if isinstance(message, AssistantMessage):
                    turn_counter += 1
                    # Track token usage
                    if message.usage:
                        total_input_tokens += message.usage.get("input_tokens", 0)
                        total_output_tokens += message.usage.get("output_tokens", 0)
                        elapsed = time.monotonic() - start_time
                        self._log(
                            f"  Turn {turn_counter}: "
                            f"tokens={total_input_tokens}+{total_output_tokens}, "
                            f"elapsed={elapsed:.1f}s"
                        )

                    # Log and record each content block
                    for block in message.content:
                        if hasattr(block, "name"):
                            # Tool use block
                            tool_input = getattr(block, "input", {})
                            if block.name == "Bash":
                                cmd = tool_input.get("command", "")
                                self._log(f"  >> Bash: {cmd[:300]}")
                            else:
                                # Log non-Bash tools with their input
                                input_str = str(tool_input)[:300]
                                self._log(f"  >> Tool: {block.name} | {input_str}")
                        elif hasattr(block, "text") and block.text:
                            # Text block — agent reasoning
                            self._log(f"  >> Text: {block.text[:200]}")
                            self._tracker.record_step(
                                command="[agent reasoning]",
                                output=block.text,
                                is_error=False,
                            )
                            # Detect captcha/blocking in agent reasoning text
                            if CAPTCHA_PATTERNS.search(block.text):
                                self._tracker.captcha_detected = True

                elif isinstance(message, ResultMessage):
                    final_message = message
                    elapsed = time.monotonic() - start_time
                    stop = getattr(message, "stop_reason", "?")
                    cost = getattr(message, "total_cost_usd", 0) or 0
                    turns = getattr(message, "num_turns", "?")
                    self._log(
                        f"  Done: stop={stop}, turns={turns}, "
                        f"cost=${cost:.3f}, elapsed={elapsed:.1f}s"
                    )
                    result_text = getattr(message, "result", "")
                    if result_text:
                        self._log(f"  Result: {result_text[:200]}")

                elif isinstance(message, SystemMessage):
                    sub = getattr(message, "subtype", "?")
                    self._log(f"  [System] {sub}")

            duration_ms = (time.monotonic() - start_time) * 1000

            return self._build_result(
                final_message, duration_ms, total_input_tokens, total_output_tokens
            )
        finally:
            # Clean up temp working directory
            shutil.rmtree(work_dir, ignore_errors=True)

    def _build_result(
        self,
        message: ResultMessage | None,
        duration_ms: float,
        total_input_tokens: int,
        total_output_tokens: int,
    ) -> AgentResult:
        """Convert SDK result + tracker state into AgentResult."""
        if message is None:
            return AgentResult(
                success=False,
                answer=None,
                failure_reason="No result message received from Agent SDK",
                steps=tuple(self._tracker.to_trace()),
                screenshots=tuple(self._tracker.screenshots),
                captcha_encountered=self._tracker.captcha_detected,
                task_impossible=self._tracker.task_impossible,
                token_usage={"input_tokens": 0, "output_tokens": 0},
                cost_usd=0.0,
                duration_ms=duration_ms,
                num_turns=0,
            )

        # Determine success/failure from SDK stop reason
        stop_reason = getattr(message, "stop_reason", "")
        is_success = stop_reason in ("end_turn", "")
        failure_reason: str | None = None

        if stop_reason == "max_turns":
            failure_reason = f"Reached maximum turn limit ({self._config.max_turns})"
            is_success = False

        # Use tracker's final_result if detected, else use SDK result text
        answer = self._tracker.final_result or getattr(message, "result", None)

        # Check for TASK_IMPOSSIBLE signal in answer
        if answer and "TASK_IMPOSSIBLE:" in answer:
            self._tracker.task_impossible = True

        # Extract cost from SDK result
        cost_usd = getattr(message, "total_cost_usd", 0.0) or 0.0

        # Prefer SDK-reported token counts, fallback to accumulated
        sdk_usage = getattr(message, "usage", None)
        if sdk_usage and isinstance(sdk_usage, dict):
            token_usage = {
                "input_tokens": sdk_usage.get("input_tokens", total_input_tokens),
                "output_tokens": sdk_usage.get("output_tokens", total_output_tokens),
            }
        else:
            token_usage = {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            }

        num_turns = getattr(message, "num_turns", len(self._tracker.steps))
        sdk_duration = getattr(message, "duration_ms", None)

        return AgentResult(
            success=is_success,
            answer=answer,
            failure_reason=failure_reason,
            steps=tuple(self._tracker.to_trace()),
            screenshots=tuple(self._tracker.screenshots),
            captcha_encountered=self._tracker.captcha_detected,
            task_impossible=self._tracker.task_impossible,
            token_usage=token_usage,
            cost_usd=cost_usd,
            duration_ms=sdk_duration or duration_ms,
            num_turns=num_turns,
        )

    def _build_timeout_result(self, start_time: float) -> AgentResult:
        """Build result for timed-out execution."""
        duration_ms = (time.monotonic() - start_time) * 1000
        return AgentResult(
            success=False,
            answer=None,
            failure_reason=f"Task timed out after {self._config.timeout_seconds}s",
            steps=tuple(self._tracker.to_trace()),
            screenshots=tuple(self._tracker.screenshots),
            captcha_encountered=self._tracker.captcha_detected,
            task_impossible=self._tracker.task_impossible,
            token_usage={"input_tokens": 0, "output_tokens": 0},
            cost_usd=0.0,
            duration_ms=duration_ms,
            num_turns=len(self._tracker.steps),
        )

    async def _close_existing_browser(self) -> None:
        """Close any existing browser daemon before starting a new task.

        Daemon-based CLIs (like agent-browser) keep a persistent browser
        process. If it was started headless, subsequent --headed flags are
        ignored. Closing it first ensures the correct mode on next launch.
        """
        cmd = self._cli_tool.close_command
        if not cmd:
            return

        self._log(f"Closing existing browser daemon: {cmd}")
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception:
            pass  # Ignore errors — daemon may not be running
