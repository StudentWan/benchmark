"""CLI tool registry for browser automation benchmarks.

Each CLI tool is defined as an immutable dataclass with its binary name,
allowed command prefixes (for hook validation), skill directory path,
and per-CLI configuration for headed mode, screenshot dirs, and lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

    # -- Per-CLI screenshot configuration --

    screenshot_command: str | None = None
    """Command template to capture a screenshot. Use {dir} as placeholder
    for the output directory. None if the CLI doesn't support screenshots
    or the command is unknown."""

    default_screenshot_dirs: tuple[Path, ...] = ()
    """Default directories where this CLI saves screenshots.
    Used as fallback scan paths for collecting screenshots."""

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
    "browser-use": CliTool(
        name="browser-use",
        binary="browser-use",
        allowed_prefixes=frozenset({"browser-use"}),
        skill_dir=_SKILLS_DIR / "browser-use",
        description="Browser Use CLI - AI-native browser automation",
        headed_env_var=None,
        screenshot_command="browser-use screenshot {dir}/screenshot_{ts}.png",
        default_screenshot_dirs=(),
        close_command="browser-use close",
    ),
    "agent-browser": CliTool(
        name="agent-browser",
        binary="agent-browser",
        allowed_prefixes=frozenset({"agent-browser"}),
        skill_dir=_SKILLS_DIR / "agent-browser",
        description="Vercel agent-browser - browser automation CLI for AI agents",
        headed_env_var="AGENT_BROWSER_HEADED",
        headed_flag="--headed",
        screenshot_command="agent-browser screenshot --screenshot-dir {dir}",
        default_screenshot_dirs=(
            _HOME / ".agent-browser" / "tmp" / "screenshots",
        ),
        close_command="agent-browser close --all",
    ),
    "playwright-cli": CliTool(
        name="playwright-cli",
        binary="playwright-cli",
        allowed_prefixes=frozenset({"playwright-cli"}),
        skill_dir=_SKILLS_DIR / "playwright-cli",
        description="Microsoft Playwright CLI - cross-browser automation",
        headed_env_var=None,
        headed_flag="--headed",
        screenshot_command="playwright-cli screenshot --filename={dir}/screenshot_{ts}.png",
        default_screenshot_dirs=(),
        close_command="playwright-cli close",
    ),
    "patchright-cli": CliTool(
        name="patchright-cli",
        binary="patchright-cli",
        allowed_prefixes=frozenset({"patchright-cli"}),
        skill_dir=_SKILLS_DIR / "patchright-cli",
        description="Patchright CLI - undetected browser automation",
        headed_env_var=None,
        headed_flag="--headed",
        screenshot_command="patchright-cli screenshot --filename={dir}/screenshot_{ts}.png",
        default_screenshot_dirs=(),
        close_command="patchright-cli close-all",
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
