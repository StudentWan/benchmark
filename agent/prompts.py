"""System prompt construction for CLI browser automation agents.

Builds dynamic prompts by combining:
1. Role and workflow instructions (preserved from original SYSTEM_PROMPT)
2. CLI-specific skill content (loaded from skill directory)
3. Benchmark constraints (tool isolation, completion protocol)
"""

from __future__ import annotations

from pathlib import Path

from agent.cli_registry import CliTool


def build_system_prompt(cli_tool: CliTool, *, headless: bool = True) -> str:
    """Build the system prompt for the Agent SDK.

    Loads the main SKILL.md from the CLI tool's skill directory and
    lists available reference/template files that the agent can read
    on demand via ``cat``.

    Args:
        cli_tool: The CLI tool definition to build the prompt for.
        headless: Whether to run in headless mode. When False, the prompt
            instructs the agent to use headed mode flags.

    Returns:
        Complete system prompt string.

    Raises:
        FileNotFoundError: If the skill directory or SKILL.md does not exist.
    """
    skill_content, ref_listing = _load_skill_dir(cli_tool.skill_dir)

    # Build headed mode instruction
    headed_instruction = ""
    if not headless:
        headed_instruction = """
## Browser Display Mode

You MUST run the browser in **headed mode** (visible browser window). \
Ensure the browser launches with a visible UI, not headless.
"""

    return f"""\
You are a browser automation agent. Your task is to complete a specific \
objective by interacting with a web browser through the `{cli_tool.binary}` \
command-line tool.

## Tool Reference — {cli_tool.name}

{skill_content}

## Workflow

1. **Navigate**: Use `{cli_tool.binary}` to go to the target URL.
2. **Observe**: After navigation or any action, read the output / snapshot. \
The output typically contains an accessibility tree with element references \
(e.g. e1, e2, e15) that you use to target elements.
3. **Act**: Use the appropriate command (click, fill, select, press, etc.) \
with the element ref from the output.
4. **Verify**: Check the resulting output to confirm your action had the \
intended effect.
5. **Complete**: When the task is done, signal completion using the \
protocol below.

## Rules

- **Observe first**: Always read the page state before interacting with \
elements. Element refs change after page updates.
- **Use latest refs**: Only use element refs from the MOST RECENT output. \
Old refs may be stale and cause errors.
- **Scroll**: If content is not visible, try scrolling to reveal more.
- **No fabrication**: NEVER fabricate or guess information. Only report \
what you actually see on the page or extract via JavaScript.
- **Wait for load**: After navigation or form submission, the page may \
not immediately reflect the new state. If unchanged, try again.

## Error Handling

- If you encounter a CAPTCHA or login wall you cannot bypass, signal \
completion explaining the blocker.
- If a page fails to load, try reloading or navigating again.
- If you are stuck in a loop, try a completely different approach.
- If an element cannot be found, try scrolling or inspecting the DOM.
- After 3 consecutive failures with the same approach, switch strategy.

## CRITICAL CONSTRAINTS

1. **Tool isolation**: You may ONLY use `{cli_tool.binary}` commands \
and basic utilities (echo, cat, ls, pwd). Do NOT use any other browser \
automation tools, python, node, curl, wget, or other executables.
2. **Turn limit**: You have a maximum of 50 turns to complete the task. \
Work efficiently. Do NOT waste turns on screenshots — they are captured automatically.
{headed_instruction}

## Completion Protocol

When you have completed the task (or determined it is impossible), \
you MUST signal completion by running ONE of these echo commands:

**Task completed successfully:**
```bash
echo "TASK_COMPLETE: <your final answer or result here>"
```

If the task asks for specific information, put that information after \
TASK_COMPLETE. If the task asks you to perform an action, describe \
what you did and the outcome.

**Task is truly impossible:**
```bash
echo "TASK_IMPOSSIBLE: <reason why the task cannot be completed>"
```

Use TASK_IMPOSSIBLE only when the task fundamentally cannot be completed \
(e.g., website is down, requires authentication not provided, CAPTCHA \
blocks progress).

## Important Notes

- Elements are typically identified by ref IDs (e.g. e1, e2, e15). \
Use these refs in click, fill, select, and other element-targeting commands.
- The snapshot shows the accessibility tree, not the visual layout. \
Some visual elements may not appear.
- Use tab management commands for multi-tab workflows.
- Use keyboard shortcuts (Enter, Tab, Escape, etc.) when appropriate.
{ref_listing}"""


def _load_skill_dir(skill_dir: Path) -> tuple[str, str]:
    """Load main skill file and list available references/templates.

    Directory structure (following the standard skill layout)::

        skill_dir/
        ├── SKILL.md          # Main skill file (required, injected into prompt)
        ├── references/       # Additional reference docs (optional, listed for cat)
        │   ├── commands.md
        │   └── ...
        └── templates/        # Example scripts (optional, listed for cat)
            ├── example.sh
            └── ...

    Only SKILL.md is injected into the system prompt. Reference and template
    files are listed with their paths so the agent can read them on demand
    using ``cat <path>``.

    Returns:
        Tuple of (skill_content, ref_listing) where ref_listing is a
        formatted string section listing available files, or empty string
        if none exist.

    Raises:
        FileNotFoundError: If the skill directory or SKILL.md is missing.
    """
    if not skill_dir.exists():
        raise FileNotFoundError(
            f"Skill directory not found: {skill_dir}. "
            f"Create it with the CLI's skill files "
            f"(SKILL.md + references/ + templates/)."
        )

    # 1. Main skill file (required)
    main_file = skill_dir / "SKILL.md"
    if not main_file.exists():
        # Fallback: try any .md file at the root
        md_files = sorted(skill_dir.glob("*.md"))
        if not md_files:
            raise FileNotFoundError(
                f"No SKILL.md or .md files found in {skill_dir}."
            )
        main_file = md_files[0]

    skill_content = main_file.read_text(encoding="utf-8")

    # 2. Collect available reference and template files
    available_files: list[str] = []

    refs_dir = skill_dir / "references"
    if refs_dir.exists():
        for ref_file in sorted(refs_dir.glob("*.md")):
            # Use relative path from project root for cat
            available_files.append(str(ref_file))

    templates_dir = skill_dir / "templates"
    if templates_dir.exists():
        for tmpl_file in sorted(templates_dir.iterdir()):
            if tmpl_file.is_file():
                available_files.append(str(tmpl_file))

    # Build listing section
    ref_listing = ""
    if available_files:
        file_list = "\n".join(f"- `{f}`" for f in available_files)
        ref_listing = f"""

## Additional Reference Files

The following reference docs and templates are available. \
Use `cat <path>` to read any of them when you need more details:

{file_list}
"""

    return skill_content, ref_listing
