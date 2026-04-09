# Phantom Benchmark

Browser automation benchmark using CLI tools + Claude Code Agent SDK. Evaluates how well an LLM-driven agent can complete real-world web tasks using different browser automation CLI backends.

## Architecture

```
run_eval.py                          # Main entry point
    |
    v
AgentSDKExecutor (agent/agent.py)    # Drives Claude Code Agent SDK
    |
    v
claude_agent_sdk.query()             # Claude Code manages the agentic loop
    |
    v
Bash tool -> CLI commands             # Agent calls CLI via Bash
    |
    v
browser-use / agent-browser /        # CLI backends (one per benchmark run)
playwright-cli / patchright-cli
```

The executor uses Claude Code Agent SDK to run an agent that can only interact with the browser through the selected CLI tool. Each task:

1. Agent receives the task description via CLAUDE.md
2. Agent uses the CLI tool's commands (navigate, click, fill, snapshot, etc.) via Bash
3. PostToolUse hooks auto-capture screenshots after key actions
4. PreToolUse hooks enforce tool isolation (only the selected CLI is allowed)
5. A separate Claude judge evaluates the final result

### CLI Backends

| Backend | Install | Description |
|---------|---------|-------------|
| `browser-use` | `uv pip install browser-use` | AI-native browser automation by Browser Use |
| `agent-browser` | `npm install -g agent-browser` | Browser automation CLI for AI agents by Vercel Labs |
| `playwright-cli` | `npm install -g @playwright/cli@latest` | Official Playwright CLI by Microsoft |
| `patchright-cli` | `npm install -g patchright-cli` | Undetected browser automation based on Playwright |

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for npm-based CLI tools)
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **Claude Code CLI** installed (`npm install -g @anthropic-ai/claude-code`)
- **Anthropic API key** (or compatible proxy like Agent Maestro Desktop)

### 1. Install CLI backends

Install one or more CLI tools to benchmark:

```bash
# agent-browser (recommended to start with)
npm install -g agent-browser
agent-browser install   # downloads Chrome for Testing

# playwright-cli
npm install -g @playwright/cli@latest
playwright-cli --version

# patchright-cli
npm install -g patchright-cli
patchright-cli --version

# browser-use (Python-based)
uv pip install browser-use
browser-use install
browser-use doctor
```

### 2. Clone and install Python dependencies

```bash
git clone <repo-url>
cd benchmark
pip install uv    # if you don't have uv yet
uv sync
```

### 3. Configure API credentials

```bash
cp .env.example .env
```

Edit `.env` with one of these options:

**Option A: Direct Anthropic API key**
```env
ANTHROPIC_API_KEY=sk-ant-api03-...
```

**Option B: Proxy (e.g. Agent Maestro Desktop)**
```env
ANTHROPIC_BASE_URL=http://127.0.0.1:23337
ANTHROPIC_AUTH_TOKEN=Powered by Agent Maestro Desktop
```

### 4. Add skill files

Each CLI tool needs a skill directory under `skills/` with at least a `SKILL.md` file describing its commands. See `skills/agent-browser/` for an example of the expected structure:

```
skills/<tool-name>/
├── SKILL.md              # Main command reference (injected into CLAUDE.md)
├── references/           # Additional docs (agent reads via cat on demand)
└── templates/            # Example scripts (agent reads via cat on demand)
```

### 5. Run the benchmark

```bash
# Run 1 task to verify setup (headed mode to see the browser)
uv run python run_eval.py --cli agent-browser --tasks 1 --headed

# Run a specific task by ID
uv run python run_eval.py --cli agent-browser --task-id 2c93f863 --headed

# Run all 100 BU Bench tasks
uv run python run_eval.py --cli agent-browser

# Compare backends
uv run python run_eval.py --cli agent-browser
uv run python run_eval.py --cli playwright-cli
uv run python run_eval.py --cli patchright-cli
```

---

## Usage

### Command-line Options

```
uv run python run_eval.py [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--cli` | `playwright-cli` | CLI backend to test |
| `--model` | `sonnet` | Claude model: `haiku`, `sonnet`, `opus` |
| `--benchmark` | `bu-bench` | Benchmark: `bu-bench` (100 tasks) or `stealth-bench` (71 tasks) |
| `--tasks` | all | Number of tasks to run (e.g. `--tasks 5`) |
| `--task-id` | none | Run a specific task by ID (prefix match) |
| `--headed` | off | Show the browser window |
| `--max-turns` | 50 | Maximum agentic turns per task |
| `--max-budget-usd` | 5.0 | Maximum API cost per task in USD |
| `--anthropic-base-url` | from `.env` | Proxy URL for Agent Maestro Desktop |
| `--cli-path` | auto-detect | Path to Claude Code CLI binary |

### Examples

```bash
# Quick smoke test with agent-browser
uv run python run_eval.py --cli agent-browser --tasks 1 --headed

# Run specific task
uv run python run_eval.py --cli agent-browser --task-id 66c6641b --headed

# Run 10 tasks with Haiku (fastest, cheapest)
uv run python run_eval.py --cli agent-browser --tasks 10 --model haiku

# Full BU Bench with Opus
uv run python run_eval.py --cli playwright-cli --model opus

# Compare all backends on BU Bench
for cli in agent-browser playwright-cli patchright-cli browser-use; do
    uv run python run_eval.py --cli $cli
done
```

### Terminal Output

Each task logs detailed progress in real-time:

```
Starting evaluation: 1 tasks, cli=agent-browser, model=sonnet, benchmark=bu-bench, headless=False
Running task: 66c6641b-f949-46a2-8bcc-6d9dd388b534
[66c6641b] Task: Browse the list of active Q&A communities on https://stackexchange.com...
[66c6641b] CLI tool: agent-browser, model: sonnet
[66c6641b]   [System] init
[66c6641b]   Turn 1: tokens=30922+1, elapsed=15.6s
[66c6641b]   >> Bash: agent-browser open --headed https://stackexchange.com/sites
[66c6641b]   Turn 2: tokens=61944+2, elapsed=41.2s
[66c6641b]   >> Bash: agent-browser snapshot -i
...
[66c6641b]   Done: stop=end_turn, turns=18, cost=$1.590, elapsed=381.7s
[66c6641b] Running judge...
Task 66c6641b completed: score=1, verdict=True, captcha=False, impossible=False
Run complete: 1/1 tasks successful, 18 steps, 370.4s, $1.59, captcha=0, impossible=0
```

---

## Output

### Results

Aggregate results are saved to `results/<benchmark>/`:

```json
[
  {
    "run_start": "20260409_152255",
    "cli_tool": "agent-browser",
    "model": "sonnet",
    "tasks_completed": 100,
    "tasks_successful": 62,
    "tasks_captcha": 3,
    "tasks_impossible": 1,
    "total_steps": 1547,
    "total_duration": 28340.5,
    "total_cost": 185.23
  }
]
```

### Traces

Detailed per-task traces are saved to `run_data/<benchmark>/<run_key>/`:

Each trace JSON contains:
- `agent_trace`: task, final result, step-by-step actions with command output, ground truth, screenshots (base64)
- `metrics`: steps, duration, cost, token usage, captcha encountered, task impossible
- `judgement`: verdict, reasoning, failure reason, captcha detection, impossible task detection

---

## Benchmarks

### BU Bench V1

**100 hand-selected tasks** for evaluating browser automation agents.

| Source | Tasks | Description |
|--------|-------|-------------|
| Custom | 20 | Page interaction challenges |
| WebBench | 20 | Web browsing tasks |
| Mind2Web 2 | 20 | Multi-step web navigation |
| GAIA | 20 | General AI assistant tasks (web-based) |
| BrowseComp | 20 | Browser comprehension tasks |

### Stealth Bench V1

**71 tasks** for evaluating browser stealth across anti-bot protections.

### Task Encryption

Task sets are encrypted (Fernet + SHA256) to prevent data contamination in LLM training. The keys are derived from the benchmark names and embedded in the code.

---

## Tool Isolation

Each benchmark run enforces strict isolation to ensure fair comparison:

1. **CLAUDE.md instructions**: The agent's working directory contains a CLAUDE.md with the CLI's full skill reference and constraints
2. **`allowed_tools` patterns**: Only `Bash(<cli-binary>:*)` and basic utilities (echo, cat, ls, pwd) are permitted
3. **`disallowed_tools`**: Claude Code built-in tools (WebFetch, WebSearch, Agent, Read, etc.) are disabled
4. **PreToolUse hooks**: Hard enforcement — any Bash command not starting with the target CLI binary is denied

---

## Project Structure

```
phantom-benchmark/
  agent/                      # Agent package
    __init__.py               # Exports AgentSDKExecutor, ExecutorConfig, AgentResult
    agent.py                  # Agent SDK executor (drives Claude Code)
    cli_registry.py           # CLI tool definitions and config
    hooks.py                  # PreToolUse (isolation) + PostToolUse (tracking) hooks
    prompts.py                # System prompt / CLAUDE.md construction
    result.py                 # AgentResult dataclass
  skills/                     # Skill files for each CLI tool
    agent-browser/            # SKILL.md + references/ + templates/
    browser-use/
    patchright-cli/
    playwright-cli/
  judge.py                    # Judge message construction
  judge_llm.py                # Claude judge invocation
  run_eval.py                 # Main benchmark script
  run_batch.py                # Batch runner (for GitHub Actions)
  orchestrator.py             # Distributed run orchestrator
  generate_plots.py           # Plot generation from results
  BU_Bench_V1.enc             # Encrypted BU Bench tasks
  Stealth_Bench_V1.enc        # Encrypted Stealth Bench tasks
  pyproject.toml              # Python project config
```

---

## Distributed Runs (GitHub Actions)

For running large-scale evaluations across GitHub Actions runners:

```bash
# Configure orchestrator.py with desired models and run count
uv run python orchestrator.py
```

See `orchestrator.py` for configuration and `run_batch.py` for the per-batch runner.

---

## Troubleshooting

### CLI tool not found

Make sure it's installed globally and in your PATH:

```bash
# Verify installation
agent-browser --version
playwright-cli --version
patchright-cli --version
browser-use --version
```

### Claude Code CLI not found

The Agent SDK needs Claude Code CLI installed:

```bash
npm install -g @anthropic-ai/claude-code
claude --version
```

If installed but not found, use `--cli-path`:

```bash
uv run python run_eval.py --cli agent-browser --cli-path /path/to/claude
```

### `ANTHROPIC_API_KEY not set`

Create a `.env` file from the template:

```bash
cp .env.example .env
# Then edit .env with your API key or proxy URL
```

### Browser not showing in headed mode

The `--headed` flag sets environment variables and prompt instructions for the CLI tool. If the browser doesn't appear:
- Make sure any existing browser daemon is closed first (the benchmark does this automatically)
- Check that your CLI tool supports headed mode

### Task timed out

Default timeout is 30 minutes per task. The agent caps at 50 turns per task. Use `--max-turns` to adjust.

---

## Attributions

### WebBench
MIT License | https://webbench.ai/

### Mind2Web 2 (OMI2W-2)
MIT License | https://openreview.net/forum?id=AUaW6DS9si

### BrowseComp
MIT License | https://cdn.openai.com/pdf/5e10f4ab-d6f7-442e-9508-59515c65e35d/browsecomp.pdf

### GAIA
No license (public validation split only) | https://huggingface.co/datasets/gaia-benchmark/GAIA
