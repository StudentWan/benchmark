"""CLI Runner abstraction layer for browser automation tools.

Provides a uniform interface for playwright-cli and phantomwright-cli,
enabling the benchmark to swap CLI backends without changing agent logic.
"""

import asyncio
import shutil
import sys
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    """Result of a CLI command execution."""

    stdout: str
    stderr: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """Combined stdout + stderr for display."""
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append(self.stderr.strip())
        return "\n".join(parts)


class CliRunner(ABC):
    """Abstract interface for CLI browser automation tools.

    Subclasses implement the command-line specifics for each tool
    (playwright-cli, phantomwright-cli, etc.) while the agent layer
    works against this uniform interface.
    """

    BINARY_NAME: str = ""  # Override in subclasses

    def __init__(
        self,
        session_name: str | None = None,
        headless: bool = True,
        output_dir: Path | None = None,
    ):
        self.session_name = session_name or f"bench_{uuid.uuid4().hex[:8]}"
        self.headless = headless
        self.output_dir = output_dir or Path(".")
        self._screenshot_counter = 0

    @property
    @abstractmethod
    def name(self) -> str:
        """Framework name for result file naming (e.g. 'PlaywrightCLI')."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Framework version string."""

    @abstractmethod
    async def start(self, url: str | None = None) -> str:
        """Open browser session. Returns initial output (snapshot, etc.)."""

    @abstractmethod
    async def execute(
        self, command: str, args: list[str] | None = None, timeout: float = 30.0
    ) -> CommandResult:
        """Run a CLI command. Returns stdout/stderr/exit_code."""

    @abstractmethod
    async def screenshot(self, filename: str | None = None) -> Path:
        """Take a screenshot. Returns the file path."""

    @abstractmethod
    async def snapshot(self) -> str:
        """Get page accessibility snapshot content."""

    @abstractmethod
    async def close(self) -> None:
        """Close the browser session and clean up."""

    # -- Command translation (override in subclasses where CLI syntax differs) --

    def translate_navigate(self, url: str) -> tuple[str, list[str]]:
        """Return (command, args) for navigating to a URL."""
        return ("goto", [url])

    def translate_scroll(self, direction: str, amount: int) -> tuple[str, list[str]]:
        """Return (command, args) for scrolling."""
        dy = amount if direction == "down" else -amount
        return ("mousewheel", ["0", str(dy)])

    @classmethod
    def extra_tools(cls) -> list[dict]:
        """Return additional tool definitions supported by this backend."""
        return []

    # -- Shared subprocess execution --

    async def _run_raw(
        self, cmd: list[str], timeout: float = 30.0
    ) -> CommandResult:
        """Execute a raw subprocess command."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return CommandResult(
                    stdout="",
                    stderr=f"Command timed out after {timeout}s: {' '.join(cmd)}",
                    exit_code=-1,
                )
            return CommandResult(
                stdout=stdout_bytes.decode(errors="replace"),
                stderr=stderr_bytes.decode(errors="replace"),
                exit_code=proc.returncode or 0,
            )
        except FileNotFoundError:
            return CommandResult(
                stdout="",
                stderr=f"CLI binary not found: {cmd[0]}. Is it installed globally?",
                exit_code=-2,
            )


class PlaywrightCliRunner(CliRunner):
    """playwright-cli subprocess wrapper.

    Drives Microsoft's playwright-cli via asyncio subprocesses.
    Each instance manages a named session for isolation.
    Falls back to 'npx playwright-cli' if the global binary is not found.
    """

    BINARY_NAME = "playwright-cli"
    _CLI_CMD: list[str] | None = None  # Resolved command prefix
    _VERSION: str | None = None

    @classmethod
    async def _resolve_cli(cls) -> list[str]:
        """Find the working playwright-cli command."""
        if cls._CLI_CMD is not None:
            return cls._CLI_CMD

        # On Windows, shutil.which finds .cmd/.bat wrappers correctly
        resolved = shutil.which("playwright-cli")
        if resolved:
            cls._CLI_CMD = [resolved]
            # Get version
            try:
                proc = await asyncio.create_subprocess_exec(
                    resolved, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                if proc.returncode == 0:
                    cls._VERSION = stdout.decode(errors="replace").strip()
            except (asyncio.TimeoutError, OSError):
                pass
            return cls._CLI_CMD

        # Try npx fallback
        npx = shutil.which("npx")
        if npx:
            try:
                proc = await asyncio.create_subprocess_exec(
                    npx, "playwright-cli", "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode == 0:
                    cls._CLI_CMD = [npx, "playwright-cli"]
                    cls._VERSION = stdout.decode(errors="replace").strip()
                    return cls._CLI_CMD
            except (asyncio.TimeoutError, OSError):
                pass

        raise FileNotFoundError(
            "playwright-cli not found. Install it with: npm install -g @playwright/cli@latest"
        )

    @property
    def name(self) -> str:
        return "PlaywrightCLI"

    @property
    def version(self) -> str:
        return self._VERSION or "unknown"

    async def start(self, url: str | None = None) -> str:
        # Resolve CLI command and cache version
        await self._resolve_cli()

        args = []
        if not self.headless:
            args.append("--headed")
        if url:
            args.append(url)
        result = await self.execute("open", args, timeout=60.0)
        return result.output

    async def execute(
        self, command: str, args: list[str] | None = None, timeout: float = 30.0
    ) -> CommandResult:
        cli_cmd = await self._resolve_cli()
        cmd = [
            *cli_cmd,
            f"-s={self.session_name}",
            command,
            *(args or []),
        ]
        return await self._run_raw(cmd, timeout=timeout)

    async def screenshot(self, filename: str | None = None) -> Path:
        self._screenshot_counter += 1
        if filename is None:
            filename = f"screenshot_{self._screenshot_counter:04d}.png"

        screenshot_dir = self.output_dir / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        filepath = screenshot_dir / filename

        await self.execute(
            "screenshot", [f"--filename={filepath}"], timeout=15.0
        )
        return filepath

    async def snapshot(self) -> str:
        result = await self.execute("snapshot", timeout=15.0)
        output = result.output

        # playwright-cli returns a reference to a YAML file like:
        # [Snapshot](.playwright-cli/page-2026-xxx.yml)
        # Try to read the actual YAML file content for richer data
        import re
        match = re.search(r'\[Snapshot\]\(([^)]+\.yml)\)', output)
        if match:
            yml_path = Path(match.group(1))
            if yml_path.exists():
                try:
                    content = yml_path.read_text(encoding="utf-8")
                    # Prepend page info (URL, title) from the output
                    header_lines = []
                    for line in output.split("\n"):
                        if line.startswith("- Page ") or line.startswith("### Page"):
                            header_lines.append(line)
                    header = "\n".join(header_lines)
                    return f"{header}\n\n{content}" if header else content
                except Exception:
                    pass

        return output

    async def close(self) -> None:
        try:
            await self.execute("close", timeout=10.0)
        except Exception:
            # Best-effort cleanup
            pass


class PhantomwrightCliRunner(CliRunner):
    """phantomwright-cli subprocess wrapper.

    Drives phantomwright-cli via asyncio subprocesses.
    Uses daemon-based sessions for persistent browser state,
    human behavior simulation, and Cloudflare CAPTCHA solving.
    """

    BINARY_NAME = "phantomwright-cli"
    _CLI_CMD: list[str] | None = None
    _VERSION: str | None = None

    @classmethod
    async def _resolve_cli(cls) -> list[str]:
        """Find the working phantomwright-cli command."""
        if cls._CLI_CMD is not None:
            return cls._CLI_CMD

        resolved = shutil.which("phantomwright-cli")
        if resolved:
            cls._CLI_CMD = [resolved]
            try:
                proc = await asyncio.create_subprocess_exec(
                    resolved, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                if proc.returncode == 0:
                    cls._VERSION = stdout.decode(errors="replace").strip()
            except (asyncio.TimeoutError, OSError):
                pass
            return cls._CLI_CMD

        raise FileNotFoundError(
            "phantomwright-cli not found. Is it installed and in your PATH?"
        )

    @property
    def name(self) -> str:
        return "PhantomwrightCLI"

    @property
    def version(self) -> str:
        return self._VERSION or "unknown"

    async def start(self, url: str | None = None) -> str:
        await self._resolve_cli()
        # phantomwright-cli defaults to headed; --headless is a global flag
        # handled in execute(). No --headed flag needed on `open`.
        args = [url] if url else []
        result = await self.execute("open", args, timeout=60.0)
        return result.output

    async def execute(
        self, command: str, args: list[str] | None = None, timeout: float = 30.0
    ) -> CommandResult:
        cli_cmd = await self._resolve_cli()
        # phantomwright-cli uses `-s <name>` (space-separated)
        # and --headless as a global flag (before the command)
        # Force --channel chrome to match playwright-cli's default browser
        global_flags = ["-s", self.session_name, "--channel", "chrome"]
        if self.headless:
            global_flags.append("--headless")
        cmd = [*cli_cmd, *global_flags, command, *(args or [])]
        return await self._run_raw(cmd, timeout=timeout)

    async def screenshot(self, filename: str | None = None) -> Path:
        self._screenshot_counter += 1
        if filename is None:
            filename = f"screenshot_{self._screenshot_counter:04d}.png"

        screenshot_dir = self.output_dir / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        filepath = screenshot_dir / filename

        # phantomwright-cli uses -o, not --filename=
        await self.execute(
            "screenshot", ["-o", str(filepath)], timeout=15.0
        )
        return filepath

    async def snapshot(self) -> str:
        result = await self.execute("snapshot", timeout=15.0)
        # phantomwright-cli outputs the accessibility tree inline
        # (no YAML file reference like playwright-cli)
        return result.output

    async def close(self) -> None:
        try:
            await self.execute(
                "session-stop", [self.session_name], timeout=10.0
            )
        except Exception:
            try:
                await self.execute("close", timeout=10.0)
            except Exception:
                pass

    # -- Command translations (phantomwright-cli syntax) --

    def translate_navigate(self, url: str) -> tuple[str, list[str]]:
        """phantomwright-cli uses 'open' for navigation."""
        return ("open", [url])

    def translate_scroll(self, direction: str, amount: int) -> tuple[str, list[str]]:
        """phantomwright-cli uses 'scroll [up|down] --by <pixels>'."""
        return ("scroll", [direction, "--by", str(amount)])

    @classmethod
    def extra_tools(cls) -> list[dict]:
        """Return phantomwright-specific tools (cf-solve, wait, etc.)."""
        from agent.tools import PHANTOMWRIGHT_EXTRA_TOOLS
        return PHANTOMWRIGHT_EXTRA_TOOLS
