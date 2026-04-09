"""Hooks for CLI browser automation benchmarks.

Provides:
- PreToolUse hook: Hard enforcement of CLI tool isolation via command validation.
- PostToolUse hook: Step tracking, screenshot collection, and signal detection.
- StepTracker: Mutable accumulator for execution steps during a single agent run.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.cli_registry import CliTool


# ---------------------------------------------------------------------------
# Always-allowed utility commands (needed for output and basic file ops)
# ---------------------------------------------------------------------------

ALWAYS_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "echo",   # Completion protocol: echo "TASK_COMPLETE: ..."
    "cat",    # Reading files
    "ls",     # Listing directories
    "pwd",    # Checking working directory
    "mkdir",  # Creating directories (for screenshots)
})

# Max characters to keep in step output for the judge trace.
# Must be large enough for JS eval results to be useful.
STEP_OUTPUT_MAX_CHARS = 8000
CAPTCHA_PATTERNS = re.compile(
    r"captcha|recaptcha|hcaptcha|cloudflare|verify.+human|"
    r"bot.+detection|anti.?bot|turnstile|blocked.+by.+protection|"
    r"access.+denied|challenge.+page",
    re.IGNORECASE,
)

TASK_COMPLETE_PATTERN = re.compile(r"TASK_COMPLETE:\s*(.+)", re.DOTALL)
TASK_IMPOSSIBLE_PATTERN = re.compile(r"TASK_IMPOSSIBLE:\s*(.+)", re.DOTALL)

# Commands that should trigger an auto-screenshot after execution.
# These are the "interesting" moments: navigation, clicks, form submits.
AUTO_SCREENSHOT_KEYWORDS = re.compile(
    r"\b(open|goto|navigate|click|dblclick|submit|fill|select|check|uncheck|press)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Step tracking
# ---------------------------------------------------------------------------

@dataclass
class StepTracker:
    """Mutable accumulator for execution steps. Lives for one agent run."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    captcha_detected: bool = False
    task_impossible: bool = False
    final_result: str | None = None
    _step_counter: int = field(default=0, init=False, repr=False)
    _known_screenshots: set[str] = field(default_factory=set, init=False, repr=False)

    def record_step(
        self,
        command: str,
        output: str,
        is_error: bool = False,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Record a single execution step."""
        self._step_counter += 1
        ts = timestamp or time.time()

        # Collect new screenshots from output directory
        new_screenshots = self._scan_new_screenshots()

        step = {
            "step": self._step_counter,
            "command": command,
            "output": output[:STEP_OUTPUT_MAX_CHARS],  # Truncate very long outputs
            "is_error": is_error,
            "screenshots": new_screenshots,
            "timestamp": ts,
        }
        self.steps.append(step)
        return step

    def set_screenshot_dir(self, screenshot_dir: Path) -> None:
        """Set the primary directory to scan for new screenshots."""
        self._screenshot_dir = screenshot_dir

    def add_screenshot_dir(self, extra_dir: Path) -> None:
        """Add an extra directory to scan for screenshots.

        Used for CLI tools that save screenshots to their own default
        directories (e.g. agent-browser saves to ~/.agent-browser/tmp/screenshots/).
        """
        if not hasattr(self, "_extra_screenshot_dirs"):
            self._extra_screenshot_dirs: list[Path] = []
        self._extra_screenshot_dirs.append(extra_dir)

    def snapshot_existing_files(self) -> None:
        """Mark all currently existing screenshot files as 'already known'.

        Call this BEFORE the task starts so that only screenshots created
        during the task run are collected. Without this, old screenshots
        from previous runs in the same directory would be picked up.
        """
        dirs_to_scan: list[Path] = []

        screenshot_dir = getattr(self, "_screenshot_dir", None)
        if screenshot_dir is not None and screenshot_dir.exists():
            dirs_to_scan.append(screenshot_dir)

        for extra in getattr(self, "_extra_screenshot_dirs", []):
            if extra.exists():
                dirs_to_scan.append(extra)

        for scan_dir in dirs_to_scan:
            for pattern in ("*.png", "*.jpg", "*.jpeg"):
                for f in scan_dir.glob(pattern):
                    self._known_screenshots.add(str(f))

    def _scan_new_screenshots(self) -> list[str]:
        """Find screenshot files that aren't already tracked."""
        dirs_to_scan: list[Path] = []

        screenshot_dir = getattr(self, "_screenshot_dir", None)
        if screenshot_dir is not None and screenshot_dir.exists():
            dirs_to_scan.append(screenshot_dir)

        for extra in getattr(self, "_extra_screenshot_dirs", []):
            if extra.exists():
                dirs_to_scan.append(extra)

        new_files: list[str] = []
        for scan_dir in dirs_to_scan:
            for pattern in ("*.png", "*.jpg", "*.jpeg"):
                for f in sorted(scan_dir.glob(pattern)):
                    path_str = str(f)
                    if path_str not in self._known_screenshots:
                        self._known_screenshots.add(path_str)
                        self.screenshots.append(path_str)
                        new_files.append(path_str)
        return new_files

    def track_screenshot_from_output(self, output: str) -> list[str]:
        """Extract screenshot file paths mentioned in command output.

        CLI tools often print the path of saved screenshots in their output.
        This captures those paths even when they're outside our screenshot_dir.
        """
        new_files: list[str] = []
        # Common patterns: "Saved screenshot to /path/to/file.png"
        # or just a bare path ending in .png/.jpg
        for match in re.finditer(
            r'(?:saved?\s+(?:screenshot\s+)?(?:to|at|:)\s*)?'
            r'([A-Za-z]:[/\\][^\s"\']+\.(?:png|jpg|jpeg)|'
            r'/[^\s"\']+\.(?:png|jpg|jpeg))',
            output,
            re.IGNORECASE,
        ):
            path_str = match.group(1)
            if path_str not in self._known_screenshots and Path(path_str).exists():
                self._known_screenshots.add(path_str)
                self.screenshots.append(path_str)
                new_files.append(path_str)
        return new_files

    def to_trace(self) -> list[dict[str, Any]]:
        """Convert steps to the trace format expected by judge.py."""
        return list(self.steps)


# ---------------------------------------------------------------------------
# PreToolUse hook — command validation (hard enforcement)
# ---------------------------------------------------------------------------

def _deny(reason: str) -> dict[str, Any]:
    """Build a hook response that denies the tool use."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _extract_first_command(command: str) -> str:
    """Extract the first command/binary name from a shell command string.

    Handles:
    - Simple commands: "browser-use goto https://..."
    - Env var prefixes: "VAR=1 browser-use click #btn"
    - Pipes: "browser-use screenshot | cat"
    - Chained commands: "browser-use click e1 && echo done"
    - Paths: "/usr/bin/browser-use goto ..."

    Returns the binary name without path.
    """
    # Split on shell operators to get the first command segment
    # Handle &&, ||, ;, | as command separators
    first_segment = re.split(r"\s*(?:&&|\|\||;|\|)\s*", command, maxsplit=1)[0].strip()

    tokens = first_segment.split()
    for token in tokens:
        # Skip env var assignments (KEY=VALUE)
        if "=" in token and not token.startswith("-"):
            continue
        # Return the binary name (strip path)
        return Path(token).name
    return tokens[0] if tokens else ""


def create_pre_tool_use_hook(cli_tool: CliTool):
    """Create a PreToolUse hook that validates Bash commands against the allowed CLI.

    This is the HARD enforcement layer. Combined with system prompt constraints,
    it provides dual-layer tool isolation for benchmark fairness.

    Args:
        cli_tool: The CLI tool definition — only commands matching its
            ``allowed_prefixes`` (plus ``ALWAYS_ALLOWED_COMMANDS``) are permitted.

    Returns:
        An async hook function compatible with the Agent SDK's hook system.
    """

    async def pre_tool_use_hook(
        input_data: dict[str, Any],
        tool_use_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")

        # Only validate Bash commands — let the SDK's allowed_tools
        # handle other tool restrictions. Some internal Claude Code tools
        # (like Skill) may bypass allowed_tools and must not be blocked
        # by this hook.
        if tool_name != "Bash":
            return {}  # Pass through — not our responsibility

        command = input_data.get("tool_input", {}).get("command", "").strip()
        if not command:
            return _deny("Empty command is not allowed.")

        first_cmd = _extract_first_command(command)

        # Allow target CLI
        if first_cmd in cli_tool.allowed_prefixes:
            return {}  # Approved

        # Allow basic utilities
        if first_cmd in ALWAYS_ALLOWED_COMMANDS:
            return {}  # Approved

        # Deny everything else
        return _deny(
            f"Command '{first_cmd}' is not allowed. "
            f"You may only use: {', '.join(sorted(cli_tool.allowed_prefixes))} "
            f"(and utilities: {', '.join(sorted(ALWAYS_ALLOWED_COMMANDS))})"
        )

    return pre_tool_use_hook


# ---------------------------------------------------------------------------
# PostToolUse hook — step tracking and signal detection
# ---------------------------------------------------------------------------

def create_post_tool_use_hook(
    cli_tool: CliTool,
    tracker: StepTracker,
    screenshot_dir: Path | None = None,
):
    """Create a PostToolUse hook for tracking execution steps.

    Records every Bash command execution, collects screenshots,
    detects CAPTCHA / completion / impossible-task signals, and
    auto-captures a screenshot after navigation/click commands.

    Args:
        cli_tool: The CLI tool definition (for context).
        tracker: The StepTracker accumulator for this agent run.
        screenshot_dir: Directory to save auto-screenshots. If None,
            uses the CLI tool's default screenshot dir.

    Returns:
        An async hook function compatible with the Agent SDK's hook system.
    """

    async def post_tool_use_hook(
        input_data: dict[str, Any],
        tool_use_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")
        if tool_name != "Bash":
            return {}

        command = input_data.get("tool_input", {}).get("command", "")
        # tool_output may be in input_data or context depending on SDK version
        tool_output = input_data.get("tool_output", {})
        output = ""
        is_error = False

        if isinstance(tool_output, dict):
            output = tool_output.get("stdout", "") + tool_output.get("stderr", "")
            is_error = tool_output.get("exit_code", 0) != 0
        elif isinstance(tool_output, str):
            output = tool_output

        # Record the step
        tracker.record_step(
            command=command,
            output=output,
            is_error=is_error,
        )

        # Auto-screenshot after navigation/click/fill commands
        if (
            not is_error
            and AUTO_SCREENSHOT_KEYWORDS.search(command)
            and "screenshot" not in command.lower()
        ):
            await _auto_screenshot(cli_tool, screenshot_dir)

        # Collect screenshots: scan directory + parse paths from output
        tracker._scan_new_screenshots()
        tracker.track_screenshot_from_output(output)

        # Detect CAPTCHA in output
        if CAPTCHA_PATTERNS.search(output):
            tracker.captcha_detected = True

        # Detect completion signals
        complete_match = TASK_COMPLETE_PATTERN.search(output)
        if complete_match:
            tracker.final_result = complete_match.group(1).strip()[:5000]

        impossible_match = TASK_IMPOSSIBLE_PATTERN.search(output)
        if impossible_match:
            tracker.task_impossible = True
            if tracker.final_result is None:
                tracker.final_result = impossible_match.group(1).strip()[:5000]

        return {}

    return post_tool_use_hook


# ---------------------------------------------------------------------------
# Auto-screenshot helper
# ---------------------------------------------------------------------------

async def _auto_screenshot(cli_tool: CliTool, screenshot_dir: Path | None) -> None:
    """Silently capture a screenshot after an interesting command.

    Uses the CLI tool's ``screenshot_command`` from the registry.
    Runs as a subprocess independent of the Agent SDK loop.
    Failures are logged but never block the benchmark.
    """
    if not cli_tool.screenshot_command:
        return  # CLI doesn't support screenshots

    # Build command from template, replacing {dir} and {ts}
    import time as _time

    ts = str(int(_time.time() * 1000))
    dir_str = str(screenshot_dir) if screenshot_dir else "."
    cmd = cli_tool.screenshot_command.format(dir=dir_str, ts=ts)

    try:
        # Ensure screenshot directory exists
        if screenshot_dir:
            screenshot_dir.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            print(f"[auto-screenshot] FAILED ({proc.returncode}): {cmd}", flush=True)
            if err:
                print(f"[auto-screenshot]   stderr: {err[:200]}", flush=True)
        else:
            print(f"[auto-screenshot] OK: {cmd}", flush=True)
    except asyncio.TimeoutError:
        print(f"[auto-screenshot] TIMEOUT: {cmd}", flush=True)
    except Exception as e:
        print(f"[auto-screenshot] ERROR: {cmd} -> {e}", flush=True)
