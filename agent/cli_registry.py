"""CLI tool registry for browser automation benchmarks.

Each CLI tool is defined as an immutable dataclass with its binary name,
allowed command prefixes (for hook validation), skill directory path,
and per-CLI configuration for headed mode, screenshots, and lifecycle.

Screenshot Design
-----------------
Each CLI tool declares a ``screenshot_command`` template that produces a
screenshot at a **deterministic file path**.  The template uses two
placeholders:

* ``{path}`` — the full output file path (e.g. ``./screenshots/auto_001.png``)
* ``{dir}``  — the output directory (for CLIs that only accept a directory)

The auto-screenshot system calls the command **synchronously** (awaiting
completion) so the resulting file is guaranteed to exist when the path is
recorded into the step tracker.  This eliminates the race condition of the
old fire-and-forget approach where the directory was scanned before the
screenshot file was fully written.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CliTool:
    """Immutable definition of a CLI browser automation tool."""

    name: str
    """Human-readable name (e.g., 'agent-browser')."""

    binary: str
    """Primary binary name for command validation (e.g., 'agent-browser')."""

    allowed_prefixes: frozenset[str]
    """All valid command prefixes — used by PreToolUse hook to validate commands."""

    skill_dir: Path
    """Path to the skill directory (contains SKILL.md + references/ + templates/)."""

    description: str
    """One-line description for logs and UI."""

    # -- Per-CLI headed mode configuration --

    headed_env_var: str | None = None
    """Environment variable to enable headed mode (e.g., 'AGENT_BROWSER_HEADED').
    Set to 'true' when --headed is requested. None if not supported via env."""

    headed_flag: str = "--headed"
    """CLI flag to pass for headed mode (e.g., '--headed')."""

    # -- Per-CLI session isolation --

    session_env_var: str | None = None
    """Environment variable to set the browser session name.
    Used to isolate concurrent tasks so they don't share the same browser.
    None if the CLI doesn't support session isolation via env."""

    session_flag: str | None = None
    """CLI flag template for session isolation (e.g., '-s={session}').
    Used in the close command to close the task-specific session."""

    # -- Per-CLI screenshot configuration --

    screenshot_command: str | None = None
    """Command template to capture a screenshot.

    Placeholders:
      {path} — full output file path (preferred, deterministic)
      {dir}  — output directory (for CLIs that generate their own filename)

    The command is awaited synchronously so the file is guaranteed to exist
    when recording completes.  Set to None if the CLI doesn't support
    screenshots."""

    screenshot_returns_path: bool = True
    """If True, the screenshot file is at the exact ``{path}`` we specified.
    If False (e.g. agent-browser with --screenshot-dir), the CLI generates
    its own filename inside ``{dir}`` and we must scan for it."""

    # -- Per-CLI lifecycle commands --

    close_command: str | None = None
    """Command to close/kill existing browser daemon before starting.
    None if the CLI doesn't use a daemon model."""


# ---------------------------------------------------------------------------
# Canonical registry
# ---------------------------------------------------------------------------

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
_HOME = Path.home()

CLI_REGISTRY: dict[str, CliTool] = {
    # ----- browser-use -----
    # Known issue: browser-use 0.6.x CLI is broken on some platforms
    # (outputs only dotenv initialization messages, no actual browser
    # interaction).  screenshot_command is set to the documented form but
    # may silently fail; the system degrades gracefully (no screenshot
    # collected, benchmark continues).
    "browser-use": CliTool(
        name="browser-use",
        binary="browser-use",
        allowed_prefixes=frozenset({"browser-use"}),
        skill_dir=_SKILLS_DIR / "browser-use",
        description="Browser Use CLI - AI-native browser automation",
        headed_env_var=None,
        screenshot_command="browser-use screenshot {path}",
        screenshot_returns_path=True,
        close_command="browser-use close",
    ),
    # ----- agent-browser -----
    # agent-browser accepts either an explicit path or --screenshot-dir.
    # We use the explicit path form for deterministic file naming.
    "agent-browser": CliTool(
        name="agent-browser",
        binary="agent-browser",
        allowed_prefixes=frozenset({"agent-browser"}),
        skill_dir=_SKILLS_DIR / "agent-browser",
        description="Vercel agent-browser - browser automation CLI for AI agents",
        headed_env_var="AGENT_BROWSER_HEADED",
        headed_flag="--headed",
        session_env_var="AGENT_BROWSER_SESSION",
        screenshot_command="agent-browser screenshot {path}",
        screenshot_returns_path=True,
        close_command="agent-browser close",
    ),
    # ----- playwright-cli -----
    "playwright-cli": CliTool(
        name="playwright-cli",
        binary="playwright-cli",
        allowed_prefixes=frozenset({"playwright-cli"}),
        skill_dir=_SKILLS_DIR / "playwright-cli",
        description="Microsoft Playwright CLI - cross-browser automation",
        headed_env_var=None,
        headed_flag="--headed",
        session_flag="-s={session}",
        screenshot_command="playwright-cli screenshot --filename={path}",
        screenshot_returns_path=True,
        close_command="playwright-cli close",
    ),
    # ----- patchright-cli -----
    "patchright-cli": CliTool(
        name="patchright-cli",
        binary="patchright-cli",
        allowed_prefixes=frozenset({"patchright-cli"}),
        skill_dir=_SKILLS_DIR / "patchright-cli",
        description="Patchright CLI - undetected browser automation",
        headed_env_var=None,
        headed_flag="--headed",
        session_flag="-s={session}",
        screenshot_command="patchright-cli screenshot --filename={path}",
        screenshot_returns_path=True,
        close_command="patchright-cli close",
    ),
}


def get_cli_tool(name: str) -> CliTool:
    """Retrieve a CLI tool definition by name.

    Raises:
        KeyError: If the tool name is not registered.
    """
    if name not in CLI_REGISTRY:
        available = ", ".join(sorted(CLI_REGISTRY.keys()))
        raise KeyError(f"Unknown CLI tool '{name}'. Available: {available}")
    return CLI_REGISTRY[name]


def list_cli_tools() -> list[str]:
    """Return sorted list of registered CLI tool names."""
    return sorted(CLI_REGISTRY.keys())
