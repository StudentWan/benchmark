# Phantom Benchmark

Browser automation benchmark using CLI tools + Claude. Evaluates how well an LLM-driven agent can complete real-world web tasks using [playwright-cli](https://github.com/anthropics/anthropic-playwright-mcp) as the browser automation backend.

## Architecture

```
run_eval.py                    # Main entry point
    |
    v
CliAgent (agent/agent.py)     # Agentic loop: Claude decides -> CLI executes -> repeat
    |
    v
CliRunner (agent/runner.py)   # Abstract CLI interface
    |
    v
PlaywrightCliRunner            # playwright-cli subprocess wrapper
    |
    v
playwright-cli                 # Browser automation via snapshots + element refs
```

The agent uses Claude's [tool_use](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) API to decide which browser actions to take. Each iteration:

1. Claude receives the task + current page snapshot
2. Claude calls a tool (navigate, click, fill, snapshot, etc.)
3. Python executes the corresponding `playwright-cli` command
4. The result is fed back to Claude
5. Repeat until task is complete or iteration limit is reached

A separate Claude judge evaluates the final result against ground truth.

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for playwright-cli)
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **Anthropic API key** (or compatible proxy)

### 1. Install playwright-cli

```bash
npm install -g @playwright/cli
```

Verify it's installed:

```bash
playwright-cli --version
```

### 2. Clone and install Python dependencies

```bash
git clone <repo-url>
cd phantom-benchmark
pip install uv    # if you don't have uv yet
uv sync
```

### 3. Configure API credentials

```bash
cp .env.example .env
```

Edit `.env` with one of these options:

**Option A: Direct Anthropic API key (recommended)**
```env
ANTHROPIC_API_KEY=sk-ant-api03-...
```

**Option B: Proxy (e.g. Agent Maestro Desktop)**
```env
ANTHROPIC_BASE_URL=http://127.0.0.1:23337
ANTHROPIC_AUTH_TOKEN=Powered by Agent Maestro Desktop
```

### 4. Run the benchmark

```bash
# Run 1 task to verify setup (headed mode to see the browser)
uv run python run_eval.py --tasks 1 --headed

# Run all 100 BU Bench tasks
uv run python run_eval.py

# Run Stealth Bench (71 tasks)
uv run python run_eval.py --benchmark stealth-bench
```

---

## Usage

### Command-line Options

```
uv run python run_eval.py [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--benchmark` | `bu-bench` | Benchmark to run: `bu-bench` (100 tasks) or `stealth-bench` (71 tasks) |
| `--model` | `claude-sonnet-4.6` | Claude model: `claude-haiku-4.5`, `claude-sonnet-4.6`, `claude-opus-4.6` |
| `--tasks` | all | Number of tasks to run (e.g. `--tasks 5` for first 5) |
| `--headed` | off | Show the browser window (useful for debugging) |
| `--cli` | `playwright-cli` | CLI backend to use |

### Examples

```bash
# Quick smoke test
uv run python run_eval.py --tasks 1 --headed

# Run 10 tasks with Haiku (fastest, cheapest)
uv run python run_eval.py --tasks 10 --model claude-haiku-4.5

# Full BU Bench with Opus
uv run python run_eval.py --model claude-opus-4.6

# Full Stealth Bench
uv run python run_eval.py --benchmark stealth-bench

# Both benchmarks
uv run python run_eval.py --benchmark bu-bench && uv run python run_eval.py --benchmark stealth-bench
```

### Terminal Output

Each task logs detailed progress in real-time:

```
Running task: 66c6641b-...
[66c6641b-...] Starting browser session...
[66c6641b-...] Browser started
[66c6641b-...] Task: Browse the list of active Q&A communities on https://stackex...
[66c6641b-...] Iteration 1/50 - calling LLM...
[66c6641b-...]   LLM responded: stop=tool_use, tokens_in=2841, tokens_out=98, cost=$0.023, elapsed=5.2s
[66c6641b-...]   Step 1: Navigated to https://stackexchange.com
[66c6641b-...] Iteration 2/50 - calling LLM...
[66c6641b-...]   Step 2: Took page snapshot
...
[66c6641b-...] Done: 12 steps, 89.3s, $0.452, tokens=52000+3200
[66c6641b-...] Running judge...
Task 66c6641b-... completed: score=1, verdict=True
Run complete: 1/1 tasks successful, 12 steps, 89.3s, $0.45
```

---

## Output

### Results

Aggregate results are saved to `results/`:

```
results/PlaywrightCLI_1.59.0_model_claude-sonnet-4.6.json
```

```json
[
  {
    "run_start": "20260331_154630",
    "tasks_completed": 100,
    "tasks_successful": 62,
    "total_steps": 1547,
    "total_duration": 28340.5,
    "total_cost": 185.23
  }
]
```

### Traces

Detailed per-task traces are saved to `run_data/<run_key>/`:

```
run_data/PlaywrightCLI_1.59.0_model_claude-sonnet-4.6_start_at_20260331_154630/
  66c6641b-f949-46a2-8bcc-6d9dd388b534.json    # Trace + judgement
  66c6641b-f949-46a2-8bcc-6d9dd388b534/         # Screenshots
    screenshots/
      screenshot_0001.png
      screenshot_0002.png
      ...
```

Each trace JSON contains:
- `agent_trace`: task, final result, step-by-step actions, ground truth, screenshots (base64)
- `metrics`: steps, duration, cost
- `judgement`: verdict, reasoning, failure reason

### Plots

Generate comparison plots from results in `official_results/`:

```bash
uv run --group plots python generate_plots.py
```

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

Task sets are encrypted (Fernet + SHA256) to prevent data contamination in LLM training. The keys are derived from the benchmark names and embedded in the code. **Do not publish decrypted tasks in plaintext.**

To inspect the tasks:

```bash
uv run python -c "
from run_eval import load_tasks
tasks = load_tasks('bu-bench')
print(f'{len(tasks)} tasks loaded')
for t in tasks[:3]:
    print(f\"  {t['task_id']}: {t['confirmed_task'][:80]}...\")
"
```

---

## Distributed Runs (GitHub Actions)

For running large-scale evaluations across GitHub Actions runners:

```bash
# Configure orchestrator.py with desired models and run count
uv run python orchestrator.py
```

This dispatches batches of 10 tasks to parallel runners and aggregates results into `official_results/`.

See `orchestrator.py` for configuration (models, batch size, concurrency) and `run_batch.py` for the per-batch runner.

---

## Project Structure

```
phantom-benchmark/
  agent/                    # Agent package
    __init__.py             # Exports CliAgent, AgentResult
    agent.py                # Agentic loop (Claude tool_use)
    cost.py                 # Token cost calculation
    prompts.py              # System prompt for the agent
    result.py               # AgentResult dataclass
    runner.py               # CliRunner ABC + PlaywrightCliRunner
    tools.py                # Tool definitions + executor
  judge.py                  # Judge message construction
  judge_llm.py              # Claude judge invocation
  run_eval.py               # Main benchmark script
  run_batch.py              # Batch runner (for GitHub Actions)
  orchestrator.py           # Distributed run orchestrator
  generate_plots.py         # Plot generation from results
  BU_Bench_V1.enc           # Encrypted BU Bench tasks
  Stealth_Bench_V1.enc      # Encrypted Stealth Bench tasks
  pyproject.toml            # Python project config
  .env.example              # Environment variable template
```

---

## Cost Estimates

Approximate costs per full benchmark run (100 BU Bench tasks):

| Model | Est. Cost | Est. Duration |
|-------|-----------|---------------|
| claude-haiku-4.5 | ~$30-60 | ~2-4 hours |
| claude-sonnet-4.6 | ~$100-200 | ~3-6 hours |
| claude-opus-4.6 | ~$150-300 | ~4-8 hours |

Costs depend on task complexity and number of iterations per task. Use `--tasks N` to do a smaller test run first.

---

## Troubleshooting

### `playwright-cli not found`

Make sure it's installed globally and in your PATH:

```bash
npm install -g @playwright/cli
playwright-cli --version
```

### `ANTHROPIC_API_KEY not set`

Create a `.env` file from the template:

```bash
cp .env.example .env
# Then edit .env with your API key
```

### Browser not showing in headed mode

Make sure you're using `--headed` (not `--head`):

```bash
uv run python run_eval.py --tasks 1 --headed
```

### API 502 / Connection errors

The agent retries API calls automatically (3 attempts with exponential backoff). If errors persist, check:
- Your API key is valid
- Your proxy is running (if using one)
- You have internet connectivity

### Task timed out

Default timeout is 30 minutes per task. Some complex tasks may hit this limit. The agent caps at 50 iterations per task.

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
