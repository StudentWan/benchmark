"""Browser automation tool definitions and executor for Anthropic API tool_use.

Defines the BROWSER_TOOLS list (Anthropic API format) and execute_tool()
that maps tool calls to CliRunner commands.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.runner import CliRunner

MAX_SNAPSHOT_CHARS = 8000


def _truncate(text: str, limit: int = MAX_SNAPSHOT_CHARS) -> str:
    """Truncate text with a notice if it exceeds the limit."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[... snapshot truncated — use browser_snapshot to refresh]"


# ---------------------------------------------------------------------------
# Tool definitions (Anthropic API format)
# ---------------------------------------------------------------------------

BROWSER_TOOLS: list[dict] = [
    {
        "name": "browser_navigate",
        "description": "Navigate to a URL in the browser.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to navigate to"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_click",
        "description": "Click an element identified by its ref from the snapshot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Element ref (e.g. 'e15') from the snapshot",
                },
            },
            "required": ["ref"],
        },
    },
    {
        "name": "browser_fill",
        "description": "Clear an input field and type new text into it. Use for form fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Element ref of the input field",
                },
                "text": {"type": "string", "description": "Text to fill in"},
                "submit": {
                    "type": "boolean",
                    "description": "Press Enter after filling (default: false)",
                    "default": False,
                },
            },
            "required": ["ref", "text"],
        },
    },
    {
        "name": "browser_type",
        "description": "Type text into the currently focused element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "browser_press",
        "description": "Press a keyboard key (e.g. Enter, Tab, Escape, ArrowDown).",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to press"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "browser_snapshot",
        "description": "Get the current page accessibility snapshot with element refs.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_screenshot",
        "description": "Take a screenshot of the current page.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_select",
        "description": "Select an option from a dropdown element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref of the select"},
                "value": {"type": "string", "description": "Option value to select"},
            },
            "required": ["ref", "value"],
        },
    },
    {
        "name": "browser_hover",
        "description": "Hover over an element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref to hover over"},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the page up or down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction",
                    "default": "down",
                },
                "amount": {
                    "type": "integer",
                    "description": "Pixels to scroll (default: 500)",
                    "default": 500,
                },
            },
        },
    },
    {
        "name": "browser_go_back",
        "description": "Navigate back in browser history.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_go_forward",
        "description": "Navigate forward in browser history.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_reload",
        "description": "Reload the current page.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_tab_list",
        "description": "List all open browser tabs.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_tab_new",
        "description": "Open a new browser tab, optionally navigating to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to open in the new tab (optional)",
                },
            },
        },
    },
    {
        "name": "browser_tab_select",
        "description": "Switch to a specific browser tab by index.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": "Tab index (0-based)",
                },
            },
            "required": ["index"],
        },
    },
    {
        "name": "browser_tab_close",
        "description": "Close a browser tab by index (defaults to current tab).",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": "Tab index to close (optional, defaults to current)",
                },
            },
        },
    },
    {
        "name": "browser_eval",
        "description": "Execute JavaScript on the page or a specific element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "JavaScript expression to evaluate",
                },
                "ref": {
                    "type": "string",
                    "description": "Element ref to evaluate against (optional)",
                },
            },
            "required": ["expression"],
        },
    },
    {
        "name": "task_complete",
        "description": "Signal that the task is complete. Provide the final result or answer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "The final result, answer, or summary of what was accomplished",
                },
            },
            "required": ["result"],
        },
    },
]


# ---------------------------------------------------------------------------
# PhantomWright-only extra tools (conditionally added via runner.extra_tools())
# ---------------------------------------------------------------------------

PHANTOMWRIGHT_EXTRA_TOOLS: list[dict] = [
    {
        "name": "browser_cf_solve",
        "description": "Attempt to solve a Cloudflare CAPTCHA/Turnstile challenge. Use when the page shows a Cloudflare verification screen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timeout_ms": {
                    "type": "integer",
                    "description": "Timeout in milliseconds (default: 30000)",
                    "default": 30000,
                },
            },
        },
    },
    {
        "name": "browser_wait",
        "description": "Wait for a specific element to appear on the page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Element ref to wait for",
                },
            },
            "required": ["ref"],
        },
    },
    {
        "name": "browser_wait_for_load",
        "description": "Wait for the page to finish loading (network idle).",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


def _describe_tool_call(tool_name: str, tool_input: dict) -> str:
    """Generate a human-readable description of a tool call for step logging."""
    match tool_name:
        case "browser_navigate":
            return f"Navigated to {tool_input.get('url', '?')}"
        case "browser_click":
            return f"Clicked element {tool_input.get('ref', '?')}"
        case "browser_fill":
            ref = tool_input.get("ref", "?")
            text = tool_input.get("text", "")
            submit = " and submitted" if tool_input.get("submit") else ""
            return f"Filled '{text}' into {ref}{submit}"
        case "browser_type":
            text = tool_input.get("text", "")
            return f"Typed '{text}'"
        case "browser_press":
            return f"Pressed {tool_input.get('key', '?')}"
        case "browser_snapshot":
            return "Took page snapshot"
        case "browser_screenshot":
            return "Took screenshot"
        case "browser_select":
            return f"Selected '{tool_input.get('value', '?')}' in {tool_input.get('ref', '?')}"
        case "browser_hover":
            return f"Hovered over {tool_input.get('ref', '?')}"
        case "browser_scroll":
            direction = tool_input.get("direction", "down")
            amount = tool_input.get("amount", 500)
            return f"Scrolled {direction} by {amount}px"
        case "browser_go_back":
            return "Navigated back"
        case "browser_go_forward":
            return "Navigated forward"
        case "browser_reload":
            return "Reloaded page"
        case "browser_tab_list":
            return "Listed browser tabs"
        case "browser_tab_new":
            url = tool_input.get("url", "")
            return f"Opened new tab{': ' + url if url else ''}"
        case "browser_tab_select":
            return f"Switched to tab {tool_input.get('index', '?')}"
        case "browser_tab_close":
            idx = tool_input.get("index")
            return f"Closed tab {idx}" if idx is not None else "Closed current tab"
        case "browser_eval":
            expr = tool_input.get("expression", "?")
            return f"Evaluated JS: {expr}"
        case "task_complete":
            result = tool_input.get("result", "")
            return f"Task completed: {result}"
        case "browser_cf_solve":
            return f"Solving Cloudflare CAPTCHA (timeout={tool_input.get('timeout_ms', 30000)}ms)"
        case "browser_wait":
            return f"Waiting for element {tool_input.get('ref', '?')}"
        case "browser_wait_for_load":
            return "Waiting for page load (network idle)"
        case _:
            return f"Unknown tool: {tool_name}"


async def execute_tool(
    runner: CliRunner,
    tool_name: str,
    tool_input: dict,
    screenshot_paths: list[Path],
) -> tuple[str, bool]:
    """Execute a tool call via the CLI runner.

    Args:
        runner: The CLI runner instance.
        tool_name: Name of the tool to execute.
        tool_input: Tool input parameters.
        screenshot_paths: Mutable list to append screenshot paths to.

    Returns:
        Tuple of (result_text, is_task_complete).

    Raises:
        Exception: If the command fails critically.
    """
    if tool_name == "task_complete":
        # Force a final screenshot before completing
        try:
            path = await runner.screenshot(filename="final.png")
            if path.exists():
                screenshot_paths.append(path)
        except Exception:
            pass
        return tool_input.get("result", "Task completed"), True

    if tool_name == "browser_navigate":
        url = tool_input["url"]
        cmd, args = runner.translate_navigate(url)
        result = await runner.execute(cmd, args)
        # Auto-screenshot after navigation
        try:
            path = await runner.screenshot()
            if path.exists():
                screenshot_paths.append(path)
        except Exception:
            pass
        snapshot = await runner.snapshot()
        if result.ok:
            return f"Navigated to {url}.\n\nPage snapshot:\n{_truncate(snapshot)}", False
        return f"Navigation error: {result.output}\n\nPage snapshot:\n{_truncate(snapshot)}", False

    if tool_name == "browser_click":
        ref = tool_input["ref"]
        result = await runner.execute("click", [ref])
        # Auto-screenshot after click
        try:
            path = await runner.screenshot()
            if path.exists():
                screenshot_paths.append(path)
        except Exception:
            pass
        snapshot = await runner.snapshot()
        if result.ok:
            return f"Clicked {ref}.\n\nPage snapshot:\n{_truncate(snapshot)}", False
        return f"Click error on {ref}: {result.output}\n\nPage snapshot:\n{_truncate(snapshot)}", False

    if tool_name == "browser_fill":
        ref = tool_input["ref"]
        text = tool_input["text"]
        args = [ref, text]
        if tool_input.get("submit"):
            args.append("--submit")
        result = await runner.execute("fill", args)
        snapshot = await runner.snapshot()
        if tool_input.get("submit"):
            try:
                path = await runner.screenshot()
                if path.exists():
                    screenshot_paths.append(path)
            except Exception:
                pass
        if result.ok:
            return f"Filled '{text[:50]}' into {ref}.\n\nPage snapshot:\n{_truncate(snapshot)}", False
        return f"Fill error on {ref}: {result.output}", False

    if tool_name == "browser_type":
        text = tool_input["text"]
        result = await runner.execute("type", [text])
        if result.ok:
            return f"Typed '{text[:50]}'.", False
        return f"Type error: {result.output}", False

    if tool_name == "browser_press":
        key = tool_input["key"]
        result = await runner.execute("press", [key])
        snapshot = await runner.snapshot()
        if result.ok:
            return f"Pressed {key}.\n\nPage snapshot:\n{_truncate(snapshot)}", False
        return f"Press error: {result.output}", False

    if tool_name == "browser_snapshot":
        snapshot = await runner.snapshot()
        return f"Page snapshot:\n{_truncate(snapshot, 16000)}", False

    if tool_name == "browser_screenshot":
        try:
            path = await runner.screenshot()
            if path.exists():
                screenshot_paths.append(path)
            return "Screenshot taken.", False
        except Exception as e:
            return f"Screenshot error: {e}", False

    if tool_name == "browser_select":
        ref = tool_input["ref"]
        value = tool_input["value"]
        result = await runner.execute("select", [ref, value])
        snapshot = await runner.snapshot()
        if result.ok:
            return f"Selected '{value}' in {ref}.\n\nPage snapshot:\n{_truncate(snapshot)}", False
        return f"Select error: {result.output}", False

    if tool_name == "browser_hover":
        ref = tool_input["ref"]
        result = await runner.execute("hover", [ref])
        snapshot = await runner.snapshot()
        if result.ok:
            return f"Hovered over {ref}.\n\nPage snapshot:\n{_truncate(snapshot)}", False
        return f"Hover error: {result.output}", False

    if tool_name == "browser_scroll":
        direction = tool_input.get("direction", "down")
        amount = tool_input.get("amount", 500)
        cmd, args = runner.translate_scroll(direction, amount)
        result = await runner.execute(cmd, args)
        snapshot = await runner.snapshot()
        if result.ok:
            return f"Scrolled {direction} by {amount}px.\n\nPage snapshot:\n{_truncate(snapshot)}", False
        return f"Scroll error: {result.output}", False

    if tool_name == "browser_go_back":
        result = await runner.execute("go-back")
        snapshot = await runner.snapshot()
        try:
            path = await runner.screenshot()
            if path.exists():
                screenshot_paths.append(path)
        except Exception:
            pass
        return f"Navigated back.\n\nPage snapshot:\n{_truncate(snapshot)}", False

    if tool_name == "browser_go_forward":
        result = await runner.execute("go-forward")
        snapshot = await runner.snapshot()
        return f"Navigated forward.\n\nPage snapshot:\n{_truncate(snapshot)}", False

    if tool_name == "browser_reload":
        result = await runner.execute("reload")
        snapshot = await runner.snapshot()
        try:
            path = await runner.screenshot()
            if path.exists():
                screenshot_paths.append(path)
        except Exception:
            pass
        return f"Page reloaded.\n\nPage snapshot:\n{_truncate(snapshot)}", False

    if tool_name == "browser_tab_list":
        result = await runner.execute("tab-list")
        return f"Open tabs:\n{result.output}", False

    if tool_name == "browser_tab_new":
        url = tool_input.get("url")
        args = [url] if url else []
        result = await runner.execute("tab-new", args)
        snapshot = await runner.snapshot()
        return f"New tab opened.\n\nPage snapshot:\n{_truncate(snapshot)}", False

    if tool_name == "browser_tab_select":
        index = str(tool_input["index"])
        result = await runner.execute("tab-select", [index])
        snapshot = await runner.snapshot()
        return f"Switched to tab {index}.\n\nPage snapshot:\n{_truncate(snapshot)}", False

    if tool_name == "browser_tab_close":
        index = tool_input.get("index")
        args = [str(index)] if index is not None else []
        result = await runner.execute("tab-close", args)
        return f"Tab closed.", False

    if tool_name == "browser_eval":
        expression = tool_input["expression"]
        ref = tool_input.get("ref")
        args = [expression]
        if ref:
            args.append(ref)
        result = await runner.execute("eval", args)
        if result.ok:
            return f"JS result:\n{result.output}", False
        return f"JS eval error: {result.output}", False

    # -- PhantomWright-only tools --

    if tool_name == "browser_cf_solve":
        timeout_ms = str(tool_input.get("timeout_ms", 30000))
        result = await runner.execute(
            "cf-solve", ["--timeout", timeout_ms], timeout=60.0
        )
        snapshot = await runner.snapshot()
        if result.ok:
            return (
                f"Cloudflare challenge solved.\n\n"
                f"Page snapshot:\n{_truncate(snapshot)}",
                False,
            )
        return f"CF-solve failed: {result.output}", False

    if tool_name == "browser_wait":
        ref = tool_input["ref"]
        result = await runner.execute("wait", [ref], timeout=35.0)
        if result.ok:
            return f"Element {ref} appeared.", False
        return f"Wait error: {result.output}", False

    if tool_name == "browser_wait_for_load":
        result = await runner.execute(
            "wait-for-load", ["--state", "networkidle"], timeout=35.0
        )
        if result.ok:
            return "Page finished loading (network idle).", False
        return f"Wait-for-load error: {result.output}", False

    return f"Unknown tool: {tool_name}", False
